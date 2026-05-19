import os
import json
from pathlib import Path
from typing import Optional

from services.compat import disable_incompatible_pandas_accelerators

disable_incompatible_pandas_accelerators()

import pandas as pd


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANALYTICS_DIR = str(_PROJECT_ROOT / "data" / "metadata_analytics")


def get_dataset_analytics_dir(dataset_id: str) -> str:
    if not dataset_id:
        raise ValueError("dataset_id cannot be empty.")

    dataset_id = str(dataset_id).strip()

    path = os.path.join(ANALYTICS_DIR, dataset_id)
    os.makedirs(path, exist_ok=True)

    return path


def _make_cell_safe(value):
    """
    Convert nested values into JSON strings so Parquet writing does not fail.

    Example:
        label_histogram dict -> JSON string
    """

    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, default=str)

    return value


def clean_for_parquet(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """
    Clean DataFrame before saving to Parquet.

    Rules:
    - dict/list/tuple/set values are converted to JSON strings.
    - known numeric columns are preserved as numeric.
    - object/category columns are converted to pandas string dtype.
    """

    if df is None:
        return pd.DataFrame()

    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)

    if df.empty:
        return df.copy()

    df = df.copy()

    for col in df.columns:
        df[col] = df[col].map(_make_cell_safe)

    numeric_columns = {
        "point_count",
        "total_points",
        "proportion",
        "x_min",
        "x_max",
        "y_min",
        "y_max",
        "z_min",
        "z_max",
        "x_range",
        "y_range",
        "z_range",
        "density_estimate",
        "label_unique_count",
        "label_min",
        "label_max",
        "intensity_min",
        "intensity_max",
        "reflectance_min",
        "reflectance_max",
        "b2_size_bytes",
        "available_numeric",
        "r",
        "g",
        "b",
    }

    for col in df.columns:
        if col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif df[col].dtype == "object" or str(df[col].dtype) == "category":
            df[col] = df[col].astype("string").fillna("")

    return df


def save_single_parquet(df: pd.DataFrame, output_path: str) -> None:
    output_path = str(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    cleaned_df = clean_for_parquet(df)

    cleaned_df.to_parquet(
        output_path,
        index=False,
        engine="pyarrow",
    )

    print(f"[PARQUET SAVED] {output_path}")


def save_analytics_parquets(
    dataset_id: str,
    file_summary_df: pd.DataFrame,
    attribute_df: pd.DataFrame,
    label_distribution_df: pd.DataFrame,
    spatial_summary_df: pd.DataFrame,
    dashboard_kpis_df: pd.DataFrame,
    quality_checks_df: pd.DataFrame,
    class_mapping_summary_df: Optional[pd.DataFrame] = None,
    class_mapping_df: Optional[pd.DataFrame] = None,
    class_label_distribution_df: Optional[pd.DataFrame] = None,
) -> None:
    dataset_dir = get_dataset_analytics_dir(dataset_id)

    save_single_parquet(
        file_summary_df,
        os.path.join(dataset_dir, "file_summary.parquet"),
    )

    save_single_parquet(
        attribute_df,
        os.path.join(dataset_dir, "attribute_summary.parquet"),
    )

    save_single_parquet(
        label_distribution_df,
        os.path.join(dataset_dir, "label_distribution.parquet"),
    )

    if class_label_distribution_df is not None:
        save_single_parquet(
            class_label_distribution_df,
            os.path.join(dataset_dir, "class_label_distribution.parquet"),
        )
    else:
        save_single_parquet(
            pd.DataFrame(),
            os.path.join(dataset_dir, "class_label_distribution.parquet"),
        )

    save_single_parquet(
        spatial_summary_df,
        os.path.join(dataset_dir, "spatial_summary.parquet"),
    )

    save_single_parquet(
        dashboard_kpis_df,
        os.path.join(dataset_dir, "dashboard_kpis.parquet"),
    )

    save_single_parquet(
        quality_checks_df,
        os.path.join(dataset_dir, "quality_checks.parquet"),
    )

    if class_mapping_summary_df is not None:
        save_single_parquet(
            class_mapping_summary_df,
            os.path.join(dataset_dir, "class_mapping_summary.parquet"),
        )
    else:
        save_single_parquet(
            pd.DataFrame(),
            os.path.join(dataset_dir, "class_mapping_summary.parquet"),
        )

    if class_mapping_df is not None:
        save_single_parquet(
            class_mapping_df,
            os.path.join(dataset_dir, "class_mapping.parquet"),
        )
    else:
        save_single_parquet(
            pd.DataFrame(),
            os.path.join(dataset_dir, "class_mapping.parquet"),
        )


def safe_read_parquet(path: str) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()

    path_obj = Path(path)

    if not path_obj.is_absolute():
        path_obj = (_PROJECT_ROOT / path_obj).resolve()

    if not path_obj.exists():
        print(f"[PARQUET NOT FOUND] {path_obj}")
        return pd.DataFrame()

    try:
        df = pd.read_parquet(path_obj, engine="pyarrow")
        print(f"[PARQUET READ OK] {path_obj} -> {len(df)} rows")
        return df
    except Exception as e:
        print(f"[PARQUET READ ERROR] Could not read {path_obj}: {e}")
        return pd.DataFrame()


def _analytics_path(dataset_id: str, filename: str) -> str:
    dataset_id = str(dataset_id).strip() if dataset_id else ""
    return os.path.join(ANALYTICS_DIR, dataset_id, filename)


def load_file_summary(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "file_summary.parquet")
    )


def load_dashboard_kpis(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "dashboard_kpis.parquet")
    )


def load_attribute_summary(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "attribute_summary.parquet")
    )


def load_label_distribution(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "label_distribution.parquet")
    )


def load_class_label_distribution(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "class_label_distribution.parquet")
    )


def load_spatial_summary(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "spatial_summary.parquet")
    )


def load_quality_checks(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "quality_checks.parquet")
    )


def load_class_mapping_summary(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "class_mapping_summary.parquet")
    )


def load_class_mapping(dataset_id: str) -> pd.DataFrame:
    return safe_read_parquet(
        _analytics_path(dataset_id, "class_mapping.parquet")
    )
