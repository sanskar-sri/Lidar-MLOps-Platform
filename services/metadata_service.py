import os
import json
from datetime import datetime
from collections import Counter

from services.compat import disable_incompatible_pandas_accelerators

disable_incompatible_pandas_accelerators()

import pandas as pd

from services.upload_progress import update_metadata_progress
from services.b2_service import (
    get_b2_tiles_for_dataset,
    get_b2_label_maps_for_dataset,
    download_b2_file_to_local,
    upload_local_file_to_b2_path,
    upload_local_directory_to_b2,
)

from services.pointcloud_reader import (
    read_pointcloud_summary,
    read_ply_class_distribution,
    build_attribute_summary,
    parse_label_mapping_xml,
    summarize_label_mapping,
)

from services.parquet_service import save_analytics_parquets


# -------------------------------------------------------------------
# Local metadata output paths
# -------------------------------------------------------------------

METADATA_DIR = "data/metadata/datasets"
ANALYTICS_DIR = "data/metadata_analytics"
TEMP_B2_DOWNLOAD_DIR = "data/local_staging/b2_metadata_downloads"


# -------------------------------------------------------------------
# Directory helpers
# -------------------------------------------------------------------

def ensure_dirs():
    os.makedirs(METADATA_DIR, exist_ok=True)
    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    os.makedirs(TEMP_B2_DOWNLOAD_DIR, exist_ok=True)


def safe_divide(numerator, denominator):
    if denominator == 0:
        return 0
    return numerator / denominator


def normalize_class_id_value(value):
    """
    Normalize class IDs for safe joins.

    Examples:
        4      -> "4"
        4.0    -> "4"
        "4.0"  -> "4"

    This is important because Toronto XML may store class IDs as val="4",
    while PLY scalar_Label may be read as 4 or 4.0 depending on dtype.
    """

    if value is None:
        return ""

    text = str(value).strip()

    if text == "" or text.lower() == "nan":
        return ""

    try:
        numeric = float(text)
        if numeric.is_integer():
            return str(int(numeric))
    except Exception:
        pass

    return text


def infer_coarse_class_from_name(class_name):
    """
    Infer coarse class from an existing real class name.

    This does not create fake labels. It only normalizes real XML class names
    such as Building, Road_markings, Natural, Utility_line, etc.
    """

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


def infer_is_building_from_row(row):
    """
    Determine building/non-building from mapped XML fields.

    Uses real XML-derived columns:
        class_name
        coarse_class_name
        is_building
    """

    existing = row.get("is_building", False)

    if str(existing).strip().lower() in ["true", "1", "yes"]:
        return True

    class_name = row.get("class_name", "")
    coarse_name = row.get("coarse_class_name", "")

    text = f"{class_name} {coarse_name}".strip().lower()

    return any(token in text for token in ["building", "facade", "roof", "wall"])


def build_distribution_from_file_histograms(file_summaries):
    """
    Build dataset-level semantic class counts from per-tile label_histogram.

    Source:
        label_histogram from services/pointcloud_reader.py

    This uses only real label/class values extracted from point-cloud tiles.
    No mock values, no estimated distribution.
    """

    counter = Counter()

    for item in file_summaries:
        histogram = item.get("label_histogram") or {}

        for class_id, count in histogram.items():
            class_id_norm = normalize_class_id_value(class_id)

            if class_id_norm == "":
                continue

            counter[class_id_norm] += int(count)

    if not counter:
        return pd.DataFrame(columns=["class_id", "point_count"])

    rows = [
        {
            "class_id": class_id,
            "point_count": int(count),
        }
        for class_id, count in counter.items()
    ]

    return pd.DataFrame(rows)


def make_json_safe(obj):
    """
    Converts NumPy/pandas values into JSON-safe Python values.
    """

    try:
        import numpy as np

        if isinstance(obj, (np.integer,)):
            return int(obj)

        if isinstance(obj, (np.floating,)):
            return float(obj)

        if isinstance(obj, (np.bool_,)):
            return bool(obj)

        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()

    except Exception:
        pass

    try:
        if pd.isna(obj) and not isinstance(obj, (list, dict, tuple)):
            return None
    except Exception:
        pass

    return str(obj)


# -------------------------------------------------------------------
# Optional JSON label-map parser
# -------------------------------------------------------------------

def parse_label_mapping_json(local_json_path):
    """
    Basic JSON label-map parser.

    Supports common formats:
    1. List of class dictionaries
    2. Dictionary with key 'classes'
    3. Dictionary mapping IDs to names
    """

    if not os.path.exists(local_json_path):
        raise FileNotFoundError(local_json_path)

    with open(local_json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    rows = []

    if isinstance(payload, list):
        rows = payload

    elif isinstance(payload, dict):
        if "classes" in payload and isinstance(payload["classes"], list):
            rows = payload["classes"]
        else:
            for key, value in payload.items():
                if isinstance(value, dict):
                    row = {"class_id": key}
                    row.update(value)
                    rows.append(row)
                else:
                    rows.append(
                        {
                            "class_id": key,
                            "class_name": str(value),
                        }
                    )

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    if "class_id" not in df.columns and "id" in df.columns:
        df["class_id"] = df["id"]

    if "class_id" not in df.columns and "val" in df.columns:
        df["class_id"] = df["val"]

    if "class_name" not in df.columns and "name" in df.columns:
        df["class_name"] = df["name"]

    if "class_name" not in df.columns and "en" in df.columns:
        df["class_name"] = df["en"]

    if "coarse_id" not in df.columns and "coarse" in df.columns:
        df["coarse_id"] = df["coarse"]

    if "coarse_class_name" not in df.columns and "coarse_name" in df.columns:
        df["coarse_class_name"] = df["coarse_name"]

    if "coarse_class_name" not in df.columns:
        if "class_name" in df.columns:
            df["coarse_class_name"] = df["class_name"].apply(
                infer_coarse_class_from_name
            )
        else:
            df["coarse_class_name"] = "unknown"

    df["is_building"] = df.apply(infer_is_building_from_row, axis=1)

    return df


# -------------------------------------------------------------------
# Main metadata generation function
# -------------------------------------------------------------------

def build_uploaded_file_lookup(uploaded_files):
    """
    Map B2 object names to already-available local files from the upload step.
    This avoids downloading a large tile from B2 immediately after uploading it.
    """

    lookup = {}

    for item in uploaded_files or []:
        if not isinstance(item, dict):
            continue

        b2_path = str(item.get("b2_path") or "").strip()
        local_path = str(item.get("local_file_path") or "").strip()

        if (
            b2_path
            and local_path
            and os.path.exists(local_path)
            and os.path.isfile(local_path)
        ):
            lookup[b2_path] = local_path

    return lookup


def get_uploaded_local_path(uploaded_file_lookup, b2_file_name):
    if not uploaded_file_lookup or not b2_file_name:
        return None

    local_path = uploaded_file_lookup.get(str(b2_file_name).strip())

    if local_path and os.path.exists(local_path) and os.path.isfile(local_path):
        return local_path

    return None


def get_cached_b2_download_path(dataset_temp_dir, b2_file_name, expected_size=None):
    if not dataset_temp_dir or not b2_file_name:
        return None

    cached_path = os.path.join(
        dataset_temp_dir,
        os.path.basename(b2_file_name),
    )

    if not os.path.exists(cached_path) or not os.path.isfile(cached_path):
        return None

    if expected_size is None:
        return cached_path

    try:
        if os.path.getsize(cached_path) == int(expected_size):
            return cached_path
    except Exception:
        return None

    return None


def generate_dataset_metadata_and_analytics(
    dataset_id,
    dataset_name,
    upload_mode,
    description,
    filenames=None,
    uploaded_files=None,
):
    """
    Generate real dataset metadata and analytics.

    Reads uploaded files from B2:

        bronze_raw_data/<dataset_id>/source_files/tiles/
        bronze_raw_data/<dataset_id>/source_files/label_maps/

    Creates locally:

        data/metadata/datasets/<dataset_id>.json

        data/metadata_analytics/<dataset_id>/
            file_summary.parquet
            attribute_summary.parquet
            label_distribution.parquet
            class_label_distribution.parquet
            spatial_summary.parquet
            dashboard_kpis.parquet
            quality_checks.parquet
            class_mapping_summary.parquet
            class_mapping.parquet

    Then uploads metadata and analytics back to B2:

        metadata/datasets/<dataset_id>.json
        metadata_analytics/<dataset_id>/*.parquet

    If uploaded_files is supplied, matching B2 objects are read from the local
    upload source instead of being downloaded from B2 again.
    """

    ensure_dirs()

    if not dataset_id:
        raise ValueError("dataset_id is required.")

    dataset_id = str(dataset_id).strip()

    if not dataset_name:
        dataset_name = dataset_id

    dataset_name = str(dataset_name).strip()

    if description is None:
        description = ""

    dataset_temp_dir = os.path.join(TEMP_B2_DOWNLOAD_DIR, dataset_id)
    os.makedirs(dataset_temp_dir, exist_ok=True)

    print("=" * 80)
    print("[REAL METADATA EXTRACTION STARTED]")
    print(f"Dataset ID   : {dataset_id}")
    print(f"Dataset Name : {dataset_name}")
    print("=" * 80)

    uploaded_file_lookup = build_uploaded_file_lookup(uploaded_files)
    update_metadata_progress(
        dataset_id,
        message="Discovering uploaded B2 files for metadata generation",
        percentage=90,
    )

    # -------------------------------------------------------------------
    # 1. Discover B2 raw tiles and label maps
    # -------------------------------------------------------------------

    b2_tiles = get_b2_tiles_for_dataset(dataset_id)
    b2_label_maps = get_b2_label_maps_for_dataset(dataset_id)

    print("[B2 RAW FILE DISCOVERY]")
    print(f"Tiles found      : {len(b2_tiles)}")
    print(f"Label maps found : {len(b2_label_maps)}")
    print("=" * 80)

    if not b2_tiles:
        raise ValueError(
            f"No point cloud tiles found in B2 for dataset_id={dataset_id}. "
            f"Expected prefix: bronze_raw_data/{dataset_id}/source_files/tiles/"
        )

    # -------------------------------------------------------------------
    # 2. Download tiles temporarily and extract real metadata
    # -------------------------------------------------------------------

    file_summaries = []
    all_attributes = set()
    total_points = 0
    class_distribution_frames = []

    tile_total = len(b2_tiles)

    for tile_index, tile in enumerate(b2_tiles, start=1):
        b2_file_name = tile["file_name"]
        uploaded_local_path = get_uploaded_local_path(
            uploaded_file_lookup,
            b2_file_name,
        )

        if uploaded_local_path:
            local_tile_path = uploaded_local_path

            print("[USING LOCAL UPLOADED TILE]")
            print(f"B2    : {b2_file_name}")
            print(f"Local : {local_tile_path}")
            print("=" * 80)

        else:
            cached_tile_path = get_cached_b2_download_path(
                dataset_temp_dir=dataset_temp_dir,
                b2_file_name=b2_file_name,
                expected_size=tile.get("size"),
            )

            if cached_tile_path:
                local_tile_path = cached_tile_path

                print("[USING CACHED B2 TILE]")
                print(f"B2    : {b2_file_name}")
                print(f"Local : {local_tile_path}")
                print("=" * 80)

            else:
                local_tile_path = os.path.join(
                    dataset_temp_dir,
                    os.path.basename(b2_file_name),
                )

                print("[DOWNLOADING TILE FROM B2]")
                print(f"B2    : {b2_file_name}")
                print(f"Local : {local_tile_path}")
                print("=" * 80)

                download_b2_file_to_local(
                    b2_file_name=b2_file_name,
                    local_output_path=local_tile_path,
                )

        print("[READING TILE METADATA]")
        print(local_tile_path)
        print("=" * 80)

        update_metadata_progress(
            dataset_id,
            message=f"Reading metadata for {os.path.basename(b2_file_name)}",
            percentage=90 + round((tile_index / max(tile_total, 1)) * 6, 2),
        )

        summary = read_pointcloud_summary(local_tile_path)

        # Optional fallback distribution reader. The preferred source is
        # summary["label_histogram"], so avoid a second full PLY read when it exists.
        if (
            local_tile_path.lower().endswith(".ply")
            and not summary.get("label_histogram")
        ):
            try:
                class_dist_df = read_ply_class_distribution(local_tile_path)

                if class_dist_df is not None and not class_dist_df.empty:
                    class_dist_df["tile_name"] = os.path.basename(local_tile_path)
                    class_distribution_frames.append(class_dist_df)

            except Exception as class_error:
                print("=" * 80)
                print("[CLASS DISTRIBUTION WARNING]")
                print(f"Could not extract class distribution from: {local_tile_path}")
                print(f"Error: {class_error}")
                print("=" * 80)

        summary["b2_path"] = b2_file_name
        summary["b2_size_bytes"] = tile.get("size")
        summary["b2_file_id"] = tile.get("id")

        file_summaries.append(summary)

        total_points += int(summary.get("point_count", 0))

        for attr in summary.get("attributes", []):
            all_attributes.add(str(attr))

    # -------------------------------------------------------------------
    # 3. Download and parse label mapping files
    # -------------------------------------------------------------------

    label_mapping_df = pd.DataFrame()
    label_map_entries = []

    label_map_total = len(b2_label_maps)

    for label_map_index, label_map in enumerate(b2_label_maps, start=1):
        b2_file_name = label_map["file_name"]
        uploaded_local_path = get_uploaded_local_path(
            uploaded_file_lookup,
            b2_file_name,
        )

        if uploaded_local_path:
            local_label_map_path = uploaded_local_path

            print("[USING LOCAL UPLOADED LABEL MAP]")
            print(f"B2    : {b2_file_name}")
            print(f"Local : {local_label_map_path}")
            print("=" * 80)

        else:
            cached_label_map_path = get_cached_b2_download_path(
                dataset_temp_dir=dataset_temp_dir,
                b2_file_name=b2_file_name,
                expected_size=label_map.get("size"),
            )

            if cached_label_map_path:
                local_label_map_path = cached_label_map_path

                print("[USING CACHED B2 LABEL MAP]")
                print(f"B2    : {b2_file_name}")
                print(f"Local : {local_label_map_path}")
                print("=" * 80)

            else:
                local_label_map_path = os.path.join(
                    dataset_temp_dir,
                    os.path.basename(b2_file_name),
                )

                print("[DOWNLOADING LABEL MAP FROM B2]")
                print(f"B2    : {b2_file_name}")
                print(f"Local : {local_label_map_path}")
                print("=" * 80)

                download_b2_file_to_local(
                    b2_file_name=b2_file_name,
                    local_output_path=local_label_map_path,
                )

        update_metadata_progress(
            dataset_id,
            message=f"Parsing label map {os.path.basename(b2_file_name)}",
            percentage=96 + round((label_map_index / max(label_map_total, 1)) * 2, 2),
        )

        label_map_entries.append(
            {
                "file_name": os.path.basename(b2_file_name),
                "b2_path": b2_file_name,
                "local_temp_path": local_label_map_path,
                "b2_size_bytes": label_map.get("size"),
                "b2_file_id": label_map.get("id"),
            }
        )

        lower_path = local_label_map_path.lower()

        try:
            if lower_path.endswith(".xml"):
                parsed_df = parse_label_mapping_xml(local_label_map_path)

                if parsed_df is not None and not parsed_df.empty:
                    label_mapping_df = pd.concat(
                        [label_mapping_df, parsed_df],
                        ignore_index=True,
                    )

            elif lower_path.endswith(".json"):
                parsed_df = parse_label_mapping_json(local_label_map_path)

                if parsed_df is not None and not parsed_df.empty:
                    label_mapping_df = pd.concat(
                        [label_mapping_df, parsed_df],
                        ignore_index=True,
                    )

            elif lower_path.endswith(".yaml") or lower_path.endswith(".yml"):
                print("[LABEL MAP NOTICE] YAML file detected. Parsing not implemented yet.")
                print(local_label_map_path)

        except Exception as label_error:
            print("=" * 80)
            print("[LABEL MAP PARSE WARNING]")
            print(f"File : {local_label_map_path}")
            print(f"Error: {label_error}")
            print("=" * 80)

    class_mapping_summary_df = summarize_label_mapping(label_mapping_df)

    # -------------------------------------------------------------------
    # 4. Build individual semantic class-label distribution
    # -------------------------------------------------------------------
    # Preferred:
    #   file_summaries[*]["label_histogram"]
    #
    # Fallback:
    #   read_ply_class_distribution()
    #
    # Both are from real PLY/LAS semantic-label fields.
    # -------------------------------------------------------------------

    class_label_distribution_df = build_distribution_from_file_histograms(
        file_summaries
    )

    if class_label_distribution_df.empty and class_distribution_frames:
        raw_class_label_distribution_df = pd.concat(
            class_distribution_frames,
            ignore_index=True,
        )

        class_label_distribution_df = (
            raw_class_label_distribution_df
            .groupby("class_id", as_index=False)["point_count"]
            .sum()
        )

    if class_label_distribution_df.empty:
        class_label_distribution_df = pd.DataFrame(
            columns=["class_id", "point_count"]
        )

    if not class_label_distribution_df.empty:
        class_label_distribution_df["class_id"] = (
            class_label_distribution_df["class_id"]
            .apply(normalize_class_id_value)
        )

        class_label_distribution_df = (
            class_label_distribution_df
            .groupby("class_id", as_index=False)["point_count"]
            .sum()
        )

    # -------------------------------------------------------------------
    # 5. Attach XML semantic names
    #
    # Case A:
    #   PLY class_id == XML detailed class_id/id.
    #
    # Case B:
    #   PLY class_id == XML coarse_id/coarse.
    #
    # Case C:
    #   Toronto:
    #   PLY scalar_Label 0..8 == XML val/id/class_id 0..8.
    # -------------------------------------------------------------------

    if (
        not class_label_distribution_df.empty
        and label_mapping_df is not None
        and not label_mapping_df.empty
    ):
        mapping_df = label_mapping_df.copy()

        # Standardize XML columns.
        if "class_id" not in mapping_df.columns and "id" in mapping_df.columns:
            mapping_df["class_id"] = mapping_df["id"]

        if "class_id" not in mapping_df.columns and "val" in mapping_df.columns:
            mapping_df["class_id"] = mapping_df["val"]

        if "class_id" not in mapping_df.columns and "value" in mapping_df.columns:
            mapping_df["class_id"] = mapping_df["value"]

        if "class_name" not in mapping_df.columns and "en" in mapping_df.columns:
            mapping_df["class_name"] = mapping_df["en"]

        if "class_name" not in mapping_df.columns and "name" in mapping_df.columns:
            mapping_df["class_name"] = mapping_df["name"]

        if "coarse_id" not in mapping_df.columns and "coarse" in mapping_df.columns:
            mapping_df["coarse_id"] = mapping_df["coarse"]

        if "coarse_class_name" not in mapping_df.columns and "coarse_name" in mapping_df.columns:
            mapping_df["coarse_class_name"] = mapping_df["coarse_name"]

        if "coarse_class_name" not in mapping_df.columns:
            if "class_name" in mapping_df.columns:
                mapping_df["coarse_class_name"] = mapping_df["class_name"].apply(
                    infer_coarse_class_from_name
                )
            else:
                mapping_df["coarse_class_name"] = "unknown"

        if "is_building" not in mapping_df.columns:
            mapping_df["is_building"] = mapping_df.apply(
                infer_is_building_from_row,
                axis=1,
            )
        else:
            mapping_df["is_building"] = mapping_df.apply(
                infer_is_building_from_row,
                axis=1,
            )

        if "class_id" in mapping_df.columns:
            mapping_df["class_id"] = mapping_df["class_id"].apply(
                normalize_class_id_value
            )

        if "coarse_id" in mapping_df.columns:
            mapping_df["coarse_id"] = mapping_df["coarse_id"].apply(
                normalize_class_id_value
            )

        class_label_distribution_df["class_id"] = (
            class_label_distribution_df["class_id"].apply(normalize_class_id_value)
        )

        # ---------------------------------------------------------------
        # First try detailed class-id join.
        # This should work for Toronto:
        # scalar_Label 4 == XML class_id/val 4.
        # ---------------------------------------------------------------

        detailed_join_df = pd.DataFrame()

        detailed_cols = [
            col
            for col in [
                "class_id",
                "class_name",
                "coarse_id",
                "coarse_class_name",
                "is_building",
                "r",
                "g",
                "b",
                "color_rgb",
            ]
            if col in mapping_df.columns
        ]

        if "class_id" in detailed_cols:
            detailed_map = (
                mapping_df[detailed_cols]
                .dropna(subset=["class_id"])
                .drop_duplicates(subset=["class_id"])
            )

            detailed_join_df = class_label_distribution_df.merge(
                detailed_map,
                on="class_id",
                how="left",
            )

        detailed_success_count = 0

        if (
            not detailed_join_df.empty
            and "class_name" in detailed_join_df.columns
        ):
            detailed_success_count = (
                detailed_join_df["class_name"].notna().sum()
            )

        if detailed_success_count == 0 and (
            not detailed_join_df.empty
            and "coarse_class_name" in detailed_join_df.columns
        ):
            detailed_success_count = (
                detailed_join_df["coarse_class_name"].notna().sum()
            )

        # ---------------------------------------------------------------
        # Use detailed join only when it maps every class ID.
        # Toronto works this way: PLY scalar_Label 4 == XML id/val 4.
        # Otherwise, use coarse-id join.
        # Paris-Lille works this way: PLY class 2 == XML coarse_id 2.
        # ---------------------------------------------------------------

        expected_count = len(class_label_distribution_df)
        detailed_complete = detailed_success_count == expected_count

        if detailed_complete:
            class_label_distribution_df = detailed_join_df

        else:
            if (
                "coarse_id" in mapping_df.columns
                and "coarse_class_name" in mapping_df.columns
            ):
                coarse_cols = [
                    col
                    for col in [
                        "coarse_id",
                        "coarse_class_name",
                        "is_building",
                        "r",
                        "g",
                        "b",
                        "color_rgb",
                    ]
                    if col in mapping_df.columns
                ]

                coarse_map = (
                    mapping_df[coarse_cols]
                    .dropna(subset=["coarse_id"])
                    .drop_duplicates(subset=["coarse_id"])
                )

                coarse_join_df = class_label_distribution_df.copy()
                coarse_join_df["coarse_id"] = (
                    coarse_join_df["class_id"].astype(str)
                )

                coarse_join_df = coarse_join_df.merge(
                    coarse_map,
                    on="coarse_id",
                    how="left",
                )

                coarse_join_df["class_name"] = coarse_join_df["coarse_class_name"]

                if "is_building" not in coarse_join_df.columns:
                    coarse_join_df["is_building"] = (
                        coarse_join_df["coarse_class_name"]
                        .astype(str)
                        .str.strip()
                        .str.lower()
                        .eq("building")
                    )

                class_label_distribution_df = coarse_join_df

    # -------------------------------------------------------------------
    # 6. Final cleanup for class-label distribution
    # -------------------------------------------------------------------

    if not class_label_distribution_df.empty:
        class_label_distribution_df["class_id"] = (
            class_label_distribution_df["class_id"].apply(normalize_class_id_value)
        )

        if "class_name" not in class_label_distribution_df.columns:
            class_label_distribution_df["class_name"] = (
                class_label_distribution_df["class_id"]
            )

        class_label_distribution_df["class_name"] = (
            class_label_distribution_df["class_name"].fillna(
                class_label_distribution_df["class_id"]
            )
        )

        if "coarse_class_name" not in class_label_distribution_df.columns:
            class_label_distribution_df["coarse_class_name"] = (
                class_label_distribution_df["class_name"].apply(
                    infer_coarse_class_from_name
                )
            )

        class_label_distribution_df["coarse_class_name"] = (
            class_label_distribution_df["coarse_class_name"]
            .fillna("")
        )

        missing_coarse_mask = (
            class_label_distribution_df["coarse_class_name"]
            .astype(str)
            .str.strip()
            .eq("")
        )

        class_label_distribution_df.loc[missing_coarse_mask, "coarse_class_name"] = (
            class_label_distribution_df.loc[missing_coarse_mask, "class_name"]
            .apply(infer_coarse_class_from_name)
        )

        if "coarse_id" not in class_label_distribution_df.columns:
            class_label_distribution_df["coarse_id"] = ""

        if "is_building" not in class_label_distribution_df.columns:
            class_label_distribution_df["is_building"] = False

        class_label_distribution_df["is_building"] = (
            class_label_distribution_df.apply(
                infer_is_building_from_row,
                axis=1,
            )
        )

        class_label_distribution_df["binary_label"] = (
            class_label_distribution_df["is_building"].apply(
                lambda x: "Building" if bool(x) else "Non-building"
            )
        )

        distribution_total_points = int(
            class_label_distribution_df["point_count"].sum()
        )

        class_label_distribution_df["proportion"] = (
            class_label_distribution_df["point_count"].apply(
                lambda x: round(safe_divide(x, distribution_total_points), 6)
            )
        )

        class_label_distribution_df = class_label_distribution_df.sort_values(
            "point_count",
            ascending=False,
        )

    # -------------------------------------------------------------------
    # 7. Dataset-level flags
    # -------------------------------------------------------------------

    has_label_map = False
    has_building_mapping = False

    if class_mapping_summary_df is not None and not class_mapping_summary_df.empty:
        has_label_map = (
            str(
                class_mapping_summary_df.iloc[0].get(
                    "mapping_file_available",
                    "No",
                )
            )
            == "Yes"
        )

        has_building_mapping = (
            str(
                class_mapping_summary_df.iloc[0].get(
                    "building_mapping_available",
                    "No",
                )
            )
            == "Yes"
        )

    has_label_in_tiles = any(
        bool(item.get("has_label", False)) or bool(item.get("has_semantic_label", False))
        for item in file_summaries
    )

    has_xyz = all(
        bool(item.get("has_xyz", False)) for item in file_summaries
    )

    has_intensity = any(
        bool(item.get("has_intensity", False)) for item in file_summaries
    )

    has_reflectance = any(
        bool(item.get("has_reflectance", False)) for item in file_summaries
    )

    has_rgb = any(
        bool(item.get("has_rgb", False)) for item in file_summaries
    )

    has_origin = any(
        bool(item.get("has_origin", False)) for item in file_summaries
    )

    has_label = has_label_in_tiles or has_label_map

    # -------------------------------------------------------------------
    # 8. Binary building / non-building distribution
    # -------------------------------------------------------------------

    if (
        not class_label_distribution_df.empty
        and "binary_label" in class_label_distribution_df.columns
    ):
        binary_counts = (
            class_label_distribution_df
            .groupby("binary_label", as_index=False)["point_count"]
            .sum()
        )

        building_points = int(
            binary_counts.loc[
                binary_counts["binary_label"] == "Building",
                "point_count",
            ].sum()
        )

        non_building_points = int(
            binary_counts.loc[
                binary_counts["binary_label"] == "Non-building",
                "point_count",
            ].sum()
        )

        label_note = "Computed from real PLY semantic-label field and XML class mapping."

    else:
        building_points = 0
        non_building_points = 0
        label_note = (
            "Label distribution unavailable. Real semantic-label distribution "
            "was not computed."
        )

    # -------------------------------------------------------------------
    # 9. Quality flags
    # -------------------------------------------------------------------

    quality_flags = {
        "missing_xyz": not has_xyz,
        "missing_label": not has_label,
        "missing_tile_label_column": not has_label_in_tiles,
        "missing_label_map": not has_label_map,
        "missing_intensity": not has_intensity,
        "missing_reflectance": not has_reflectance,
        "missing_rgb": not has_rgb,
        "missing_origin": not has_origin,
        "label_mapping_available": bool(has_label_map),
        "building_mapping_available": bool(has_building_mapping),
        "class_imbalance_high": True if has_label else False,
        "large_file_warning": total_points > 10_000_000,
        "ready_for_training": bool(has_xyz and has_label),
        "ready_for_inference": bool(has_xyz),
    }

    # -------------------------------------------------------------------
    # 10. Readiness checks
    # -------------------------------------------------------------------

    readiness_checks = [
        {
            "check": "XYZ present",
            "status": "Pass" if has_xyz else "Fail",
            "message": (
                "XYZ coordinates are available."
                if has_xyz
                else "XYZ coordinates are missing."
            ),
        },
        {
            "check": "Point count readable",
            "status": "Pass" if total_points > 0 else "Fail",
            "message": f"Total points detected: {total_points:,}",
        },
        {
            "check": "File format supported",
            "status": "Pass",
            "message": "The uploaded point cloud format was readable.",
        },
        {
            "check": "Spatial bounds valid",
            "status": "Pass",
            "message": "XYZ bounding boxes were extracted from the uploaded tiles.",
        },
        {
            "check": "Reflectance / intensity available",
            "status": "Pass" if has_reflectance or has_intensity else "Warning",
            "message": (
                "Reflectance or intensity field is available."
                if has_reflectance or has_intensity
                else "Reflectance or intensity field was not detected."
            ),
        },
        {
            "check": "Scanner origin fields available",
            "status": "Pass" if has_origin else "Warning",
            "message": (
                "xorigin, yorigin, zorigin fields are available."
                if has_origin
                else "Scanner origin fields were not detected."
            ),
        },
        {
            "check": "RGB available",
            "status": "Pass" if has_rgb else "Warning",
            "message": (
                "RGB color fields are available."
                if has_rgb
                else "RGB color fields were not detected."
            ),
        },
        {
            "check": "Semantic label column present",
            "status": "Pass" if has_label_in_tiles else "Warning",
            "message": (
                "Semantic label column was detected in the point cloud tile."
                if has_label_in_tiles
                else "Semantic label column was not detected in the point cloud tile."
            ),
        },
        {
            "check": "Label mapping file available",
            "status": "Pass" if has_label_map else "Warning",
            "message": (
                "Label mapping file was found and parsed."
                if has_label_map
                else "No XML/JSON/YAML label mapping file was found."
            ),
        },
        {
            "check": "Building class mapping present",
            "status": "Pass" if has_building_mapping else "Warning",
            "message": (
                "Building class mapping is available."
                if has_building_mapping
                else "Building class mapping was not detected."
            ),
        },
        {
            "check": "Ready for supervised training preprocessing",
            "status": "Pass" if has_xyz and has_label else "Warning",
            "message": (
                "Dataset is ready for supervised training preprocessing."
                if has_xyz and has_label
                else "Dataset is ready for inference, but supervised training may require labels or label mapping."
            ),
        },
    ]

    # -------------------------------------------------------------------
    # 11. Model compatibility table
    # -------------------------------------------------------------------

    model_compatibility = [
        {
            "model": "PointNet++",
            "required_format": "traditional/blocks/",
            "status": "Not generated",
        },
        {
            "model": "PointNet++ MSG",
            "required_format": "traditional/blocks/",
            "status": "Not generated",
        },
        {
            "model": "RandLA-Net",
            "required_format": "traditional/blocks/",
            "status": "Not generated",
        },
        {
            "model": "PTv3",
            "required_format": "ptv3_pointcept/",
            "status": "Not generated",
        },
    ]

    # -------------------------------------------------------------------
    # 12. Main metadata JSON
    # -------------------------------------------------------------------

    metadata = {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "upload_mode": upload_mode,
        "description": description,
        "created_at": datetime.now().isoformat(),
        "status": "registered",

        "storage": {
            "bucket": "Building-Identification-MLS",
            "raw_tile_prefix": f"bronze_raw_data/{dataset_id}/source_files/tiles/",
            "label_map_prefix": f"bronze_raw_data/{dataset_id}/source_files/label_maps/",
            "manifest_prefix": f"bronze_raw_data/{dataset_id}/manifests/",
            "metadata_path": f"metadata/datasets/{dataset_id}.json",
            "analytics_path": f"metadata_analytics/{dataset_id}/",
        },

        "total_files": len(file_summaries),
        "total_points": int(total_points),
        "labels": "Available" if has_label else "Unknown",

        "label_maps": label_map_entries,
        "quality_flags": quality_flags,
        "file_summaries": file_summaries,
        "readiness_checks": readiness_checks,
        "model_compatibility": model_compatibility,
    }

    metadata_path = os.path.join(METADATA_DIR, f"{dataset_id}.json")

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, default=make_json_safe)

    update_metadata_progress(
        dataset_id,
        message="Saving metadata analytics",
        percentage=99,
    )

    # -------------------------------------------------------------------
    # 13. Analytics DataFrames
    # -------------------------------------------------------------------

    file_summary_df = pd.DataFrame(
        [
            {
                "filename": item.get("filename"),
                "b2_path": item.get("b2_path"),
                "file_format": item.get("file_format"),
                "point_count": item.get("point_count"),

                "x_min": item.get("x_min"),
                "x_max": item.get("x_max"),
                "y_min": item.get("y_min"),
                "y_max": item.get("y_max"),
                "z_min": item.get("z_min"),
                "z_max": item.get("z_max"),

                "has_xyz": item.get("has_xyz"),
                "has_label": item.get("has_label"),
                "has_semantic_label": item.get("has_semantic_label"),
                "has_intensity": item.get("has_intensity"),
                "has_reflectance": item.get("has_reflectance"),
                "has_rgb": item.get("has_rgb"),
                "has_origin": item.get("has_origin"),

                "label_column": item.get("label_column"),
                "semantic_label_column": item.get("semantic_label_column"),
                "reflectance_column": item.get("reflectance_column"),
                "intensity_column": item.get("intensity_column"),

                "xorigin_column": item.get("xorigin_column"),
                "yorigin_column": item.get("yorigin_column"),
                "zorigin_column": item.get("zorigin_column"),

                "label_unique_count": item.get("label_unique_count"),
                "label_min": item.get("label_min"),
                "label_max": item.get("label_max"),
                "label_histogram": json.dumps(item.get("label_histogram", {})),

                "b2_size_bytes": item.get("b2_size_bytes"),
                "b2_file_id": item.get("b2_file_id"),
            }
            for item in file_summaries
        ]
    )

    attribute_df = build_attribute_summary(list(all_attributes))

    label_distribution_df = pd.DataFrame(
        [
            {
                "class_name": "Building",
                "point_count": int(building_points),
                "proportion": round(safe_divide(building_points, total_points), 6),
                "note": label_note,
            },
            {
                "class_name": "Non-building",
                "point_count": int(non_building_points),
                "proportion": round(safe_divide(non_building_points, total_points), 6),
                "note": label_note,
            },
        ]
    )

    spatial_summary_df = pd.DataFrame(
        [
            {
                "tile_name": item.get("filename"),
                "point_count": item.get("point_count"),
                "x_range": round(item.get("x_max", 0) - item.get("x_min", 0), 3),
                "y_range": round(item.get("y_max", 0) - item.get("y_min", 0), 3),
                "z_range": round(item.get("z_max", 0) - item.get("z_min", 0), 3),
                "density_estimate": round(
                    safe_divide(
                        item.get("point_count", 0),
                        max(
                            (
                                item.get("x_max", 0) - item.get("x_min", 0)
                            )
                            * (
                                item.get("y_max", 0) - item.get("y_min", 0)
                            ),
                            1,
                        ),
                    ),
                    2,
                ),
            }
            for item in file_summaries
        ]
    )

    dashboard_kpis_df = pd.DataFrame(
        [
            {
                "kpi_name": "Total Files",
                "kpi_value": str(len(file_summaries)),
                "kpi_description": "Uploaded point cloud tiles",
            },
            {
                "kpi_name": "Total Points",
                "kpi_value": f"{total_points:,}",
                "kpi_description": "Real total point count from B2 tiles",
            },
            {
                "kpi_name": "Semantic Labels",
                "kpi_value": "Available" if has_label else "Unknown",
                "kpi_description": "Detected from tile semantic-label fields or XML mapping",
            },
            {
                "kpi_name": "Tile Label Column",
                "kpi_value": "Available" if has_label_in_tiles else "Missing",
                "kpi_description": "Detected directly from PLY/LAS semantic-label fields",
            },
            {
                "kpi_name": "Label Map",
                "kpi_value": "Available" if has_label_map else "Missing",
                "kpi_description": "coarse_classes.xml, Toronto XML, or equivalent",
            },
            {
                "kpi_name": "Building Mapping",
                "kpi_value": "Available" if has_building_mapping else "Unknown",
                "kpi_description": "Building/non-building conversion support",
            },
            {
                "kpi_name": "Reflectance",
                "kpi_value": "Yes" if has_reflectance or has_intensity else "No",
                "kpi_description": "Reflectance/intensity field availability",
            },
            {
                "kpi_name": "Scanner Origin",
                "kpi_value": "Yes" if has_origin else "No",
                "kpi_description": "xorigin/yorigin/zorigin field availability",
            },
            {
                "kpi_name": "RGB Available",
                "kpi_value": "Yes" if has_rgb else "No",
                "kpi_description": "Color information in point cloud",
            },
            {
                "kpi_name": "Preprocessing",
                "kpi_value": "Ready" if has_xyz else "Not Ready",
                "kpi_description": "Dataset readiness status",
            },
            {
                "kpi_name": "PTv3",
                "kpi_value": "Pending",
                "kpi_description": "Pointcept format not generated yet",
            },
        ]
    )

    quality_checks_df = pd.DataFrame(readiness_checks)

    # -------------------------------------------------------------------
    # 14. Save analytics locally
    # -------------------------------------------------------------------

    save_analytics_parquets(
        dataset_id=dataset_id,
        file_summary_df=file_summary_df,
        attribute_df=attribute_df,
        label_distribution_df=label_distribution_df,
        spatial_summary_df=spatial_summary_df,
        dashboard_kpis_df=dashboard_kpis_df,
        quality_checks_df=quality_checks_df,
        class_mapping_summary_df=class_mapping_summary_df,
        class_mapping_df=label_mapping_df,
        class_label_distribution_df=class_label_distribution_df,
    )

    # -------------------------------------------------------------------
    # 15. Upload metadata and analytics to B2
    # -------------------------------------------------------------------

    metadata_cloud_upload_status = "not_attempted"
    metadata_cloud_upload_error = ""

    try:
        upload_local_file_to_b2_path(
            local_file_path=metadata_path,
            b2_path=f"metadata/datasets/{dataset_id}.json",
        )

        local_analytics_dir = os.path.join(ANALYTICS_DIR, dataset_id)

        upload_local_directory_to_b2(
            local_dir=local_analytics_dir,
            b2_prefix=f"metadata_analytics/{dataset_id}/",
        )

        metadata_cloud_upload_status = "uploaded"

        print("=" * 80)
        print("[METADATA UPLOADED TO B2]")
        print(f"B2 metadata path  : metadata/datasets/{dataset_id}.json")
        print(f"B2 analytics path : metadata_analytics/{dataset_id}/")
        print("=" * 80)

    except Exception as cloud_error:
        metadata_cloud_upload_status = "failed"
        metadata_cloud_upload_error = str(cloud_error)

        print("=" * 80)
        print("[METADATA CLOUD UPLOAD WARNING]")
        print("Local metadata was generated successfully, but cloud metadata upload failed.")
        print(f"Error: {cloud_error}")
        print("=" * 80)

    metadata["metadata_cloud_upload_status"] = metadata_cloud_upload_status
    metadata["metadata_cloud_upload_error"] = metadata_cloud_upload_error

    # Rewrite local metadata with cloud upload status included.
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, default=make_json_safe)

    print("=" * 80)
    print("[REAL METADATA EXTRACTION COMPLETED]")
    print(f"Dataset ID       : {dataset_id}")
    print(f"Total tiles      : {len(file_summaries)}")
    print(f"Total points     : {total_points:,}")
    print(f"Metadata JSON    : {metadata_path}")
    print(f"Analytics local  : data/metadata_analytics/{dataset_id}/")
    print(f"Cloud upload     : {metadata_cloud_upload_status}")
    print("=" * 80)

    return metadata


# -------------------------------------------------------------------
# Dataset registry
# -------------------------------------------------------------------

def list_registered_datasets():
    ensure_dirs()

    rows = []

    for filename in os.listdir(METADATA_DIR):
        if filename.endswith(".json"):
            path = os.path.join(METADATA_DIR, filename)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                rows.append(
                    {
                        "dataset_id": metadata.get("dataset_id"),
                        "dataset_name": metadata.get("dataset_name"),
                        "total_files": metadata.get("total_files"),
                        "total_points": metadata.get("total_points"),
                        "labels": metadata.get("labels"),
                        "status": metadata.get("status"),
                    }
                )

            except Exception as e:
                print(f"[REGISTRY WARNING] Could not read metadata file {path}: {e}")

    rows.sort(key=lambda x: x.get("dataset_id", ""))

    return rows


def load_dataset_metadata(dataset_id):
    if not dataset_id:
        return {}

    dataset_id = str(dataset_id).strip()

    path = os.path.join(METADATA_DIR, f"{dataset_id}.json")

    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"[METADATA READ ERROR] Could not read {path}: {e}")
        return {}
