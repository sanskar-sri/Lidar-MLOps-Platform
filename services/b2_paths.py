import os

B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME", "building-identification-mls-v2")

B2_PREFIXES = {
    "bronze_raw_data": os.getenv("B2_BRONZE_PREFIX", "01_raw_data/bronze_raw_data"),
    "silver_preprocessed_data": os.getenv("B2_SILVER_PREFIX", "02_preprocessing/silver_preprocessed_data"),
    "gold_model_ready_data": os.getenv("B2_GOLD_PREFIX", "02_preprocessing/gold_model_ready_data"),
    "inference_ready_data": os.getenv("B2_INFERENCE_PREFIX", "02_preprocessing/inference_ready_data"),
    "training_runs": os.getenv("B2_TRAINING_RUNS_PREFIX", "03_segmentation/training_runs"),
    "segmentation_outputs": os.getenv("B2_SEGMENTATION_PREFIX", "03_segmentation/segmentation_outputs"),
    "clustered_final_outputs": os.getenv("B2_CLUSTERING_PREFIX", "04_clustering/clustered_final_outputs"),
    "gis_exports": os.getenv("B2_GIS_EXPORTS_PREFIX", "05_applications/gis_exports"),
    "risk_exposure": os.getenv("B2_RISK_EXPOSURE_PREFIX", "05_applications/risk_exposure"),
    "metadata": os.getenv("B2_METADATA_PREFIX", "06_governance/metadata"),
    "metadata_analytics": os.getenv("B2_METADATA_ANALYTICS_PREFIX", "06_governance/metadata_analytics"),
    "benchmark_results": os.getenv("B2_BENCHMARK_PREFIX", "06_governance/benchmark_results"),
    "lineage": os.getenv("B2_LINEAGE_PREFIX", "06_governance/lineage"),
    "qc_reports": os.getenv("B2_QC_REPORTS_PREFIX", "06_governance/qc_reports"),
    "logs": os.getenv("B2_LOGS_PREFIX", "06_governance/logs"),
    "rerun_outputs": os.getenv("B2_RERUN_PREFIX", "06_governance/rerun_outputs"),
}


def b2_prefix(name: str) -> str:
    try:
        return B2_PREFIXES[name].strip("/")
    except KeyError as exc:
        raise KeyError(f"Unknown B2 prefix name: {name}") from exc


def dataset_metadata_key(dataset_id: str) -> str:
    return f"{b2_prefix('metadata')}/datasets/{dataset_id}/metadata.json"


def bronze_tiles_prefix(dataset_id: str) -> str:
    return f"{b2_prefix('bronze_raw_data')}/{dataset_id}/source_files/tiles"


def bronze_label_maps_prefix(dataset_id: str) -> str:
    return f"{b2_prefix('bronze_raw_data')}/{dataset_id}/source_files/label_maps"


def bronze_manifest_prefix(dataset_id: str) -> str:
    return f"{b2_prefix('bronze_raw_data')}/{dataset_id}/manifests"


def silver_prefix(dataset_id: str, prep_version: str) -> str:
    return f"{b2_prefix('silver_preprocessed_data')}/{dataset_id}/{prep_version}"


def gold_prefix(dataset_id: str, prep_version: str) -> str:
    return f"{b2_prefix('gold_model_ready_data')}/{dataset_id}/{prep_version}"


def training_run_prefix(dataset_id: str, prep_version: str, model_name: str, run_id: str) -> str:
    return f"{b2_prefix('training_runs')}/{dataset_id}/{prep_version}/{model_name}/{run_id}"


def segmentation_prefix(dataset_id: str, prep_version: str, model_name: str, run_id: str) -> str:
    return f"{b2_prefix('segmentation_outputs')}/{dataset_id}/{prep_version}/{model_name}/{run_id}"


def clustered_output_prefix(dataset_id: str, prep_version: str, model_name: str, run_id: str) -> str:
    return f"{b2_prefix('clustered_final_outputs')}/{dataset_id}/{prep_version}/{model_name}/{run_id}"


def gis_exports_prefix(dataset_id: str, prep_version: str, model_name: str, run_id: str) -> str:
    return f"{b2_prefix('gis_exports')}/{dataset_id}/{prep_version}/{model_name}/{run_id}"


def rerun_outputs_prefix(dataset_id: str, prep_version: str, model_name: str, run_id: str) -> str:
    return f"{b2_prefix('rerun_outputs')}/{dataset_id}/{prep_version}/{model_name}/{run_id}"
