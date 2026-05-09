import os
import re
import xml.etree.ElementTree as ET

import numpy as np

from services.compat import disable_incompatible_pandas_accelerators

disable_incompatible_pandas_accelerators()

import pandas as pd


def infer_file_format(filename):
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".ply":
        return "PLY"
    if ext == ".las":
        return "LAS"
    if ext == ".laz":
        return "LAZ"
    if ext in [".txt", ".csv", ".pts", ".xyz"]:
        return "Text Point Cloud"

    return "Unsupported"


def read_pointcloud_summary(local_path):
    """
    Main dispatch function for reading point cloud metadata.
    """

    if not local_path:
        raise ValueError("local_path is empty.")

    if not os.path.exists(local_path):
        raise FileNotFoundError(local_path)

    ext = os.path.splitext(local_path)[1].lower()

    if ext == ".ply":
        return read_ply_summary_with_plyfile(local_path)

    if ext in [".las", ".laz"]:
        return read_las_laz_summary(local_path)

    if ext in [".csv", ".txt", ".pts", ".xyz"]:
        return read_text_pointcloud_summary(local_path)

    raise ValueError(f"Unsupported point cloud format: {ext}")


# =============================================================================
# Robust field / property detection
# =============================================================================

def _normalize_property_name(name):
    """
    Normalize point-cloud field names.

    Examples:
        scalar_Label          -> scalar_label
        Scalar Label          -> scalar_label
        scalar-Intensity      -> scalar_intensity
        scalar.Classification -> scalar_classification
    """

    text = str(name).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def _find_property(properties, possible_names):
    """
    Find a property name using robust case-insensitive matching.
    Returns the original property name from the PLY/LAS header.
    """

    norm_props = {_normalize_property_name(p): p for p in properties}

    for name in possible_names:
        key = _normalize_property_name(name)
        if key in norm_props:
            return norm_props[key]

    return None


def find_semantic_label_property(properties):
    """
    Detect real semantic label/class property.

    All of the following are treated as semantic_label:
        class
        label
        classification
        semantic_label
        scalar_Label
        scalar_Class
        scalar_Classification

    No mock labels are created.
    """

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

    found = _find_property(properties, candidates)
    if found:
        return found

    # Flexible fallback for CloudCompare scalar fields.
    for prop in properties:
        p = _normalize_property_name(prop)
        if p.startswith("scalar_") and any(
            token in p for token in ["label", "class", "classification", "semantic"]
        ):
            return prop

    return None


def find_intensity_or_reflectance_property(properties):
    """
    Detect real intensity/reflectance property.

    Supports:
        intensity
        reflectance
        scalar_Intensity
        scalar_Reflectance
    """

    candidates = [
        "reflectance",
        "intensity",
        "remission",
        "reflectivity",
        "amplitude",
        "scalar_reflectance",
        "scalar_intensity",
        "scalar_remission",
        "scalar_reflectivity",
        "scalar_amplitude",
    ]

    found = _find_property(properties, candidates)
    if found:
        return found

    # Flexible fallback for CloudCompare scalar fields.
    for prop in properties:
        p = _normalize_property_name(prop)
        if p.startswith("scalar_") and any(
            token in p
            for token in ["reflectance", "intensity", "remission", "reflectivity", "amplitude"]
        ):
            return prop

    return None


def build_label_histogram(labels):
    """
    Build exact label histogram from real semantic label values.

    This is required for individual semantic class distribution.
    """

    labels = np.asarray(labels)

    if labels.size == 0:
        return {}

    unique_labels, counts = np.unique(labels, return_counts=True)

    return {
        str(int(label)): int(count)
        for label, count in zip(unique_labels, counts)
    }


# =============================================================================
# PLY reader
# =============================================================================

def read_ply_summary_with_plyfile(local_path):
    """
    Real PLY reader.

    Supports:
        Paris-Lille style:
            class, label, reflectance

        Toronto / CloudCompare-exported style:
            scalar_Label
            scalar_Intensity

    This function does not generate mock labels or mock statistics.
    """

    from plyfile import PlyData

    ply = PlyData.read(local_path)

    vertex_names = [element.name for element in ply.elements]

    if "vertex" not in vertex_names:
        raise ValueError(
            f"No vertex element found in PLY file: {local_path}. "
            f"Available elements: {vertex_names}"
        )

    vertex = ply["vertex"]
    data = vertex.data

    if data is None or len(data) == 0:
        raise ValueError(f"No vertex data found in PLY file: {local_path}")

    properties = list(data.dtype.names or [])

    if not properties:
        raise ValueError(f"No PLY vertex properties found in: {local_path}")

    x_col = _find_property(properties, ["x", "X"])
    y_col = _find_property(properties, ["y", "Y"])
    z_col = _find_property(properties, ["z", "Z"])

    if not x_col or not y_col or not z_col:
        raise ValueError(
            f"PLY file does not contain x/y/z columns. "
            f"Available properties: {properties}"
        )

    x = np.asarray(data[x_col], dtype=np.float64)
    y = np.asarray(data[y_col], dtype=np.float64)
    z = np.asarray(data[z_col], dtype=np.float64)

    semantic_label_col = find_semantic_label_property(properties)
    intensity_col = find_intensity_or_reflectance_property(properties)

    xorigin_col = _find_property(
        properties,
        ["xorigin", "x_origin", "scanner_x", "origin_x", "scalar_xorigin"],
    )
    yorigin_col = _find_property(
        properties,
        ["yorigin", "y_origin", "scanner_y", "origin_y", "scalar_yorigin"],
    )
    zorigin_col = _find_property(
        properties,
        ["zorigin", "z_origin", "scanner_z", "origin_z", "scalar_zorigin"],
    )

    red_col = _find_property(properties, ["red", "r", "diffuse_red"])
    green_col = _find_property(properties, ["green", "g", "diffuse_green"])
    blue_col = _find_property(properties, ["blue", "b", "diffuse_blue"])

    has_rgb = red_col is not None and green_col is not None and blue_col is not None
    has_semantic_label = semantic_label_col is not None
    has_intensity = intensity_col is not None
    has_origin = (
        xorigin_col is not None
        and yorigin_col is not None
        and zorigin_col is not None
    )

    label_unique_count = None
    label_min = None
    label_max = None
    label_histogram = {}

    if has_semantic_label:
        labels = np.asarray(data[semantic_label_col])

        try:
            label_min = str(np.min(labels))
            label_max = str(np.max(labels))

            unique_labels = np.unique(labels)
            label_unique_count = int(len(unique_labels))
            label_histogram = build_label_histogram(labels)

        except Exception:
            label_unique_count = None
            label_min = ""
            label_max = ""
            label_histogram = {}

    intensity_min = None
    intensity_max = None

    if has_intensity:
        try:
            intensity_values = np.asarray(data[intensity_col], dtype=np.float64)
            finite = np.isfinite(intensity_values)

            if finite.any():
                intensity_min = float(np.nanmin(intensity_values[finite]))
                intensity_max = float(np.nanmax(intensity_values[finite]))
        except Exception:
            intensity_min = None
            intensity_max = None

    summary = {
        "filename": os.path.basename(local_path),
        "file_format": "PLY",
        "point_count": int(len(data)),

        "x_min": float(np.min(x)),
        "x_max": float(np.max(x)),
        "y_min": float(np.min(y)),
        "y_max": float(np.max(y)),
        "z_min": float(np.min(z)),
        "z_max": float(np.max(z)),

        "attributes": [str(p) for p in properties],

        "has_xyz": True,

        # Backward-compatible keys
        "has_label": bool(has_semantic_label),
        "label_column": semantic_label_col or "",

        # New normalized keys
        "has_semantic_label": bool(has_semantic_label),
        "semantic_label_column": semantic_label_col or "",

        "has_intensity": bool(has_intensity),
        "has_reflectance": bool(has_intensity),
        "has_rgb": bool(has_rgb),
        "has_origin": bool(has_origin),

        # Backward-compatible naming
        "reflectance_column": intensity_col or "",

        # Clearer naming
        "intensity_column": intensity_col or "",

        "xorigin_column": xorigin_col or "",
        "yorigin_column": yorigin_col or "",
        "zorigin_column": zorigin_col or "",

        "red_column": red_col or "",
        "green_column": green_col or "",
        "blue_column": blue_col or "",

        "label_unique_count": label_unique_count,
        "label_min": label_min,
        "label_max": label_max,
        "label_histogram": label_histogram,

        "intensity_min": intensity_min,
        "intensity_max": intensity_max,
        "reflectance_min": intensity_min,
        "reflectance_max": intensity_max,
    }

    return summary


def read_ply_class_distribution(local_path):
    """
    Reads point-level semantic class/label distribution from a PLY file.

    Handles:
        Paris-Lille:
            class / label

        Toronto:
            scalar_Label

    Output:
        DataFrame with:
            class_id, point_count
    """

    from plyfile import PlyData

    if not local_path:
        raise ValueError("local_path is empty.")

    if not os.path.exists(local_path):
        raise FileNotFoundError(local_path)

    ply = PlyData.read(local_path)

    vertex_names = [element.name for element in ply.elements]

    if "vertex" not in vertex_names:
        return pd.DataFrame(columns=["class_id", "point_count"])

    data = ply["vertex"].data

    if data is None or len(data) == 0:
        return pd.DataFrame(columns=["class_id", "point_count"])

    properties = list(data.dtype.names or [])

    if not properties:
        return pd.DataFrame(columns=["class_id", "point_count"])

    semantic_label_col = find_semantic_label_property(properties)

    if not semantic_label_col:
        return pd.DataFrame(columns=["class_id", "point_count"])

    labels = np.asarray(data[semantic_label_col])

    if labels.size == 0:
        return pd.DataFrame(columns=["class_id", "point_count"])

    unique_labels, counts = np.unique(labels, return_counts=True)

    return pd.DataFrame(
        {
            "class_id": unique_labels.astype(str),
            "point_count": counts.astype(int),
        }
    )


# =============================================================================
# LAS / LAZ reader
# =============================================================================

def read_las_laz_summary(local_path):
    """
    Read real metadata from LAS/LAZ using laspy.
    """

    import laspy

    las = laspy.read(local_path)

    x = np.asarray(las.x)
    y = np.asarray(las.y)
    z = np.asarray(las.z)

    if len(x) == 0:
        raise ValueError(f"No points found in LAS/LAZ file: {local_path}")

    attributes = ["x", "y", "z"]
    dimension_names = list(las.point_format.dimension_names)

    for dim in dimension_names:
        if dim not in attributes:
            attributes.append(dim)

    semantic_label_col = find_semantic_label_property(dimension_names)

    has_intensity = "intensity" in dimension_names
    has_rgb = all(c in dimension_names for c in ["red", "green", "blue"])
    has_semantic_label = semantic_label_col is not None

    label_unique_count = None
    label_min = None
    label_max = None
    label_histogram = {}

    if has_semantic_label:
        try:
            labels = np.asarray(getattr(las, semantic_label_col))
            label_min = str(np.min(labels))
            label_max = str(np.max(labels))
            label_unique_count = int(len(np.unique(labels)))
            label_histogram = build_label_histogram(labels)
        except Exception:
            label_unique_count = None
            label_min = ""
            label_max = ""
            label_histogram = {}

    return {
        "filename": os.path.basename(local_path),
        "file_format": infer_file_format(local_path),
        "point_count": int(len(las.points)),

        "x_min": float(np.min(x)),
        "x_max": float(np.max(x)),
        "y_min": float(np.min(y)),
        "y_max": float(np.max(y)),
        "z_min": float(np.min(z)),
        "z_max": float(np.max(z)),

        "attributes": attributes,
        "has_xyz": True,

        "has_label": bool(has_semantic_label),
        "label_column": semantic_label_col or "",
        "has_semantic_label": bool(has_semantic_label),
        "semantic_label_column": semantic_label_col or "",

        "has_intensity": bool(has_intensity),
        "has_reflectance": bool(has_intensity),
        "has_rgb": bool(has_rgb),
        "has_origin": False,

        "reflectance_column": "intensity" if has_intensity else "",
        "intensity_column": "intensity" if has_intensity else "",

        "xorigin_column": "",
        "yorigin_column": "",
        "zorigin_column": "",

        "red_column": "red" if "red" in dimension_names else "",
        "green_column": "green" if "green" in dimension_names else "",
        "blue_column": "blue" if "blue" in dimension_names else "",

        "label_unique_count": label_unique_count,
        "label_min": label_min,
        "label_max": label_max,
        "label_histogram": label_histogram,
    }


# =============================================================================
# Text point-cloud reader
# =============================================================================

def read_text_pointcloud_summary(local_path):
    """
    Basic reader for TXT/CSV/PTS/XYZ files.

    For files without headers, first three columns are assumed to be X, Y, Z.
    For CSV files with x/y/z headers, those columns are used directly.
    """

    filename = os.path.basename(local_path)
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(local_path)

        lower_cols = {str(c).lower(): c for c in df.columns}

        if {"x", "y", "z"}.issubset(set(lower_cols.keys())):
            xyz = df[
                [
                    lower_cols["x"],
                    lower_cols["y"],
                    lower_cols["z"],
                ]
            ].astype(float).values
            attributes = [str(c) for c in df.columns]
        else:
            if df.shape[1] < 3:
                raise ValueError(
                    f"CSV point cloud must have at least 3 columns: {local_path}"
                )

            xyz = df.iloc[:, :3].astype(float).values
            attributes = [str(c) for c in df.columns]

    else:
        df = pd.read_csv(
            local_path,
            sep=r"\s+|,",
            engine="python",
            header=None,
            comment="#",
        )

        if df.shape[1] < 3:
            raise ValueError(
                f"Text point cloud must have at least 3 columns: {local_path}"
            )

        xyz = df.iloc[:, :3].astype(float).values
        attributes = ["x", "y", "z"]

    if xyz.shape[0] == 0:
        raise ValueError(f"No points found in text point cloud: {local_path}")

    x = xyz[:, 0]
    y = xyz[:, 1]
    z = xyz[:, 2]

    return {
        "filename": filename,
        "file_format": infer_file_format(filename),
        "point_count": int(xyz.shape[0]),

        "x_min": float(np.min(x)),
        "x_max": float(np.max(x)),
        "y_min": float(np.min(y)),
        "y_max": float(np.max(y)),
        "z_min": float(np.min(z)),
        "z_max": float(np.max(z)),

        "attributes": attributes,
        "has_xyz": True,

        "has_label": False,
        "label_column": "",
        "has_semantic_label": False,
        "semantic_label_column": "",

        "has_intensity": False,
        "has_reflectance": False,
        "has_rgb": False,
        "has_origin": False,

        "reflectance_column": "",
        "intensity_column": "",

        "xorigin_column": "",
        "yorigin_column": "",
        "zorigin_column": "",

        "red_column": "",
        "green_column": "",
        "blue_column": "",

        "label_unique_count": None,
        "label_min": None,
        "label_max": None,
        "label_histogram": {},
    }


# =============================================================================
# Dashboard attribute summary
# =============================================================================

def build_attribute_summary(attributes):
    """
    Build attribute availability table for dashboard.

    Important:
    class / label / classification / semantic_label / scalar_Label
    are treated as one normalized semantic_label concept.
    """

    normalized_available = {_normalize_property_name(attr) for attr in attributes}

    def has_any(candidates):
        return any(_normalize_property_name(c) in normalized_available for c in candidates)

    has_x = has_any(["x"])
    has_y = has_any(["y"])
    has_z = has_any(["z"])

    has_xorigin = has_any(["xorigin", "x_origin", "scanner_x", "origin_x", "scalar_xorigin"])
    has_yorigin = has_any(["yorigin", "y_origin", "scanner_y", "origin_y", "scalar_yorigin"])
    has_zorigin = has_any(["zorigin", "z_origin", "scanner_z", "origin_z", "scalar_zorigin"])

    has_intensity = has_any(
        [
            "reflectance",
            "intensity",
            "remission",
            "reflectivity",
            "amplitude",
            "scalar_reflectance",
            "scalar_intensity",
            "scalar_remission",
            "scalar_reflectivity",
            "scalar_amplitude",
        ]
    )

    has_semantic_label = has_any(
        [
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
    )

    has_rgb = (
        has_any(["red", "r", "diffuse_red"])
        and has_any(["green", "g", "diffuse_green"])
        and has_any(["blue", "b", "diffuse_blue"])
    )

    rows = [
        {
            "attribute": "x",
            "available": "Yes" if has_x else "No",
            "available_numeric": 1 if has_x else 0,
            "use": "Geometry",
        },
        {
            "attribute": "y",
            "available": "Yes" if has_y else "No",
            "available_numeric": 1 if has_y else 0,
            "use": "Geometry",
        },
        {
            "attribute": "z",
            "available": "Yes" if has_z else "No",
            "available_numeric": 1 if has_z else 0,
            "use": "Geometry",
        },
        {
            "attribute": "xorigin",
            "available": "Yes" if has_xorigin else "No",
            "available_numeric": 1 if has_xorigin else 0,
            "use": "Scanner origin",
        },
        {
            "attribute": "yorigin",
            "available": "Yes" if has_yorigin else "No",
            "available_numeric": 1 if has_yorigin else 0,
            "use": "Scanner origin",
        },
        {
            "attribute": "zorigin",
            "available": "Yes" if has_zorigin else "No",
            "available_numeric": 1 if has_zorigin else 0,
            "use": "Scanner origin",
        },
        {
            "attribute": "reflectance_or_intensity",
            "available": "Yes" if has_intensity else "No",
            "available_numeric": 1 if has_intensity else 0,
            "use": "Feature",
        },
        {
            "attribute": "semantic_label",
            "available": "Yes" if has_semantic_label else "No",
            "available_numeric": 1 if has_semantic_label else 0,
            "use": "Semantic class",
        },
        {
            "attribute": "rgb",
            "available": "Yes" if has_rgb else "No",
            "available_numeric": 1 if has_rgb else 0,
            "use": "Color visualization",
        },
    ]

    return pd.DataFrame(rows)


# =============================================================================
# XML label mapping
# =============================================================================

def parse_label_mapping_xml(local_xml_path):
    """
    Parse XML label map.

    Supports:
    1. Paris-Lille coarse_classes.xml:
       <class id="203000000" en="building" coarse="2" coarse_name="building" />

    2. Toronto / CloudCompare style:
       <label val="4" name="Building" />
       <step r="0" g="0" b="255" pos="0.5" />
    """

    if not os.path.exists(local_xml_path):
        raise FileNotFoundError(local_xml_path)

    tree = ET.parse(local_xml_path)
    root = tree.getroot()

    rows = []

    # Read color-scale steps if present.
    color_steps = []

    for elem in root.iter():
        tag = str(elem.tag).split("}")[-1].lower()
        attrs = {str(k).lower(): v for k, v in elem.attrib.items()}

        if tag == "step" and {"r", "g", "b"}.issubset(attrs.keys()):
            try:
                color_steps.append(
                    {
                        "pos": float(attrs.get("pos", 0.0)),
                        "r": int(float(attrs["r"])),
                        "g": int(float(attrs["g"])),
                        "b": int(float(attrs["b"])),
                    }
                )
            except Exception:
                pass

    color_steps = sorted(color_steps, key=lambda item: item["pos"])

    for elem in root.iter():
        tag = str(elem.tag).split("}")[-1]
        attrib = {str(k): v for k, v in elem.attrib.items()}

        if not attrib:
            continue

        row = {"tag": tag}
        row.update(attrib)

        class_id = (
            attrib.get("id")
            or attrib.get("val")
            or attrib.get("value")
            or attrib.get("class")
            or attrib.get("label")
        )

        class_name = (
            attrib.get("en")
            or attrib.get("name")
            or attrib.get("label_name")
            or attrib.get("description")
            or attrib.get("desc")
        )

        coarse_id = attrib.get("coarse") or attrib.get("coarse_id") or ""
        coarse_name = (
            attrib.get("coarse_name")
            or attrib.get("coarse_class")
            or attrib.get("category")
            or ""
        )

        if class_id is not None:
            row["class_id"] = str(class_id)

        if class_name is not None:
            row["class_name"] = str(class_name)

        if coarse_id:
            row["coarse_id"] = str(coarse_id)

        if coarse_name:
            row["coarse_class_name"] = str(coarse_name)

        if class_name is not None and not coarse_name:
            row["coarse_class_name"] = infer_coarse_class_from_name(class_name)

        row["is_building"] = infer_is_building(
            class_name=class_name,
            coarse_name=row.get("coarse_class_name", coarse_name),
        )

        rows.append(row)

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # Attach colors from color scale to class/label rows if possible.
    if color_steps and "class_id" in df.columns:
        class_rows_mask = df["class_id"].notna()

        try:
            class_ids_numeric = pd.to_numeric(
                df.loc[class_rows_mask, "class_id"],
                errors="coerce",
            )
            valid_ids = class_ids_numeric.dropna()

            if not valid_ids.empty:
                min_id = int(valid_ids.min())
                max_id = int(valid_ids.max())
                denom = max(max_id - min_id, 1)

                for idx in df.loc[class_rows_mask].index:
                    cid = pd.to_numeric(df.at[idx, "class_id"], errors="coerce")

                    if pd.isna(cid):
                        continue

                    pos = (int(cid) - min_id) / denom
                    nearest = min(color_steps, key=lambda item: abs(item["pos"] - pos))

                    df.at[idx, "r"] = nearest["r"]
                    df.at[idx, "g"] = nearest["g"]
                    df.at[idx, "b"] = nearest["b"]
                    df.at[idx, "color_rgb"] = f'{nearest["r"]},{nearest["g"]},{nearest["b"]}'
        except Exception:
            pass

    # Backward-compatible Paris-Lille aliases.
    if "coarse" in df.columns:
        df["coarse_id"] = df["coarse"]

    if "coarse_name" in df.columns:
        df["coarse_class_name"] = df["coarse_name"]

    if "en" in df.columns:
        df["class_name"] = df["en"]

    if "id" in df.columns:
        df["class_id"] = df["id"]

    if "is_building" not in df.columns:
        df["is_building"] = False

    return df


def infer_coarse_class_from_name(class_name):
    name = str(class_name or "").strip().lower()

    if "building" in name:
        return "building"

    if "road_marking" in name or "road marking" in name or "marking" in name:
        return "road_marking"

    if "road" in name or "ground" in name:
        return "ground"

    if "natural" in name or "vegetation" in name or "tree" in name:
        return "vegetation"

    if "utility" in name or "line" in name:
        return "utility_line"

    if "pole" in name:
        return "pole"

    if "car" in name or "vehicle" in name:
        return "car"

    if "fence" in name:
        return "fence"

    if "unclassified" in name:
        return "unclassified"

    return "unknown"


def infer_is_building(class_name=None, coarse_name=None):
    text = f"{class_name or ''} {coarse_name or ''}".strip().lower()
    return any(token in text for token in ["building", "facade", "roof", "wall"])


def summarize_label_mapping(label_map_df):
    """
    Create dashboard-friendly summary of label mapping file.
    """

    if label_map_df is None or label_map_df.empty:
        return pd.DataFrame(
            [
                {
                    "mapping_file_available": "No",
                    "total_class_entries": 0,
                    "coarse_classes": 0,
                    "building_mapping_available": "No",
                    "building_coarse_id": "",
                    "building_class_name": "",
                }
            ]
        )

    total_entries = len(label_map_df)

    coarse_classes = 0

    if "coarse_class_name" in label_map_df.columns:
        coarse_classes = int(label_map_df["coarse_class_name"].nunique())
    elif "coarse_name" in label_map_df.columns:
        coarse_classes = int(label_map_df["coarse_name"].nunique())

    if "is_building" in label_map_df.columns:
        building_rows = label_map_df[label_map_df["is_building"] == True]
    else:
        building_rows = pd.DataFrame()

    building_coarse_id = ""
    building_class_name = ""

    if not building_rows.empty:
        if "coarse" in building_rows.columns:
            building_coarse_id = str(building_rows.iloc[0].get("coarse", ""))
        elif "coarse_id" in building_rows.columns:
            building_coarse_id = str(building_rows.iloc[0].get("coarse_id", ""))

        if "coarse_name" in building_rows.columns:
            building_class_name = str(
                building_rows.iloc[0].get("coarse_name", "building")
            )
        elif "coarse_class_name" in building_rows.columns:
            building_class_name = str(
                building_rows.iloc[0].get("coarse_class_name", "building")
            )
        elif "class_name" in building_rows.columns:
            building_class_name = str(
                building_rows.iloc[0].get("class_name", "building")
            )

    return pd.DataFrame(
        [
            {
                "mapping_file_available": "Yes",
                "total_class_entries": int(total_entries),
                "coarse_classes": int(coarse_classes),
                "building_mapping_available": "Yes"
                if not building_rows.empty
                else "No",
                "building_coarse_id": building_coarse_id,
                "building_class_name": building_class_name,
            }
        ]
    )
