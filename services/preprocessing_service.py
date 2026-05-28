import base64
import json
import os
from datetime import datetime
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

from services.b2_service import B2_BUCKET_NAME
from services.metadata_service import load_dataset_metadata
from services.mlflow_service import DEFAULT_MLFLOW_TRACKING_URI
from services.b2_paths import b2_prefix


load_dotenv()


PREPROCESSING_SCRIPT_PATH = os.getenv(
    "PREPROCESSING_SCRIPT_PATH",
    "/Users/sanskarsrivastava/Desktop/preprocessing/preprocess_mls_v9_compat.py",
)
REMOTE_SCRIPT_PATH = os.getenv(
    "REMOTE_PREPROCESSING_SCRIPT_PATH",
    "/opt/mls_preprocessing_airflow/preprocess_mls_v9_compat.py",
)
AIRFLOW_DAG_ID = os.getenv("AIRFLOW_PREPROCESSING_DAG_ID", "lidar_preprocessing_pipeline")
AIRFLOW_API_BASE_URL = os.getenv("AIRFLOW_API_BASE_URL", "")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "")
RUN_REQUEST_DIR = Path("data/airflow_preprocessing_requests")
DEFAULT_NUM_SEGMENTS = 20
DEFAULT_TRAIN_SEGMENTS = 14
DEFAULT_VAL_SEGMENTS = 3
DEFAULT_TEST_SEGMENTS = 3


def _utc_run_id(dataset_id, prep_version):
    clean_dataset_id = (dataset_id or "dataset").strip() or "dataset"
    clean_prep_version = (prep_version or "prep_v001").strip() or "prep_v001"
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{clean_dataset_id}_{clean_prep_version}_{timestamp}"


def build_minimal_trigger_conf(dataset_id, mode="train", prep_version=None, run_id=None):
    """Minimal conf sent to the remote Airflow DAG.

    The workstation owns all defaults (voxel size, block size, workers, etc.).
    prep_version is omitted when falsy so the workstation auto-increments from
    the last successful gold run (→ prep_v001 on first run, prep_v002 next, …).
    Only include it explicitly when the caller wants to pin/overwrite a version.
    """
    dataset_id = (dataset_id or "").strip()
    run_id = run_id or _utc_run_id(dataset_id, prep_version or "auto")
    conf = {
        "dataset_id": dataset_id,
        "mode": mode or "train",
        "run_id": run_id,
    }
    if prep_version and prep_version.strip():
        conf["prep_version"] = prep_version.strip()
    return conf


def get_preprocessing_script_info():
    script_path = Path(PREPROCESSING_SCRIPT_PATH)
    exists = script_path.exists()

    return {
        "name": "Production MLS Preprocessing v9",
        "local_reference_path": PREPROCESSING_SCRIPT_PATH,
        "remote_execution_path": REMOTE_SCRIPT_PATH,
        "exists_on_controller": exists,
        "last_modified": (
            datetime.fromtimestamp(script_path.stat().st_mtime).isoformat(timespec="seconds")
            if exists
            else "Not found on this controller"
        ),
        "purpose": (
            "Stages bronze raw MLS tiles, writes the silver conformed cloud, "
            "then builds gold model-ready outputs for PointNet++, RandLA-Net, "
            "and PTv3/Pointcept without running on the Dash controller."
        ),
    }


def build_storage_contract(dataset_id, prep_version, bucket_name=None, run_id=None):
    dataset_id = (dataset_id or "").strip()
    prep_version = (prep_version or "").strip() or "prep_v001"
    run_id = (run_id or "<run_id>").strip() or "<run_id>"
    bucket = bucket_name or B2_BUCKET_NAME

    return {
        "bucket": bucket,
        "airflow_raw_listing_prefix": f"b2://{bucket}/{b2_prefix('bronze_raw_data')}/",
        "raw_tiles": f"b2://{bucket}/{b2_prefix('bronze_raw_data')}/{dataset_id}/source_files/tiles/",
        "label_maps": f"b2://{bucket}/{b2_prefix('bronze_raw_data')}/{dataset_id}/source_files/label_maps/",
        "registry_metadata": f"b2://{bucket}/{b2_prefix('metadata')}/datasets/{dataset_id}.json",
        "analytics": f"b2://{bucket}/{b2_prefix('metadata_analytics')}/{dataset_id}/",
        "silver_output": f"b2://{bucket}/{b2_prefix('silver_preprocessed_data')}/{dataset_id}/{prep_version}/",
        "gold_output": f"b2://{bucket}/{b2_prefix('gold_model_ready_data')}/{dataset_id}/{prep_version}/",
        "preprocessing_logs": f"b2://{bucket}/{b2_prefix('logs')}/{dataset_id}/{run_id}/",
        "preprocessing_metadata": f"b2://{bucket}/{b2_prefix('metadata')}/datasets/{dataset_id}/metadata.json",
    }


def _as_int_label(value):
    try:
        number = float(str(value).strip())
    except Exception:
        return None
    if not number.is_integer():
        return None
    return int(number)


def _raw_label_values(metadata):
    values = set()
    for item in metadata.get("file_summaries") or []:
        for label in (item.get("label_histogram") or {}).keys():
            normalized = _as_int_label(label)
            if normalized is not None:
                values.add(normalized)
    return sorted(values)


def _coerce_label_list(value):
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = text.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = [value]

    labels = []
    for item in parts:
        normalized = _as_int_label(item)
        if normalized is not None and normalized not in labels:
            labels.append(normalized)
    return labels


def _infer_label_field(metadata):
    for item in metadata.get("file_summaries") or []:
        label_field = item.get("semantic_label_column") or item.get("label_column")
        if label_field:
            return label_field
    return "class"


def _infer_building_labels(dataset_id, dataset_name, label_field):
    text = f"{dataset_id} {dataset_name} {label_field}".lower()
    if "toronto" in text or "torronto" in text or label_field == "scalar_Label":
        return [4]
    if "paris" in text or "lille" in text or label_field == "class":
        return [2]
    return [4]


def build_dataset_config(
    dataset_id,
    dataset_name=None,
    label_field=None,
    building_labels=None,
    non_building_labels=None,
    ignore_labels=None,
):
    metadata = load_dataset_metadata(dataset_id)
    label_field = (label_field or "").strip() or _infer_label_field(metadata)
    raw_labels = _raw_label_values(metadata)
    parsed_building_labels = _coerce_label_list(building_labels)
    parsed_non_building_labels = _coerce_label_list(non_building_labels)
    parsed_ignore_labels = _coerce_label_list(ignore_labels)

    if parsed_building_labels is None:
        parsed_building_labels = _infer_building_labels(dataset_id, dataset_name, label_field)

    if parsed_ignore_labels is None:
        parsed_ignore_labels = [0] if 0 in raw_labels or not raw_labels else []

    if parsed_non_building_labels is None:
        parsed_non_building_labels = [
            label
            for label in raw_labels
            if label not in set(parsed_building_labels)
            and label not in set(parsed_ignore_labels)
        ]

    return {
        "dataset_name": (dataset_id or "").strip(),
        "file_glob": ["*.ply", "*.las", "*.laz"],
        "label_field": label_field,
        "building_labels": parsed_building_labels,
        "non_building_labels": parsed_non_building_labels,
        "ignore_labels": parsed_ignore_labels,
        "utm_offset_subtract": True,
        "coord_offset": None,
    }


def build_airflow_conf(
    dataset_id,
    dataset_name,
    mode,
    prep_version,
    output_mode,
    voxel_size,
    voxel_keep_strategy,
    block_size,
    n_points,
    max_blocks_train,
    stride_val_test,
    split_gap_m,
    num_segments,
    train_segments,
    val_segments,
    test_segments,
    min_bldg_ratio,
    randla_overlap,
    ptv3_scene_length,
    num_workers,
    compute_normals,
    include_density,
    save_ply,
    compress_output,
    write_silver=True,
    label_field=None,
    building_labels=None,
    non_building_labels=None,
    ignore_labels=None,
    execution_target=None,
    airflow_queue=None,
    mlflow_tracking_uri=DEFAULT_MLFLOW_TRACKING_URI,
    mlflow_experiment="mls-preprocessing",
    mlflow_run_name=None,
    dvc_remote="b2remote",
    disable_mlflow=False,
    mlflow_log_artifacts=True,
    run_id=None,
):
    dataset_id = (dataset_id or "").strip()
    dataset_name = (dataset_name or dataset_id).strip()
    mode = mode or "train"
    prep_version = (prep_version or "").strip() or "prep_v001"
    run_id = run_id or _utc_run_id(dataset_id, prep_version)
    storage = build_storage_contract(dataset_id, prep_version, run_id=run_id)
    dataset_config = build_dataset_config(
        dataset_id,
        dataset_name,
        label_field=label_field,
        building_labels=building_labels,
        non_building_labels=non_building_labels,
        ignore_labels=ignore_labels,
    )

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "mode": mode,
        "prep_version": prep_version,
        "run_id": run_id,
        "execution_target": execution_target or "any_gpu_worker",
        "airflow_queue": airflow_queue or "system1",
        "controller_runs_script_locally": False,
        "dag_id": AIRFLOW_DAG_ID,
        "script": get_preprocessing_script_info(),
        "storage": storage,
        "dataset_config": dataset_config,
        "script_args": {
            "use_b2": True,
            "mode": mode,
            "custom_dataset": f"/airflow/staging/{dataset_id}/dataset_config.json",
            "data_dir": f"/airflow/staging/{dataset_id}/raw",
            "output_dir": f"/airflow/staging/{dataset_id}/preprocessed",
            "output_mode": output_mode,
            "b2_raw_bucket": storage["bucket"],
            "b2_raw_prefix": b2_prefix("bronze_raw_data"),
            "b2_output_bucket": storage["bucket"],
            "b2_output_prefix": f"{b2_prefix('gold_model_ready_data')}/{dataset_id}/{prep_version}",
            "b2_silver_bucket": storage["bucket"],
            "b2_silver_prefix": f"{b2_prefix('silver_preprocessed_data')}/{dataset_id}/{prep_version}",
            "b2_logs_bucket": storage["bucket"],
            "b2_logs_prefix": f"{b2_prefix('logs')}/{dataset_id}/{run_id}",
            "b2_metadata_bucket": storage["bucket"],
            "b2_metadata_prefix": f"{b2_prefix('metadata')}/datasets/{dataset_id}",
            "prep_version": prep_version,
            "run_id": run_id,
            "cleanup_stage": True,
            "voxel_size": float(voxel_size),
            "voxel_keep_strategy": voxel_keep_strategy or "representative",
            "block_size": float(block_size),
            "n_points": int(n_points),
            "max_blocks_train": int(max_blocks_train),
            "stride_val_test": float(stride_val_test),
            "split_gap_m": float(split_gap_m),
            "num_segments": int(num_segments or DEFAULT_NUM_SEGMENTS),
            "train_segments": int(train_segments or DEFAULT_TRAIN_SEGMENTS),
            "val_segments": int(val_segments or DEFAULT_VAL_SEGMENTS),
            "test_segments": int(test_segments or DEFAULT_TEST_SEGMENTS),
            "min_bldg_ratio": float(min_bldg_ratio),
            "randla_overlap": float(randla_overlap),
            "ptv3_scene_length": float(ptv3_scene_length),
            "num_workers": int(num_workers),
            "compute_normals": bool(compute_normals),
            "include_density": bool(include_density),
            "save_ply": bool(save_ply),
            "compress_output": bool(compress_output),
            "write_silver": bool(write_silver),
            "mlflow_tracking_uri": (mlflow_tracking_uri or "").strip() or DEFAULT_MLFLOW_TRACKING_URI,
            "mlflow_experiment": (mlflow_experiment or "").strip() or "mls-preprocessing",
            "mlflow_run_name": (mlflow_run_name or "").strip() or None,
            "dvc_remote": (dvc_remote or "").strip() or "b2remote",
            "disable_mlflow": bool(disable_mlflow),
            "mlflow_no_artifacts": not bool(mlflow_log_artifacts),
        },
    }


def _append_value(command, flag, value):
    if value is not None:
        command.extend([flag, str(value)])


def build_remote_command(conf):
    args = conf["script_args"]
    command = [
        "python",
        conf["script"]["remote_execution_path"],
        "--use_b2",
    ]

    for flag, key in [
        ("--mode", "mode"),
        ("--custom_dataset", "custom_dataset"),
        ("--data_dir", "data_dir"),
        ("--output_dir", "output_dir"),
        ("--output_mode", "output_mode"),
        ("--b2_raw_bucket", "b2_raw_bucket"),
        ("--b2_raw_prefix", "b2_raw_prefix"),
        ("--b2_output_bucket", "b2_output_bucket"),
        ("--b2_output_prefix", "b2_output_prefix"),
        ("--b2_silver_bucket", "b2_silver_bucket"),
        ("--b2_silver_prefix", "b2_silver_prefix"),
        ("--b2_logs_bucket", "b2_logs_bucket"),
        ("--b2_logs_prefix", "b2_logs_prefix"),
        ("--b2_metadata_bucket", "b2_metadata_bucket"),
        ("--b2_metadata_prefix", "b2_metadata_prefix"),
        ("--prep_version", "prep_version"),
        ("--run_id", "run_id"),
        ("--voxel_size", "voxel_size"),
        ("--voxel_keep_strategy", "voxel_keep_strategy"),
        ("--block_size", "block_size"),
        ("--n_points", "n_points"),
        ("--max_blocks_train", "max_blocks_train"),
        ("--stride_val_test", "stride_val_test"),
        ("--split_gap_m", "split_gap_m"),
        ("--num_segments", "num_segments"),
        ("--train_segments", "train_segments"),
        ("--val_segments", "val_segments"),
        ("--test_segments", "test_segments"),
        ("--min_bldg_ratio", "min_bldg_ratio"),
        ("--randla_overlap", "randla_overlap"),
        ("--ptv3_scene_length", "ptv3_scene_length"),
        ("--num_workers", "num_workers"),
        ("--mlflow_tracking_uri", "mlflow_tracking_uri"),
        ("--mlflow_experiment", "mlflow_experiment"),
        ("--mlflow_run_name", "mlflow_run_name"),
        ("--dvc_remote", "dvc_remote"),
    ]:
        _append_value(command, flag, args.get(key))

    for flag in [
        "compute_normals",
        "include_density",
        "save_ply",
        "compress_output",
        "cleanup_stage",
        "disable_mlflow",
        "mlflow_no_artifacts",
    ]:
        if args.get(flag):
            command.append(f"--{flag}")

    if args.get("write_silver") is False:
        command.append("--no-write_silver")

    return command


def persist_airflow_request(conf):
    RUN_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    run_id = conf.get("run_id") or _utc_run_id(conf["dataset_id"], conf["prep_version"])
    dataset_config_path = RUN_REQUEST_DIR / f"{run_id}_dataset_config.json"
    dataset_config_path.write_text(
        json.dumps(conf.get("dataset_config", {}), indent=2),
        encoding="utf-8",
    )
    payload = {
        "dag_run_id": run_id,
        "conf": conf,
        "remote_command": build_remote_command(conf),
        "dataset_config_path": str(dataset_config_path),
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    output_path = RUN_REQUEST_DIR / f"{run_id}.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, str(output_path)


def trigger_airflow_dag(conf):
    if not AIRFLOW_API_BASE_URL:
        raise ValueError("AIRFLOW_API_BASE_URL is not configured, so only a local trigger payload was created.")

    payload, payload_path = persist_airflow_request(conf)
    url = f"{AIRFLOW_API_BASE_URL.rstrip('/')}/api/v1/dags/{AIRFLOW_DAG_ID}/dagRuns"
    body = json.dumps(
        {
            "dag_run_id": payload["dag_run_id"],
            "conf": conf,
        }
    ).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
        token = base64.b64encode(f"{AIRFLOW_USERNAME}:{AIRFLOW_PASSWORD}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    req = request.Request(url, data=body, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=30) as response:
            response_body = response.read().decode("utf-8")
            return {
                "status": "triggered",
                "airflow_url": url,
                "payload_path": payload_path,
                "response": json.loads(response_body) if response_body else {},
            }
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Airflow returned HTTP {exc.code}: {details}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Airflow API: {exc.reason}") from exc
