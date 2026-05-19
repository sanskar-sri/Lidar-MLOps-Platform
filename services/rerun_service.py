"""
services/rerun_service.py

Production Rerun pipeline for Dash Data Explorer.

Strict rules:
- No mock point cloud.
- No generated fake labels.
- No estimated semantic distribution.
- Every visualized point comes from the real uploaded PLY tile.
- Every semantic/binary color comes from real PLY semantic labels joined with the uploaded label map.

Supported real color modes:
- solid            -> positions only, fastest for 4.5M-5M point navigation
- rgb               -> PLY red/green/blue
- height            -> PLY z coordinate
- intensity         -> PLY scalar_Intensity / intensity / reflectance
- semantic_label    -> PLY scalar_Label/class/label + XML/JSON/YAML label map
- binary_label      -> PLY scalar_Label/class/label + XML/JSON/YAML building mapping

The CloudCompare-like tabs and viewer layout are handled by:
    services/rerun_viewer.py
"""

from __future__ import annotations

import os
import json
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
from plyfile import PlyData

try:
    import yaml
except Exception:
    yaml = None

from services.b2_service import download_b2_file_to_local

from services.rerun_viewer import (
    init_recording,
    log_real_tile_modes,
    finalize_blueprint,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

LOCAL_RERUN_DOWNLOAD_DIR = PROJECT_ROOT / "data" / "local_staging" / "rerun_downloads"
RERUN_OUTPUT_DIR = PROJECT_ROOT / "data" / "rerun_outputs"
DEFAULT_POINT_BUDGET = 50_000
MAX_POINT_BUDGET = 5_000_000
RECORDING_PROFILE = "single_layer_local_coords_chunked_v3"

LOCAL_RERUN_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
RERUN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# Public entry point called by pages/data_explorer.py
# ---------------------------------------------------------------------

def generate_rerun_preview(
    dataset_id: str,
    tile_items: list[dict[str, Any]],
    label_map_items: list[dict[str, Any]] | None = None,
    point_budget: int = DEFAULT_POINT_BUDGET,
    color_mode: str = "solid",
    view_mode: str = "raw",
    open_viewer: bool = False,
) -> dict[str, Any]:
    """
    Generate a real Rerun .rrd preview from B2 point-cloud tiles.

    Parameters
    ----------
    dataset_id:
        Dataset ID selected in Dash.

    tile_items:
        Example:
        [
            {
                "name": "L002.ply",
                "b2_key": "bronze_raw_data/id-2/source_files/tiles/L002.ply"
            }
        ]

    label_map_items:
        Example:
        [
            {
                "name": "torronot_coarse_updated.xml",
                "b2_key": "bronze_raw_data/id-2/source_files/label_maps/torronot_coarse_updated.xml"
            }
        ]

    point_budget:
        Maximum real points sampled per tile.

    color_mode:
        solid, rgb, height, intensity, semantic_label, binary_label

    view_mode:
        raw, semantic, binary, z_slice

    open_viewer:
        True opens the native Rerun Viewer and saves .rrd.
        False saves .rrd only.

    Returns
    -------
    dict with rrd_path, tile_summaries, total_logged_points, etc.
    """

    if not dataset_id:
        raise ValueError("dataset_id is required.")

    if not tile_items:
        raise ValueError("No tile selected for Rerun preview.")

    if len(tile_items) > 1:
        raise ValueError(
            "Large Rerun visualization is optimized for one tile at a time. "
            "Please select only one tile."
        )

    point_budget = int(point_budget or DEFAULT_POINT_BUDGET)

    if point_budget <= 0:
        raise ValueError("point_budget must be greater than 0.")

    if point_budget > MAX_POINT_BUDGET:
        raise ValueError(
            f"point_budget cannot exceed {MAX_POINT_BUDGET:,} points per tile."
        )

    color_mode = str(color_mode or "solid").strip().lower()
    view_mode = str(view_mode or "raw").strip().lower()

    valid_color_modes = {
        "solid",
        "rgb",
        "height",
        "intensity",
        "high_contrast",
        "semantic_label",
        "binary_label",
    }

    valid_view_modes = {
        "raw",
        "semantic",
        "binary",
        "z_slice",
    }

    if color_mode not in valid_color_modes:
        raise ValueError(
            f"Unsupported color_mode={color_mode}. "
            f"Valid values: {sorted(valid_color_modes)}"
        )

    if view_mode not in valid_view_modes:
        raise ValueError(
            f"Unsupported view_mode={view_mode}. "
            f"Valid values: {sorted(valid_view_modes)}"
        )

    requires_label_map = (
        color_mode in {"semantic_label", "binary_label"}
        or view_mode in {"semantic", "binary"}
    )

    label_map = {}
    local_label_map_path = ""

    if label_map_items:
        first_label_map = label_map_items[0]
        local_label_map_path = ensure_b2_file_local(
            b2_key=first_label_map["b2_key"],
            dataset_id=dataset_id,
            subfolder="label_maps",
        )

        label_map = load_label_map(local_label_map_path)

    if requires_label_map and not label_map:
        raise ValueError(
            "Semantic Label or Building vs Non-building mode requires a real XML/JSON/YAML label map."
        )

    run_id = uuid.uuid4().hex[:10]
    rrd_path = RERUN_OUTPUT_DIR / f"{dataset_id}_{run_id}.rrd"

    init_recording(
        rrd_path=str(rrd_path),
        open_viewer=open_viewer,
    )

    total_logged_points = 0
    tile_summaries: list[dict[str, Any]] = []

    global_min = np.full(3, np.inf, dtype=np.float32)
    global_max = np.full(3, -np.inf, dtype=np.float32)

    available_modes: set[str] = set()

    for tile_item in tile_items:
        tile_name = tile_item.get("name") or os.path.basename(tile_item["b2_key"])
        tile_b2_key = tile_item.get("b2_key")

        if not tile_b2_key:
            raise ValueError(f"Missing b2_key for selected tile: {tile_item}")

        selected_rerun_mode = get_selected_rerun_mode(
            color_mode=color_mode,
            view_mode=view_mode,
        )

        local_tile_path = ensure_b2_file_local(
            b2_key=tile_b2_key,
            dataset_id=dataset_id,
            subfolder="tiles",
        )

        try:
            fields = read_real_tile_fields(
                local_tile_path,
                include_rgb=selected_rerun_mode == "rgb",
                include_intensity=selected_rerun_mode in {"intensity", "high_contrast"},
                include_semantic=selected_rerun_mode in {"semantic_label", "binary_label"},
            )
        except Exception as _read_exc:
            _evict_if_corrupted(local_tile_path, _read_exc)

        validate_requested_mode(
            fields=fields,
            label_map=label_map,
            color_mode=color_mode,
            view_mode=view_mode,
            tile_name=tile_name,
        )

        xyz_full = fields["xyz"]

        xyz, sample_idx = subsample_real_points(
            xyz=xyz_full,
            point_budget=point_budget,
        )

        rgb_sample = None
        intensity_sample = None
        semantic_ids_sample = None
        semantic_colors = None
        semantic_labels = None
        binary_colors = None
        binary_labels = None

        if selected_rerun_mode == "rgb" and fields["rgb"] is not None:
            rgb_sample = sample_array(fields["rgb"], sample_idx)

        if (
            selected_rerun_mode in {"intensity", "high_contrast"}
            and fields["intensity"] is not None
        ):
            intensity_sample = sample_array(fields["intensity"], sample_idx)

        if (
            selected_rerun_mode in {"semantic_label", "binary_label"}
            and fields["semantic_label"] is not None
        ):
            semantic_ids_sample = sample_array(fields["semantic_label"], sample_idx)

            if label_map:
                if selected_rerun_mode == "semantic_label":
                    semantic_colors, semantic_labels = semantic_colors_and_labels(
                        class_ids=semantic_ids_sample,
                        label_map=label_map,
                    )

                if selected_rerun_mode == "binary_label":
                    binary_colors, binary_labels = binary_colors_and_labels(
                        class_ids=semantic_ids_sample,
                        label_map=label_map,
                    )

        source_columns = {
            "xyz": "x,y,z",
            "rgb": fields.get("rgb_columns", []),
            "intensity": fields.get("intensity_column", ""),
            "semantic_label": fields.get("semantic_label_column", ""),
        }

        visual_origin = compute_visual_origin(xyz)
        xyz = to_local_visual_xyz(xyz, visual_origin)

        logged_modes = log_real_tile_modes(
            tile_name=tile_name,
            xyz=xyz,
            rgb=rgb_sample,
            intensity=intensity_sample,
            semantic_class_ids=semantic_ids_sample,
            semantic_colors=semantic_colors,
            semantic_labels=semantic_labels,
            binary_colors=binary_colors,
            binary_labels=binary_labels,
            n_orig=len(xyz_full),
            source_columns=source_columns,
            include_modes={selected_rerun_mode},
            visual_origin=visual_origin,
        )

        available_modes.update(logged_modes.keys())

        global_min = np.minimum(global_min, xyz.min(axis=0))
        global_max = np.maximum(global_max, xyz.max(axis=0))

        total_logged_points += int(len(xyz))

        tile_summaries.append(
            {
                "tile_name": tile_name,
                "b2_key": tile_b2_key,
                "local_tile_path": str(local_tile_path),
                "original_points": int(len(xyz_full)),
                "logged_points": int(len(xyz)),
                "color_source": describe_selected_color_source(
                    fields=fields,
                    color_mode=color_mode,
                    view_mode=view_mode,
                    label_map_loaded=bool(label_map),
                ),
                "logged_modes": sorted(list(logged_modes.keys())),
                "detected_columns": source_columns,
                "visual_origin": [float(v) for v in visual_origin],
            }
        )

    if not np.isfinite(global_min).all() or not np.isfinite(global_max).all():
        raise RuntimeError("Could not compute global scene bounds for Rerun blueprint.")

    finalize_blueprint(
        xyz_min=global_min,
        xyz_max=global_max,
        available_modes=available_modes,
    )

    return {
        "status": "success",
        "dataset_id": dataset_id,
        "rrd_path": str(rrd_path),
        "tiles_loaded": len(tile_summaries),
        "total_logged_points": int(total_logged_points),
        "color_mode": color_mode,
        "view_mode": view_mode,
        "point_budget": int(point_budget),
        "label_map_path": str(local_label_map_path) if local_label_map_path else "",
        "available_modes": sorted(list(available_modes)),
        "tile_summaries": tile_summaries,
    }


def get_selected_rerun_mode(color_mode: str, view_mode: str) -> str:
    """
    Map the dashboard selection to one Rerun point-cloud layer.

    Keeping recordings to one active point layer avoids duplicating the same
    positions across RGB/height/intensity/semantic tabs, which makes hover and
    camera movement noticeably smoother for large point clouds.
    """

    view_mode = str(view_mode or "raw").strip().lower()
    color_mode = str(color_mode or "solid").strip().lower()

    if view_mode == "semantic":
        return "semantic_label"

    if view_mode == "binary":
        return "binary_label"

    return color_mode


def compute_visual_origin(xyz: np.ndarray) -> np.ndarray:
    """
    Use a local origin for Rerun display coordinates.

    Large geospatial coordinates lose precision in realtime 3D navigation. Shifting
    one tile close to (0, 0, 0) removes most hover/camera jitter without changing
    the real shape of the point cloud.
    """

    xyz = np.asarray(xyz, dtype=np.float32)
    xyz_min = xyz.min(axis=0)
    xyz_max = xyz.max(axis=0)
    return ((xyz_min + xyz_max) / 2.0).astype(np.float32)


def to_local_visual_xyz(xyz: np.ndarray, visual_origin: np.ndarray) -> np.ndarray:
    # Explicit copy: when point_budget >= n_points subsample_real_points returns
    # the original array unchanged (copy=False), so in-place -= would silently
    # modify xyz_full and corrupt any subsequent use of the raw coordinates.
    xyz = np.asarray(xyz, dtype=np.float32).copy()
    xyz -= np.asarray(visual_origin, dtype=np.float32)
    return xyz


# ---------------------------------------------------------------------
# B2 local cache
# ---------------------------------------------------------------------

def ensure_b2_file_local(b2_key: str, dataset_id: str, subfolder: str) -> Path:
    """
    Download one B2 object to local rerun_downloads cache if needed.

    This uses actual B2 data. It does not create synthetic input files.
    """

    if not b2_key:
        raise ValueError("b2_key is required.")

    safe_name = b2_key.replace("/", "__")
    local_path = LOCAL_RERUN_DOWNLOAD_DIR / dataset_id / subfolder / safe_name
    local_path.parent.mkdir(parents=True, exist_ok=True)

    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path

    try:
        download_b2_file_to_local(
            b2_key=b2_key,
            local_path=str(local_path),
        )
    except Exception as exc:
        err_lower = str(exc).lower()
        if "file not present" in err_lower or "not present" in err_lower or type(exc).__name__ == "FileNotPresent":
            raise RuntimeError(
                f"The file '{b2_key}' does not exist in the B2 bucket.\n\n"
                f"This dataset's source files have not been uploaded to B2 storage. "
                f"Only datasets whose PLY/LAS/LAZ tiles have been successfully uploaded "
                f"can be visualized in Rerun.\n\n"
                f"Please upload this dataset's tiles first, then try again."
            ) from exc
        raise

    if not local_path.exists():
        raise RuntimeError(f"B2 download failed. Local file not created: {local_path}")

    if local_path.stat().st_size == 0:
        raise RuntimeError(f"B2 download failed. Local file is empty: {local_path}")

    return local_path


# ---------------------------------------------------------------------
# PLY reading
# ---------------------------------------------------------------------

def read_real_ply_fields(
    local_path: str | Path,
    include_rgb: bool = False,
    include_intensity: bool = False,
    include_semantic: bool = False,
) -> dict[str, Any]:
    """
    Read real fields from a PLY tile.

    Required:
        x, y, z

    Optional real fields:
        RGB:
            red/green/blue

        Intensity:
            scalar_Intensity / intensity / reflectance / scalar_Reflectance

        Semantic label:
            scalar_Label / label / class / classification / semantic_label
    """

    local_path = Path(local_path)

    if not local_path.exists():
        raise FileNotFoundError(local_path)

    ply = PlyData.read(str(local_path))

    vertex_names = [element.name for element in ply.elements]

    if "vertex" not in vertex_names:
        raise ValueError(f"PLY has no vertex element: {local_path}")

    vertex = ply["vertex"].data
    field_names = list(vertex.dtype.names or [])

    if not field_names:
        raise ValueError(f"PLY has no vertex fields: {local_path}")

    x_col = find_field(field_names, ["x"])
    y_col = find_field(field_names, ["y"])
    z_col = find_field(field_names, ["z"])

    if not x_col or not y_col or not z_col:
        raise ValueError(
            f"PLY missing x/y/z fields. Found fields: {field_names}"
        )

    xyz = np.column_stack(
        [
            np.asarray(vertex[x_col], dtype=np.float32),
            np.asarray(vertex[y_col], dtype=np.float32),
            np.asarray(vertex[z_col], dtype=np.float32),
        ]
    )

    r_col = find_field(field_names, ["red", "r", "diffuse_red"])
    g_col = find_field(field_names, ["green", "g", "diffuse_green"])
    b_col = find_field(field_names, ["blue", "b", "diffuse_blue"])

    rgb = None

    if include_rgb and r_col and g_col and b_col:
        rgb = np.column_stack(
            [
                np.asarray(vertex[r_col]),
                np.asarray(vertex[g_col]),
                np.asarray(vertex[b_col]),
            ]
        )
        rgb = normalize_rgb(rgb)

    intensity_col = find_intensity_field(field_names)
    intensity = None

    if include_intensity and intensity_col:
        intensity = np.asarray(vertex[intensity_col], dtype=np.float32)

    semantic_col = find_semantic_label_field(field_names)
    semantic_label = None

    if include_semantic and semantic_col:
        semantic_label = np.asarray(vertex[semantic_col]).astype(np.int64)

    return {
        "xyz": xyz,
        "rgb": rgb,
        "intensity": intensity,
        "semantic_label": semantic_label,
        "field_names": field_names,
        "xyz_columns": [x_col, y_col, z_col],
        "rgb_columns": [r_col, g_col, b_col] if r_col and g_col and b_col else [],
        "intensity_column": intensity_col or "",
        "semantic_label_column": semantic_col or "",
    }


def _evict_if_corrupted(local_path: Path, exc: Exception) -> None:
    """
    If exc looks like a truncated/corrupted file error, delete the local cache
    and raise a user-friendly message telling the user to click again.

    A partial B2 download leaves a file that passes the size > 0 check but
    fails mid-read (e.g. plyfile 'early end-of-file'). Evicting the cache lets
    the next launch re-download the tile from B2.
    """

    _TRUNCATION_KEYWORDS = [
        "end-of-file", "early end", "truncat",
        "unexpected eof", "unexpected end", "corrupt",
    ]

    err_lower = str(exc).lower()

    if any(kw in err_lower for kw in _TRUNCATION_KEYWORDS):
        try:
            local_path.unlink(missing_ok=True)
        except Exception:
            pass

        raise RuntimeError(
            f"The cached tile file '{local_path.name}' was incomplete or corrupted "
            f"(the previous B2 download was truncated). "
            f"The bad cache file has been deleted automatically. "
            f"Please click 'Stream from B2' or 'Open in Rerun Viewer' again — "
            f"the tile will be re-downloaded fresh from B2.\n\n"
            f"Technical detail: {exc}"
        ) from exc

    raise exc


def read_real_las_fields(
    local_path: str | Path,
    include_rgb: bool = False,
    include_intensity: bool = False,
    include_semantic: bool = False,
) -> dict[str, Any]:
    """
    Read real fields from a LAS or LAZ tile using laspy.
    Returns the same structure as read_real_ply_fields.
    """

    try:
        import laspy
    except ImportError as exc:
        raise ImportError(
            "laspy is required to read LAS/LAZ tiles. "
            "Install it with: pip install laspy[laszip]"
        ) from exc

    local_path = Path(local_path)

    if not local_path.exists():
        raise FileNotFoundError(local_path)

    las = laspy.read(str(local_path))

    x = np.asarray(las.x, dtype=np.float32)
    y = np.asarray(las.y, dtype=np.float32)
    z = np.asarray(las.z, dtype=np.float32)
    xyz = np.column_stack([x, y, z])

    point_format = las.point_format
    dim_names = [d.name for d in point_format.dimensions]

    rgb = None
    rgb_columns: list[str] = []

    if include_rgb and hasattr(las, "red") and hasattr(las, "green") and hasattr(las, "blue"):
        r = np.asarray(las.red, dtype=np.float32)
        g = np.asarray(las.green, dtype=np.float32)
        b = np.asarray(las.blue, dtype=np.float32)
        # LAS RGB is uint16 (0–65535) — scale to 0–255
        scale = 255.0 / 65535.0 if r.max() > 255 else 1.0
        rgb = np.clip(np.column_stack([r, g, b]) * scale, 0, 255).astype(np.uint8)
        rgb_columns = ["red", "green", "blue"]

    intensity = None
    intensity_col = ""

    if include_intensity and hasattr(las, "intensity"):
        intensity = np.asarray(las.intensity, dtype=np.float32)
        intensity_col = "intensity"

    semantic_label = None
    semantic_col = ""

    if include_semantic:
        for candidate in ("classification", "scalar_Label", "label", "class"):
            if hasattr(las, candidate):
                semantic_label = np.asarray(getattr(las, candidate), dtype=np.int64)
                semantic_col = candidate
                break

    return {
        "xyz": xyz,
        "rgb": rgb,
        "intensity": intensity,
        "semantic_label": semantic_label,
        "field_names": dim_names,
        "xyz_columns": ["x", "y", "z"],
        "rgb_columns": rgb_columns,
        "intensity_column": intensity_col,
        "semantic_label_column": semantic_col,
    }


def read_real_tile_fields(
    local_path: str | Path,
    include_rgb: bool = False,
    include_intensity: bool = False,
    include_semantic: bool = False,
) -> dict[str, Any]:
    """Format-agnostic dispatcher: routes PLY, LAS, or LAZ to the correct reader."""

    ext = Path(local_path).suffix.lower()

    if ext == ".ply":
        return read_real_ply_fields(
            local_path,
            include_rgb=include_rgb,
            include_intensity=include_intensity,
            include_semantic=include_semantic,
        )

    if ext in {".las", ".laz"}:
        return read_real_las_fields(
            local_path,
            include_rgb=include_rgb,
            include_intensity=include_intensity,
            include_semantic=include_semantic,
        )

    raise ValueError(
        f"Unsupported point cloud format: {ext}. Supported: .ply, .las, .laz"
    )


def normalize_field_name(name: str) -> str:
    import re

    text = str(name).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def find_field(field_names: list[str], candidates: list[str]) -> str | None:
    norm_to_original = {
        normalize_field_name(name): name
        for name in field_names
    }

    for candidate in candidates:
        key = normalize_field_name(candidate)

        if key in norm_to_original:
            return norm_to_original[key]

    return None


def find_semantic_label_field(field_names: list[str]) -> str | None:
    candidates = [
        "label",
        "labels",
        "class",
        "classes",
        "classification",
        "semantic",
        "semantic_label",
        "semanticlabel",
        "category",
        "category_id",
        "object_class",
        "class_id",
        "classid",
        "scalar_label",
        "scalar_labels",
        "scalar_class",
        "scalar_classes",
        "scalar_classification",
        "scalar_semantic",
        "scalar_semantic_label",
        "scalar_label_id",
    ]

    found = find_field(field_names, candidates)

    if found:
        return found

    for name in field_names:
        low = normalize_field_name(name)

        if low.startswith("scalar_") and any(
            token in low
            for token in ["label", "class", "classification", "semantic"]
        ):
            return name

    return None


def find_intensity_field(field_names: list[str]) -> str | None:
    candidates = [
        "intensity",
        "reflectance",
        "remission",
        "reflectivity",
        "amplitude",
        "scalar_intensity",
        "scalar_reflectance",
        "scalar_remission",
        "scalar_reflectivity",
        "scalar_amplitude",
    ]

    found = find_field(field_names, candidates)

    if found:
        return found

    for name in field_names:
        low = normalize_field_name(name)

        if low.startswith("scalar_") and any(
            token in low
            for token in ["intensity", "reflectance", "remission", "amplitude"]
        ):
            return name

    return None


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def validate_requested_mode(
    fields: dict[str, Any],
    label_map: dict[str, dict[str, Any]],
    color_mode: str,
    view_mode: str,
    tile_name: str,
) -> None:
    """
    Fail early if the selected UI mode requires a real field that does not exist.
    """

    if color_mode == "rgb" and fields["rgb"] is None:
        raise ValueError(
            f"RGB mode selected, but tile {tile_name} has no real RGB fields."
        )

    if color_mode in {"intensity", "high_contrast"} and fields["intensity"] is None:
        raise ValueError(
            f"Intensity / Reflectance mode selected, but tile {tile_name} "
            f"has no real intensity/reflectance field."
        )

    requires_semantic = (
        color_mode in {"semantic_label", "binary_label"}
        or view_mode in {"semantic", "binary"}
    )

    if requires_semantic and fields["semantic_label"] is None:
        raise ValueError(
            f"Semantic mode selected, but tile {tile_name} has no real semantic-label field."
        )

    if requires_semantic and not label_map:
        raise ValueError(
            f"Semantic mode selected, but no valid label map was loaded for tile {tile_name}."
        )


# ---------------------------------------------------------------------
# Subsampling
# ---------------------------------------------------------------------

def subsample_real_points(
    xyz: np.ndarray,
    point_budget: int,
) -> tuple[np.ndarray, np.ndarray | None]:
    """
    Subsample real points from the original point cloud.

    This is not mock generation. It selects real point indices from the original tile.
    """

    xyz = np.asarray(xyz, dtype=np.float32)

    if xyz.size == 0:
        raise ValueError("Cannot subsample empty xyz array.")

    if point_budget <= 0 or len(xyz) <= point_budget:
        return xyz.astype(np.float32, copy=False), None

    idx = np.sort(
        np.random.choice(len(xyz), size=int(point_budget), replace=False)
    ).astype(np.int64)

    return xyz[idx].astype(np.float32), idx


def sample_array(values: np.ndarray, sample_idx: np.ndarray | None) -> np.ndarray:
    """
    Apply the same point subsample to an optional attribute array.

    When all points are used, return the original array instead of creating a
    massive duplicate copy.
    """

    if sample_idx is None:
        return values

    return values[sample_idx]


# ---------------------------------------------------------------------
# Label map parsing
# ---------------------------------------------------------------------

def load_label_map(local_path: str | Path) -> dict[str, dict[str, Any]]:
    """
    Load real label map from XML/JSON/YAML.

    Supports:
    - Paris-Lille class/coarse XML
    - Toronto XML with label val/name
    - JSON/YAML class maps
    """

    local_path = Path(local_path)

    if not local_path.exists():
        raise FileNotFoundError(local_path)

    ext = local_path.suffix.lower()

    if ext == ".xml":
        return load_xml_label_map(local_path)

    if ext == ".json":
        with open(local_path, "r", encoding="utf-8") as f:
            return normalize_label_map_payload(json.load(f))

    if ext in {".yaml", ".yml"}:
        if yaml is None:
            raise ImportError("pyyaml is required for YAML label maps.")
        with open(local_path, "r", encoding="utf-8") as f:
            return normalize_label_map_payload(yaml.safe_load(f))

    raise ValueError(f"Unsupported label map format: {ext}")


def load_xml_label_map(local_path: str | Path) -> dict[str, dict[str, Any]]:
    """
    Parse XML class mapping.

    Supported examples:

    Paris-Lille:
        <class id="203000000" en="building" coarse="2" coarse_name="building" />

    Toronto:
        <label val="4" name="Building" />

    Optional colors:
        <step r="0" g="0" b="255" pos="0.5" />
        or r/g/b attributes on class/label rows.
    """

    tree = ET.parse(local_path)
    root = tree.getroot()

    color_steps: list[dict[str, Any]] = []

    for elem in root.iter():
        tag = strip_ns(elem.tag).lower()
        attrs = {strip_ns(k).lower(): v for k, v in elem.attrib.items()}

        if tag == "step" and {"r", "g", "b"}.issubset(attrs.keys()):
            try:
                color_steps.append(
                    {
                        "pos": float(attrs.get("pos", 0.0)),
                        "color": [
                            int(float(attrs["r"])),
                            int(float(attrs["g"])),
                            int(float(attrs["b"])),
                        ],
                    }
                )
            except Exception:
                pass

    color_steps = sorted(color_steps, key=lambda item: item["pos"])

    rows: dict[str, dict[str, Any]] = {}

    for elem in root.iter():
        tag = strip_ns(elem.tag).lower()
        attrs = {strip_ns(k).lower(): v for k, v in elem.attrib.items()}

        if tag not in {"class", "label"}:
            continue

        class_id = (
            attrs.get("id")
            or attrs.get("val")
            or attrs.get("value")
            or attrs.get("class")
            or attrs.get("label")
        )

        class_name = (
            attrs.get("en")
            or attrs.get("name")
            or attrs.get("label_name")
            or attrs.get("description")
            or attrs.get("desc")
        )

        if class_id is None or class_name is None:
            continue

        cid = normalize_class_id(class_id)

        coarse_id = attrs.get("coarse") or attrs.get("coarse_id") or ""
        coarse_name = (
            attrs.get("coarse_name")
            or attrs.get("coarse_class")
            or attrs.get("category")
            or infer_coarse_class_from_name(class_name)
        )

        color = parse_color_from_attrs(attrs)

        rows[cid] = {
            "class_id": cid,
            "class_name": str(class_name),
            "coarse_id": normalize_class_id(coarse_id) if coarse_id != "" else "",
            "coarse_class_name": str(coarse_name),
            "is_building": infer_is_building(
                class_name=class_name,
                coarse_name=coarse_name,
            ),
            "color": color,
        }

    # Attach CloudCompare color-scale colors if color steps exist.
    # This does not create semantic labels; it only assigns display colors to real mapped classes.
    if color_steps and rows:
        numeric_ids = []

        for key in rows.keys():
            try:
                numeric_ids.append(int(key))
            except Exception:
                pass

        if numeric_ids:
            min_id = min(numeric_ids)
            max_id = max(numeric_ids)
            denom = max(max_id - min_id, 1)

            for cid in numeric_ids:
                key = str(cid)

                if rows[key].get("color") is not None:
                    continue

                pos = (cid - min_id) / denom
                nearest = min(
                    color_steps,
                    key=lambda item: abs(item["pos"] - pos),
                )
                rows[key]["color"] = nearest["color"]

    return rows


def normalize_label_map_payload(payload: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    if isinstance(payload, dict):
        if "classes" in payload and isinstance(payload["classes"], list):
            payload = payload["classes"]
        elif "labels" in payload and isinstance(payload["labels"], list):
            payload = payload["labels"]
        else:
            payload = [
                {"class_id": key, **value}
                if isinstance(value, dict)
                else {"class_id": key, "class_name": value}
                for key, value in payload.items()
            ]

    if not isinstance(payload, list):
        return rows

    for item in payload:
        if not isinstance(item, dict):
            continue

        class_id = (
            item.get("class_id")
            or item.get("id")
            or item.get("val")
            or item.get("value")
        )

        class_name = (
            item.get("class_name")
            or item.get("name")
            or item.get("en")
            or item.get("label")
        )

        if class_id is None or class_name is None:
            continue

        cid = normalize_class_id(class_id)

        coarse_name = (
            item.get("coarse_class_name")
            or item.get("coarse_name")
            or infer_coarse_class_from_name(class_name)
        )

        color = (
            item.get("color")
            or item.get("rgb")
            or item.get("color_rgb")
        )

        rows[cid] = {
            "class_id": cid,
            "class_name": str(class_name),
            "coarse_id": normalize_class_id(
                item.get("coarse_id")
                or item.get("coarse")
                or ""
            ),
            "coarse_class_name": str(coarse_name),
            "is_building": infer_is_building(
                class_name=class_name,
                coarse_name=coarse_name,
            ),
            "color": parse_color_value(color),
        }

    return rows


# ---------------------------------------------------------------------
# Semantic/binary colors and labels
# ---------------------------------------------------------------------

def lookup_label_info(
    class_id: int,
    label_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    key = normalize_class_id(class_id)

    if key in label_map:
        return label_map[key]

    for item in label_map.values():
        if normalize_class_id(item.get("coarse_id", "")) == key:
            return item

    return None


def semantic_colors_and_labels(
    class_ids: np.ndarray,
    label_map: dict[str, dict[str, Any]],
) -> tuple[np.ndarray, None]:
    """
    Build semantic visualization colors from real class IDs and real label map.

    If XML does not provide a color for a mapped class, a deterministic display
    color is assigned from the real class ID. This does not create fake labels
    or fake points. Per-point text labels are intentionally omitted because they
    make multi-million-point Rerun recordings sluggish.
    """

    class_ids = np.asarray(class_ids).astype(np.int64)

    unique_ids = sorted(int(x) for x in np.unique(class_ids))
    missing_ids = [
        cid
        for cid in unique_ids
        if lookup_label_info(cid, label_map) is None
    ]

    if missing_ids:
        raise ValueError(
            "Semantic labels found in PLY but missing in label map: "
            f"{missing_ids[:20]}"
        )

    colors = np.zeros((len(class_ids), 3), dtype=np.uint8)

    for cid in unique_ids:
        info = lookup_label_info(cid, label_map)
        if info is None:
            raise ValueError(f"Semantic label missing in label map: {cid}")

        color = info.get("color")

        if color is None:
            color = deterministic_color_from_class_id(int(cid))

        colors[class_ids == cid] = np.asarray(color, dtype=np.uint8)

    return colors, None


def binary_colors_and_labels(
    class_ids: np.ndarray,
    label_map: dict[str, dict[str, Any]],
) -> tuple[np.ndarray, None]:
    """
    Build building/non-building colors from real class IDs and real label map.
    """

    class_ids = np.asarray(class_ids).astype(np.int64)

    unique_ids = sorted(int(x) for x in np.unique(class_ids))
    missing_ids = [
        cid
        for cid in unique_ids
        if lookup_label_info(cid, label_map) is None
    ]

    if missing_ids:
        raise ValueError(
            "Binary building/non-building mode requires every class ID in label map. "
            f"Missing IDs: {missing_ids[:20]}"
        )

    colors = np.zeros((len(class_ids), 3), dtype=np.uint8)

    for cid in unique_ids:
        info = lookup_label_info(cid, label_map)
        if info is None:
            raise ValueError(f"Binary label missing in label map: {cid}")

        is_building = bool(info.get("is_building", False))

        if is_building:
            colors[class_ids == cid] = [255, 0, 0]
        else:
            colors[class_ids == cid] = [140, 140, 140]

    return colors, None


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def normalize_rgb(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb)

    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"RGB/color array must have shape (N, 3). Got: {arr.shape}")

    if arr.dtype == np.uint8:
        return arr

    arr = arr.astype(np.float32, copy=False)

    if arr.max() <= 1.0:
        arr = arr * 255.0

    if arr.max() > 255.0:
        raise ValueError(
            "RGB values exceed 255. Export RGB as 0–255 or 0–1 before using RGB mode."
        )

    return np.clip(arr, 0, 255).astype(np.uint8)


def parse_color_from_attrs(attrs: dict[str, Any]) -> list[int] | None:
    if {"r", "g", "b"}.issubset(attrs.keys()):
        return [
            int(float(attrs["r"])),
            int(float(attrs["g"])),
            int(float(attrs["b"])),
        ]

    if {"red", "green", "blue"}.issubset(attrs.keys()):
        return [
            int(float(attrs["red"])),
            int(float(attrs["green"])),
            int(float(attrs["blue"])),
        ]

    value = attrs.get("color") or attrs.get("rgb") or attrs.get("hex")

    return parse_color_value(value)


def parse_color_value(value: Any) -> list[int] | None:
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()

        if text.startswith("#") and len(text) == 7:
            return [
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
            ]

        if "," in text:
            parts = [part.strip() for part in text.split(",")]

            if len(parts) == 3:
                return [int(float(part)) for part in parts]

    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [int(float(v)) for v in value]

    return None


def deterministic_color_from_class_id(class_id: int) -> list[int]:
    """
    Display-only color from a real class ID.

    This does not invent a class or label. It only gives unmapped-color classes
    a stable visualization color when the label map has a real class name but no color.
    """

    x = int(class_id)

    r = max((37 * x + 53) % 256, 40)
    g = max((91 * x + 101) % 256, 40)
    b = max((157 * x + 149) % 256, 40)

    return [int(r), int(g), int(b)]


def infer_coarse_class_from_name(name: Any) -> str:
    text = str(name or "").strip().lower()

    if "building" in text:
        return "building"

    if "road_marking" in text or "road marking" in text or "marking" in text:
        return "road_marking"

    if "road" in text or "ground" in text:
        return "ground"

    if "natural" in text or "vegetation" in text or "tree" in text:
        return "vegetation"

    if "utility" in text or "line" in text:
        return "utility_line"

    if "pole" in text:
        return "pole"

    if "car" in text or "vehicle" in text:
        return "car"

    if "fence" in text:
        return "fence"

    if "unclassified" in text:
        return "unclassified"

    return "unknown"


def infer_is_building(class_name: Any, coarse_name: Any) -> bool:
    text = f"{class_name or ''} {coarse_name or ''}".strip().lower()
    return any(token in text for token in ["building", "facade", "roof", "wall"])


def normalize_class_id(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text == "" or text.lower() == "nan":
        return ""

    try:
        number = float(text)

        if number.is_integer():
            return str(int(number))

    except Exception:
        pass

    return text


def strip_ns(text: Any) -> str:
    return str(text).split("}")[-1]


def describe_selected_color_source(
    fields: dict[str, Any],
    color_mode: str,
    view_mode: str,
    label_map_loaded: bool,
) -> str:
    if color_mode == "solid":
        return "real point positions, single fast display color"

    if color_mode == "rgb":
        return f"real RGB fields: {fields.get('rgb_columns', [])}"

    if color_mode == "height":
        return "real PLY Z coordinate"

    if color_mode == "intensity":
        return f"real intensity/reflectance field: {fields.get('intensity_column', '')}"

    if color_mode == "high_contrast":
        return f"real intensity/reflectance field (rank-normalized): {fields.get('intensity_column', '')}"

    if color_mode == "semantic_label":
        return (
            f"real semantic-label field {fields.get('semantic_label_column', '')} "
            f"joined with real label map"
        )

    if color_mode == "binary_label":
        return (
            f"real semantic-label field {fields.get('semantic_label_column', '')} "
            f"joined with real building/non-building mapping"
        )

    if view_mode in {"semantic", "binary"} and label_map_loaded:
        return (
            f"real semantic-label field {fields.get('semantic_label_column', '')} "
            f"joined with real label map"
        )

    return "real point-cloud fields"
