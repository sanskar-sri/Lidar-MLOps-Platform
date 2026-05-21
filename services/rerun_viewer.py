"""
services/rerun_viewer.py

CloudCompare-style Rerun viewer utilities for the Dash Data Explorer.

Strict production rule:
- No mock points.
- No fake labels.
- No estimated semantic values.
- Every point position must come from the real point cloud.
- Every color mode must use real PLY/LAS fields or real uploaded label-map mapping.

Entity tree per tile:
    lidar/<tile>/solid/part_00
    lidar/<tile>/rgb/part_00
    lidar/<tile>/height/part_00
    lidar/<tile>/intensity/part_00
    lidar/<tile>/semantic_label/part_00
    lidar/<tile>/binary_label/part_00
    features/<tile>

Only modes with real source data are logged.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

rr = None
rrb = None
_grpc_sink_active: bool = False  # True when a live gRPC stream to the host viewer was opened


RERUN_APP_NAME = "building_identification_mls_data_explorer"
RERUN_ENTITY_ROOT = "lidar"
RERUN_FEATURE_ROOT = "features"
RERUN_POINT_RADII = None
RERUN_MAX_POINTS_PER_ENTITY = 1_000_000


def _ensure_rerun_on_path() -> None:
    """
    rerun-sdk installs its package inside a rerun_sdk/ subdirectory and uses a
    .pth file to add that subdirectory to sys.path.  In some venv configurations
    the .pth file is not processed (relative-path .pth entries are ignored when
    the site module is invoked with a non-standard prefix).  We resolve the path
    explicitly so the import always works regardless of how the server is started.
    """
    import sys

    site_packages = Path(__file__).resolve().parents[1] / ".venvvv" / "lib"
    for py_dir in site_packages.glob("python3.*"):
        candidate = py_dir / "site-packages" / "rerun_sdk"
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return


def get_rerun_modules():
    """
    Import Rerun only when a recording is generated/opened.

    Dash imports page modules at app startup. Keeping Rerun lazy prevents the
    dashboard itself from hanging while the heavy native Rerun package loads.
    """

    global rr, rrb

    if rr is None or rrb is None:
        _ensure_rerun_on_path()
        import rerun as _rr
        import rerun.blueprint as _rrb

        rr = _rr
        rrb = _rrb

    return rr, rrb


# ---------------------------------------------------------------------
# Recording lifecycle
# ---------------------------------------------------------------------

def _host_rerun_viewer_available(host: str = "host.docker.internal", port: int = 9876) -> bool:
    """Return True if a Rerun Viewer is listening on the host at the given port."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except Exception:
        return False


def init_recording(rrd_path: str, app_name: str = RERUN_APP_NAME, open_viewer: bool = False) -> None:
    """
    Initialize Rerun recording.

    Always saves to a .rrd file.  When running inside Docker, also adds a
    GrpcSink pointing at the Mac Rerun Viewer (host.docker.internal:9876) so
    data streams live into the already-open window during recording — no
    post-recording transfer needed.
    """

    global _grpc_sink_active
    _grpc_sink_active = False

    rr_module, rrb_module = get_rerun_modules()
    os.makedirs(os.path.dirname(rrd_path), exist_ok=True)

    rr_module.init(app_name)

    file_sink = rr_module.FileSink(rrd_path)

    if os.path.exists("/.dockerenv"):
        # Inside Docker: stream data to the host Mac viewer if it's running.
        # GrpcSink is the producer side — it pushes data TO the viewer's gRPC
        # server (port 9876).  This is the correct direction; rerun --connect
        # is the consumer side and does NOT push data to the viewer.
        host_grpc = "rerun+http://host.docker.internal:9876/proxy"
        if _host_rerun_viewer_available():
            try:
                rr_module.set_sinks(file_sink, rr_module.GrpcSink(host_grpc))
                _grpc_sink_active = True
            except Exception:
                rr_module.set_sinks(file_sink)
        else:
            rr_module.set_sinks(file_sink)
    elif open_viewer:
        rr_module.set_sinks(file_sink, rr_module.GrpcSink())
    else:
        rr_module.set_sinks(file_sink)

    rr_module.send_blueprint(
        rrb_module.Blueprint(
            rrb_module.Spatial3DView(name="Loading point cloud…", origin=RERUN_ENTITY_ROOT),
            collapse_panels=False,
        )
    )


# ---------------------------------------------------------------------
# Main real-data tile logging
# ---------------------------------------------------------------------

def log_real_tile_modes(
    tile_name: str,
    xyz: np.ndarray,
    rgb: np.ndarray | None = None,
    intensity: np.ndarray | None = None,
    semantic_class_ids: np.ndarray | None = None,
    semantic_colors: np.ndarray | None = None,
    semantic_labels: list[str] | None = None,
    binary_colors: np.ndarray | None = None,
    binary_labels: list[str] | None = None,
    n_orig: int | None = None,
    source_columns: dict[str, Any] | None = None,
    include_modes: set[str] | None = None,
    visual_origin: np.ndarray | None = None,
) -> dict[str, str]:
    """
    Log one real tile into Rerun.

    All modes are optional and are logged only when real arrays are provided.

    Parameters
    ----------
    xyz:
        Real Nx3 point positions from PLY/LAS.

    rgb:
        Real Nx3 RGB from point cloud, if available.

    intensity:
        Real N intensity/reflectance values, if available.

    semantic_class_ids:
        Real N semantic class IDs from scalar_Label / label / class field.

    semantic_colors:
        Real/derived visualization colors for semantic classes.
        The classes themselves must come from the point cloud and label map.

    binary_colors:
        Building/non-building visualization colors derived from real semantic class IDs
        and real XML/JSON/YAML mapping.
    """

    get_rerun_modules()

    if xyz is None or len(xyz) == 0:
        raise ValueError("Cannot log Rerun tile: xyz is empty.")

    xyz = np.asarray(xyz, dtype=np.float32)

    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError(f"xyz must have shape (N, 3). Got: {xyz.shape}")

    entity_base = f"{RERUN_ENTITY_ROOT}/{safe_entity_name(tile_name)}"
    radii = RERUN_POINT_RADII
    include_modes = set(include_modes or {"height"})

    logged_modes: dict[str, str] = {}
    height_colors = None

    if "solid" in include_modes:
        chunk_count = log_points3d_chunked(
            f"{entity_base}/solid",
            xyz=xyz,
            radii=radii,
        )
        logged_modes["solid"] = (
            "real point positions with default Rerun coloring "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    # Height / Z mode: always real because it uses real Z coordinates.
    if "height" in include_modes:
        height_colors = color_by_height(xyz)
        chunk_count = log_points3d_chunked(
            f"{entity_base}/height",
            xyz=xyz,
            colors=height_colors,
            radii=radii,
        )
        logged_modes["height"] = (
            "real PLY/LAS Z coordinate "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    # RGB mode: only if real RGB exists.
    if "rgb" in include_modes and rgb is not None:
        rgb = normalize_rgb(rgb)

        if len(rgb) != len(xyz):
            raise ValueError("RGB array length does not match xyz length.")

        chunk_count = log_points3d_chunked(
            f"{entity_base}/rgb",
            xyz=xyz,
            colors=rgb,
            radii=radii,
        )
        logged_modes["rgb"] = (
            "real point-cloud RGB fields "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    # Intensity mode: only if real intensity/reflectance exists.
    if "intensity" in include_modes and intensity is not None:
        intensity = np.asarray(intensity, dtype=np.float32)

        if len(intensity) != len(xyz):
            raise ValueError("Intensity array length does not match xyz length.")

        intensity_colors = color_by_scalar_bgyr(intensity)

        chunk_count = log_points3d_chunked(
            f"{entity_base}/intensity",
            xyz=xyz,
            colors=intensity_colors,
            radii=radii,
        )
        logged_modes["intensity"] = (
            "real point-cloud intensity / reflectance field "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    # High contrast mode: only if real intensity exists.
    if "high_contrast" in include_modes and intensity is not None:
        high_contrast_colors = color_by_rank_high_contrast(intensity)

        chunk_count = log_points3d_chunked(
            f"{entity_base}/high_contrast",
            xyz=xyz,
            colors=high_contrast_colors,
            radii=radii,
        )
        logged_modes["high_contrast"] = (
            "real intensity / reflectance field, rank-normalized "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    # Semantic mode: only if real class IDs and colors/labels exist.
    if (
        "semantic_label" in include_modes
        and semantic_class_ids is not None
        and semantic_colors is not None
    ):
        semantic_class_ids = np.asarray(semantic_class_ids).astype(np.int64)
        semantic_colors = normalize_rgb(semantic_colors)

        if len(semantic_class_ids) != len(xyz):
            raise ValueError("Semantic class-id array length does not match xyz length.")

        if len(semantic_colors) != len(xyz):
            raise ValueError("Semantic color array length does not match xyz length.")

        chunk_count = log_points3d_chunked(
            f"{entity_base}/semantic_label",
            xyz=xyz,
            colors=semantic_colors,
            radii=radii,
        )
        logged_modes["semantic_label"] = (
            "real semantic-label field joined with real label map "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    # Binary mode: only if real semantic class IDs and XML-derived binary labels exist.
    if (
        "binary_label" in include_modes
        and semantic_class_ids is not None
        and binary_colors is not None
    ):
        binary_colors = normalize_rgb(binary_colors)

        if len(binary_colors) != len(xyz):
            raise ValueError("Binary color array length does not match xyz length.")

        chunk_count = log_points3d_chunked(
            f"{entity_base}/binary_label",
            xyz=xyz,
            colors=binary_colors,
            radii=radii,
        )
        logged_modes["binary_label"] = (
            "real semantic-label field joined with real building/non-building mapping "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    # Local coordinates: real xyz re-centered. This is still real geometry,
    # but not a separate semantic mode.
    if "local_coords" in include_modes:
        centroid = xyz.mean(axis=0)
        local_xyz = (xyz - centroid).astype(np.float32)
        if height_colors is None:
            height_colors = color_by_height(xyz)

        chunk_count = log_points3d_chunked(
            f"{entity_base}/local_coords",
            xyz=local_xyz,
            colors=rgb if rgb is not None else height_colors,
            radii=radii,
        )
        logged_modes["local_coords"] = (
            "real point coordinates shifted to local tile centroid "
            f"({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
        )

    log_point_feature_document(
        tile_name=tile_name,
        xyz=xyz,
        rgb=rgb,
        intensity=intensity,
        semantic_class_ids=semantic_class_ids,
        n_orig=n_orig or len(xyz),
        n_sub=len(xyz),
        logged_modes=logged_modes,
        source_columns=source_columns or {},
        visual_origin=visual_origin,
    )

    return logged_modes


def log_points3d_chunked(
    entity_path: str,
    xyz: np.ndarray,
    colors: np.ndarray | None = None,
    radii: float | None = None,
    max_points_per_entity: int = RERUN_MAX_POINTS_PER_ENTITY,
) -> int:
    """
    Log a large point cloud as multiple static Rerun entities.

    Large single `Points3D` blobs can make hover and camera interaction jittery.
    Splitting by the longest spatial axis keeps each GPU batch smaller while the
    blueprint still shows the selected mode as one logical layer.
    """

    rr_module, _ = get_rerun_modules()
    xyz = np.asarray(xyz, dtype=np.float32)
    n_points = len(xyz)

    if n_points == 0:
        raise ValueError("Cannot log an empty point cloud.")

    if colors is not None:
        colors = normalize_rgb(colors)

        if len(colors) != n_points:
            raise ValueError("Color array length does not match xyz length.")

    chunk_count = max(1, int(np.ceil(n_points / max_points_per_entity)))

    if chunk_count == 1:
        rr_module.log(
            f"{entity_path}/part_00",
            rr_module.Points3D(
                positions=xyz,
                colors=colors,
                radii=radii,
            ),
            static=True,
        )
        return 1

    axis = int(np.argmax(xyz.max(axis=0) - xyz.min(axis=0)))
    order = np.argsort(xyz[:, axis], kind="quicksort")

    for part_idx, point_idx in enumerate(np.array_split(order, chunk_count)):
        part_colors = colors[point_idx] if colors is not None else None

        rr_module.log(
            f"{entity_path}/part_{part_idx:02d}",
            rr_module.Points3D(
                positions=xyz[point_idx],
                colors=part_colors,
                radii=radii,
            ),
            static=True,
        )

    return chunk_count


# ---------------------------------------------------------------------
# Feature panel
# ---------------------------------------------------------------------

def log_point_feature_document(
    tile_name: str,
    xyz: np.ndarray,
    rgb: np.ndarray | None,
    intensity: np.ndarray | None,
    semantic_class_ids: np.ndarray | None,
    n_orig: int,
    n_sub: int,
    logged_modes: dict[str, str],
    source_columns: dict[str, Any],
    visual_origin: np.ndarray | None = None,
) -> None:
    rr_module, _ = get_rerun_modules()
    xyz_min = xyz.min(axis=0)
    xyz_max = xyz.max(axis=0)
    extent = xyz_max - xyz_min
    centroid = xyz.mean(axis=0)

    volume = float(np.prod(np.clip(extent, 1e-3, None)))
    density = float(n_sub / volume)

    md: list[str] = [
        f"# {tile_name}",
        "",
        "## Data Source",
        "This panel describes real point-cloud data only.",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Original points | {n_orig:,} |",
        f"| Logged points | {n_sub:,} |",
        f"| Subsample ratio | {safe_percent(n_sub, n_orig):.2f}% |",
        f"| Visualization coordinates | Local tile coordinates |",
        "",
        "## Detected Source Columns",
        "| Concept | Real field |",
        "|---|---|",
        f"| XYZ | `{source_columns.get('xyz', 'x,y,z')}` |",
        f"| RGB | `{source_columns.get('rgb', '')}` |",
        f"| Intensity / Reflectance | `{source_columns.get('intensity', '')}` |",
        f"| Semantic Label | `{source_columns.get('semantic_label', '')}` |",
        "",
        "## Bounding Box",
        "| Axis | Min | Max | Extent |",
        "|---|---:|---:|---:|",
        f"| X | {xyz_min[0]:.3f} | {xyz_max[0]:.3f} | {extent[0]:.3f} |",
        f"| Y | {xyz_min[1]:.3f} | {xyz_max[1]:.3f} | {extent[1]:.3f} |",
        f"| Z | {xyz_min[2]:.3f} | {xyz_max[2]:.3f} | {extent[2]:.3f} |",
        "",
        "## Center",
        f"- X: `{centroid[0]:.3f}`",
        f"- Y: `{centroid[1]:.3f}`",
        f"- Z: `{centroid[2]:.3f}`",
        "",
    ]

    if visual_origin is not None:
        origin = np.asarray(visual_origin, dtype=np.float32)
        md += [
            "## Local Coordinate Origin",
            f"- Original X offset: `{origin[0]:.3f}`",
            f"- Original Y offset: `{origin[1]:.3f}`",
            f"- Original Z offset: `{origin[2]:.3f}`",
            "",
        ]

    md += [
        "## Density Estimate",
        f"- Bounding-box volume: `{volume:,.3f}`",
        f"- Approx. density: `{density:,.3f} points / m³`",
        "",
        "## Logged Real Color Modes",
        "| Mode | Source |",
        "|---|---|",
    ]

    for mode, source in logged_modes.items():
        md.append(f"| `{mode}` | {source} |")

    if intensity is not None:
        p5, p25, p50, p75, p95 = np.percentile(intensity, [5, 25, 50, 75, 95])
        md += [
            "",
            "## Intensity / Reflectance Statistics",
            "| Stat | Value |",
            "|---|---:|",
            f"| Min | {float(np.min(intensity)):.3f} |",
            f"| P5 | {p5:.3f} |",
            f"| P25 | {p25:.3f} |",
            f"| Median | {p50:.3f} |",
            f"| Mean | {float(np.mean(intensity)):.3f} |",
            f"| P75 | {p75:.3f} |",
            f"| P95 | {p95:.3f} |",
            f"| Max | {float(np.max(intensity)):.3f} |",
        ]

    if semantic_class_ids is not None:
        unique, counts = np.unique(semantic_class_ids, return_counts=True)
        md += [
            "",
            "## Sampled Semantic Label Distribution",
            "| Class ID | Points |",
            "|---:|---:|",
        ]

        for cid, count in zip(unique, counts):
            md.append(f"| {int(cid)} | {int(count):,} |")

    rr_module.log(
        f"{RERUN_FEATURE_ROOT}/{safe_entity_name(tile_name)}",
        rr_module.TextDocument("\n".join(md), media_type=rr_module.MediaType.MARKDOWN),
        static=True,
    )


# ---------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------

def finalize_blueprint(
    xyz_min: np.ndarray,
    xyz_max: np.ndarray,
    available_modes: set[str],
) -> None:
    """
    Create a lightweight layout for large point clouds.

    A single active 3D view is intentionally used here. Multiple tabs that each
    reference the same million-point entity can make camera movement and hover
    interaction feel unstable on large recordings.
    """

    rr_module, rrb_module = get_rerun_modules()
    xyz_min = np.asarray(xyz_min, dtype=np.float32)
    xyz_max = np.asarray(xyz_max, dtype=np.float32)

    center = ((xyz_min + xyz_max) / 2.0).tolist()
    extent = float(np.max(xyz_max - xyz_min))
    dist = max(extent * 1.8, 10.0)

    mode_labels = {
        "solid": "Fast Points",
        "height": "Height / Z",
        "rgb": "RGB",
        "intensity": "Intensity / Reflectance",
        "high_contrast": "High Contrast",
        "semantic_label": "Semantic Label",
        "binary_label": "Building vs Non-building",
        "local_coords": "Local Coordinates",
    }

    mode_order = [
        "solid",
        "height",
        "rgb",
        "intensity",
        "high_contrast",
        "semantic_label",
        "binary_label",
        "local_coords",
    ]

    active_mode = next((mode for mode in mode_order if mode in available_modes), "")
    view_name = mode_labels.get(active_mode, "Point Cloud")
    # Use the SDK default "$origin/**" — shows everything logged under the
    # entity root.  Sub-path filters like "$origin/*/<mode>/**" are syntactically
    # valid but reliably resolve to empty in Rerun 0.31.x when the viewer opens
    # a saved .rrd file.  Since exactly one mode is logged per recording this
    # catches all the data without ambiguity.
    view_contents = "$origin/**"

    eye = (
        center[0] + dist,
        center[1] - dist,
        center[2] + dist,
    )

    try:
        point_view = rrb_module.Spatial3DView(
            name=view_name,
            origin=RERUN_ENTITY_ROOT,
            contents=view_contents,
            background=rrb_module.Background(rrb_module.BackgroundKind.GradientDark),
            line_grid=rrb_module.LineGrid3D(False),
            eye_controls=rrb_module.EyeControls3D(
                kind=rrb_module.Eye3DKind.Orbital,
                position=eye,
                look_target=tuple(center),
                eye_up=(0, 0, 1),
            ),
        )
    except Exception:
        point_view = rrb_module.Spatial3DView(
            name=view_name,
            origin=RERUN_ENTITY_ROOT,
            contents=view_contents,
        )

    rr_module.send_blueprint(
        rrb_module.Blueprint(
            rrb_module.Horizontal(
                point_view,
                rrb_module.TextDocumentView(
                    name="Point Features",
                    origin=RERUN_FEATURE_ROOT,
                    contents=f"{RERUN_FEATURE_ROOT}/**",
                ),
                column_shares=[3, 1],
            ),
            collapse_panels=False,
        )
    )

    # Flush all pending messages to the FileSink before returning.
    # FileSink writes asynchronously — without an explicit disconnect the .rrd
    # file may be only partially written when open_saved_rrd tries to open it.
    rr_module.disconnect()


# ---------------------------------------------------------------------
# Optional manual viewer opener
# ---------------------------------------------------------------------

def open_saved_rrd(rrd_path: str) -> None:
    """
    Open a saved .rrd file in the native Rerun Viewer.

    The venv's `rerun` CLI entry-point fails when the .pth file that adds
    rerun_sdk/ to sys.path is not processed (a known issue with some venv
    configurations on macOS).  We bypass the broken entry-point and launch
    the viewer directly via the Python interpreter, injecting the correct
    sys.path ourselves.
    """

    import sys

    if not os.path.exists(rrd_path):
        raise FileNotFoundError(rrd_path)

    # Inside Docker there is no display to spawn a new viewer window.
    # /.dockerenv is the standard Docker runtime sentinel (absent on macOS/bare-metal).
    if os.path.exists("/.dockerenv"):
        rrd_filename = os.path.basename(rrd_path)
        host_project_dir = os.environ.get("HOST_PROJECT_DIR", "").rstrip("/")
        if host_project_dir:
            mac_abs_path = f"{host_project_dir}/data/rerun_outputs/{rrd_filename}"
        else:
            mac_abs_path = f"data/rerun_outputs/{rrd_filename}"

        if _grpc_sink_active:
            # Data was already streamed live to the host viewer via GrpcSink
            # during recording — nothing more to do.
            return

        # Viewer was not running when recording started.
        # Guide the user to the two easiest options.
        raise RuntimeError(
            f"Recording saved → {mac_abs_path}\n\n"
            "To open automatically next time:\n\n"
            "  Option A — start an empty Rerun window on your Mac first:\n"
            "    rerun\n"
            "  Then click 'Open in Rerun Viewer' again. Data streams live\n"
            "  into the open window as the recording is generated.\n\n"
            "  Option B — run the file watcher on your Mac:\n"
            "    python watch_rerun.py\n"
            "  New .rrd files are auto-opened whenever you click the button.\n\n"
            f"  Manual: rerun \"{mac_abs_path}\""
        )

    # Resolve the rerun_sdk directory the same way _ensure_rerun_on_path does.
    rerun_sdk_dir = ""
    site_packages = Path(__file__).resolve().parents[1] / ".venvvv" / "lib"
    for py_dir in site_packages.glob("python3.*"):
        candidate = py_dir / "site-packages" / "rerun_sdk"
        if candidate.is_dir():
            rerun_sdk_dir = str(candidate)
            break

    # Launch the viewer in a detached subprocess using the same interpreter.
    # Injecting rerun_sdk_dir into sys.path before the import ensures rerun_cli
    # is importable even when the .pth file is not processed.
    # --new is an alias for --port auto: always spawns a fresh viewer window
    # even when another Rerun viewer is already running on the default port.
    launch_script = (
        f"import sys; "
        f"sys.path.insert(0, {rerun_sdk_dir!r}); "
        f"from rerun_cli.__main__ import main; "
        f"sys.argv = ['rerun', '--new', {rrd_path!r}]; "
        f"main()"
    )

    subprocess.Popen([sys.executable, "-c", launch_script])


# ---------------------------------------------------------------------
# Real-data color helpers
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
        raise ValueError("RGB/color values exceed 255. Use 0–255 or 0–1 colors.")

    return np.clip(arr, 0, 255).astype(np.uint8)


def normalize_01(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    finite = np.isfinite(values)

    if not finite.any():
        raise ValueError("Selected scalar field has no finite values.")

    vmin = float(np.nanmin(values[finite]))
    vmax = float(np.nanmax(values[finite]))

    if abs(vmax - vmin) < 1e-12:
        return np.zeros_like(values, dtype=np.float32)

    return np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)


def color_by_height(xyz: np.ndarray) -> np.ndarray:
    """
    Real Z-based color map.
    """

    z = np.asarray(xyz[:, 2], dtype=np.float32)
    return color_by_scalar_bgyr(z)


def color_by_scalar_bgyr(values: np.ndarray) -> np.ndarray:
    """
    Blue-Green-Yellow-Red style scalar coloring from real scalar values.
    """

    t = normalize_01(values)

    r = np.zeros_like(t)
    g = np.zeros_like(t)
    b = np.zeros_like(t)

    # 0.00–0.33: blue to green
    m1 = t <= 1 / 3
    r[m1] = 0
    g[m1] = t[m1] * 3
    b[m1] = 1 - t[m1] * 3

    # 0.33–0.66: green to yellow
    m2 = (t > 1 / 3) & (t <= 2 / 3)
    r[m2] = (t[m2] - 1 / 3) * 3
    g[m2] = 1
    b[m2] = 0

    # 0.66–1.00: yellow to red
    m3 = t > 2 / 3
    r[m3] = 1
    g[m3] = 1 - (t[m3] - 2 / 3) * 3
    b[m3] = 0

    return np.column_stack([r, g, b]).clip(0, 1).astype(np.float32) * 255


def color_by_rank_high_contrast(values: np.ndarray) -> np.ndarray:
    """
    High-contrast color using rank normalization of real scalar values.
    This does not create fake values; it only changes visualization contrast.
    """

    values = np.asarray(values, dtype=np.float32)

    finite = np.isfinite(values)

    if not finite.any():
        raise ValueError("High-contrast mode requires finite scalar values.")

    order = np.argsort(values)
    ranks = np.empty_like(order, dtype=np.float32)
    ranks[order] = np.linspace(0.0, 1.0, len(values), dtype=np.float32)

    return color_by_scalar_bgyr(ranks)


# ---------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------

def safe_entity_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(name))


def safe_percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator) / float(denominator) * 100.0
