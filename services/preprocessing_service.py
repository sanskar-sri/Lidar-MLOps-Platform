import base64
import json
import os
from datetime import datetime
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

from services.b2_service import B2_BUCKET_NAME


load_dotenv()


PREPROCESSING_SCRIPT_PATH = os.getenv(
    "PREPROCESSING_SCRIPT_PATH",
    "/Users/sanskarsrivastava/Desktop/TEST/mls_preprocessing_airflow/preprocess_mls_v9_compat.py",
)
REMOTE_SCRIPT_PATH = os.getenv(
    "REMOTE_PREPROCESSING_SCRIPT_PATH",
    "/opt/mls_preprocessing_airflow/preprocess_mls_v9_compat.py",
)
AIRFLOW_DAG_ID = os.getenv("AIRFLOW_PREPROCESSING_DAG_ID", "mls_preprocessing_v8")
AIRFLOW_API_BASE_URL = os.getenv("AIRFLOW_API_BASE_URL", "")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "")
RUN_REQUEST_DIR = Path("data/airflow_preprocessing_requests")


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
            "Builds train/val/test or inference-ready point-cloud blocks for "
            "PointNet++, RandLA-Net, and PTv3/Pointcept without running on the Dash controller."
        ),
    }


def build_storage_contract(dataset_id, prep_version, bucket_name=None):
    dataset_id = (dataset_id or "").strip()
    prep_version = (prep_version or "").strip() or "prep_v001"
    bucket = bucket_name or B2_BUCKET_NAME

    return {
        "bucket": bucket,
        "raw_tiles": f"b2://{bucket}/bronze_raw_data/{dataset_id}/source_files/tiles/",
        "label_maps": f"b2://{bucket}/bronze_raw_data/{dataset_id}/source_files/label_maps/",
        "metadata": f"b2://{bucket}/metadata/datasets/{dataset_id}.json",
        "analytics": f"b2://{bucket}/metadata_analytics/{dataset_id}/",
        "silver_output": f"b2://{bucket}/silver_preprocessed_data/{dataset_id}/{prep_version}/",
        "gold_output": f"b2://{bucket}/gold_model_ready_data/{dataset_id}/{prep_version}/",
        "logs": f"b2://{bucket}/logs/preprocessing/{dataset_id}/{prep_version}/",
    }


def build_airflow_conf(
    dataset_id,
    dataset_name,
    mode,
    prep_version,
    output_mode,
    voxel_size,
    block_size,
    n_points,
    max_blocks_train,
    stride_val_test,
    split_gap_m,
    min_bldg_ratio,
    randla_overlap,
    ptv3_scene_length,
    num_workers,
    compute_normals,
    include_density,
    save_ply,
    compress_output,
):
    dataset_id = (dataset_id or "").strip()
    dataset_name = (dataset_name or dataset_id).strip()
    mode = mode or "train"
    prep_version = (prep_version or "").strip() or "prep_v001"
    storage = build_storage_contract(dataset_id, prep_version)

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "mode": mode,
        "prep_version": prep_version,
        "execution_target": "airflow_remote_gpu_cpu_workstation",
        "controller_runs_script_locally": False,
        "dag_id": AIRFLOW_DAG_ID,
        "script": get_preprocessing_script_info(),
        "storage": storage,
        "script_args": {
            "use_b2": True,
            "mode": mode,
            "custom_dataset": f"/airflow/staging/{dataset_id}/dataset_config.json",
            "data_dir": f"/airflow/staging/{dataset_id}/raw",
            "output_dir": f"/airflow/staging/{dataset_id}/preprocessed",
            "output_mode": output_mode,
            "b2_raw_bucket": storage["bucket"],
            "b2_raw_prefix": f"bronze_raw_data/{dataset_id}/source_files",
            "b2_output_bucket": storage["bucket"],
            "b2_output_prefix": f"gold_model_ready_data/{dataset_id}/{prep_version}",
            "cleanup_stage": True,
            "voxel_size": float(voxel_size),
            "block_size": float(block_size),
            "n_points": int(n_points),
            "max_blocks_train": int(max_blocks_train),
            "stride_val_test": float(stride_val_test),
            "split_gap_m": float(split_gap_m),
            "min_bldg_ratio": float(min_bldg_ratio),
            "randla_overlap": float(randla_overlap),
            "ptv3_scene_length": float(ptv3_scene_length),
            "num_workers": int(num_workers),
            "compute_normals": bool(compute_normals),
            "include_density": bool(include_density),
            "save_ply": bool(save_ply),
            "compress_output": bool(compress_output),
        },
    }


def build_remote_command(conf):
    args = conf["script_args"]
    command = [
        "python",
        conf["script"]["remote_execution_path"],
        "--use_b2",
        "--mode",
        conf["mode"],
        "--custom_dataset",
        args["custom_dataset"],
        "--data_dir",
        args["data_dir"],
        "--output_dir",
        args["output_dir"],
        "--output_mode",
        args["output_mode"],
        "--b2_raw_bucket",
        args["b2_raw_bucket"],
        "--b2_raw_prefix",
        args["b2_raw_prefix"],
        "--b2_output_bucket",
        args["b2_output_bucket"],
        "--b2_output_prefix",
        args["b2_output_prefix"],
        "--voxel_size",
        str(args["voxel_size"]),
        "--block_size",
        str(args["block_size"]),
        "--n_points",
        str(args["n_points"]),
        "--max_blocks_train",
        str(args["max_blocks_train"]),
        "--stride_val_test",
        str(args["stride_val_test"]),
        "--split_gap_m",
        str(args["split_gap_m"]),
        "--min_bldg_ratio",
        str(args["min_bldg_ratio"]),
        "--randla_overlap",
        str(args["randla_overlap"]),
        "--ptv3_scene_length",
        str(args["ptv3_scene_length"]),
        "--num_workers",
        str(args["num_workers"]),
    ]

    for flag in ["compute_normals", "include_density", "save_ply", "compress_output", "cleanup_stage"]:
        if args.get(flag):
            command.append(f"--{flag}")

    return command


def persist_airflow_request(conf):
    RUN_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{conf['dataset_id']}_{conf['prep_version']}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    payload = {
        "dag_run_id": run_id,
        "conf": conf,
        "remote_command": build_remote_command(conf),
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
