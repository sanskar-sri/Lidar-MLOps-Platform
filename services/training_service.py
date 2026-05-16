import base64
import json
import os
from datetime import datetime
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

from services.b2_service import B2_BUCKET_NAME
from services.mlflow_service import DEFAULT_TRAINING_MLFLOW_TRACKING_URI


load_dotenv()


REMOTE_TRAINING_SCRIPT_PATH = os.getenv(
    "REMOTE_TRAINING_SCRIPT_PATH",
    "/opt/mls_training/scripts/training_job.py",
)
AIRFLOW_TRAINING_DAG_ID = os.getenv("AIRFLOW_TRAINING_DAG_ID", "mls_training_v1")
AIRFLOW_API_BASE_URL = os.getenv("AIRFLOW_API_BASE_URL", "")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "")
TRAINING_RUN_REQUEST_DIR = Path("data/airflow_training_requests")


def _utc_run_id(dataset_id, prep_version, model_type):
    clean_dataset_id = (dataset_id or "dataset").strip() or "dataset"
    clean_prep_version = (prep_version or "prep_v001").strip() or "prep_v001"
    clean_model_type = (model_type or "model").strip() or "model"
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{clean_dataset_id}_{clean_prep_version}_{clean_model_type}_{timestamp}"


def build_training_storage_contract(dataset_id, prep_version, model_type, run_id, bucket_name=None):
    dataset_id = (dataset_id or "").strip()
    prep_version = (prep_version or "prep_v001").strip() or "prep_v001"
    model_type = (model_type or "pointnet2").strip() or "pointnet2"
    run_id = (run_id or "<run_id>").strip() or "<run_id>"
    bucket = bucket_name or B2_BUCKET_NAME

    return {
        "bucket": bucket,
        "gold_input": f"b2://{bucket}/gold_model_ready_data/{dataset_id}/{prep_version}/",
        "training_output": (
            f"b2://{bucket}/training_runs/{dataset_id}/{prep_version}/{model_type}/{run_id}/"
        ),
        "segmentation_output": (
            f"b2://{bucket}/segmentation_outputs/{dataset_id}/{prep_version}/{model_type}/{run_id}/"
        ),
        "training_logs": f"b2://{bucket}/logs/training/{dataset_id}/{run_id}/",
    }


def build_training_conf(
    dataset_id,
    dataset_name,
    prep_version,
    model_type,
    execution_target=None,
    airflow_queue=None,
    run_id=None,
    num_epochs=80,
    batch_size=4,
    learning_rate=0.001,
    mlflow_tracking_uri=DEFAULT_TRAINING_MLFLOW_TRACKING_URI,
    mlflow_experiment="mls-training",
    dvc_remote="b2remote",
    upload_to_b2=True,
):
    dataset_id = (dataset_id or "").strip()
    dataset_name = (dataset_name or dataset_id).strip()
    prep_version = (prep_version or "prep_v001").strip() or "prep_v001"
    model_type = (model_type or "pointnet2").strip() or "pointnet2"
    run_id = (run_id or "").strip() or _utc_run_id(dataset_id, prep_version, model_type)
    storage = build_training_storage_contract(dataset_id, prep_version, model_type, run_id)

    staging_root = f"/airflow/training_staging/{dataset_id}/{prep_version}/{model_type}/{run_id}"
    preprocessed_dataset_root = f"{staging_root}/gold"
    artifact_root = f"{staging_root}/artifacts"

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "prep_version": prep_version,
        "model_type": model_type,
        "run_id": run_id,
        "execution_target": execution_target or "any_gpu_worker",
        "airflow_queue": airflow_queue or "system1",
        "controller_runs_training_locally": False,
        "dag_id": AIRFLOW_TRAINING_DAG_ID,
        "script": {
            "name": "MLS Training Runner",
            "remote_execution_path": REMOTE_TRAINING_SCRIPT_PATH,
            "purpose": (
                "Pull gold model-ready blocks, run segmentation training remotely, "
                "and upload training or segmentation artifacts to B2."
            ),
        },
        "storage": storage,
        "script_args": {
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "prep_version": prep_version,
            "model_type": model_type,
            "run_id": run_id,
            "gold_data_uri": storage["gold_input"],
            "preprocessed_dataset_root": preprocessed_dataset_root,
            "artifact_root": artifact_root,
            "b2_bucket": storage["bucket"],
            "b2_training_prefix": f"training_runs/{dataset_id}/{prep_version}/{model_type}/{run_id}",
            "b2_segmentation_prefix": (
                f"segmentation_outputs/{dataset_id}/{prep_version}/{model_type}/{run_id}"
            ),
            "b2_logs_prefix": f"logs/training/{dataset_id}/{run_id}",
            "num_epochs": int(num_epochs),
            "batch_size": int(batch_size),
            "learning_rate": float(learning_rate),
            "mlflow_tracking_uri": (mlflow_tracking_uri or "").strip() or DEFAULT_TRAINING_MLFLOW_TRACKING_URI,
            "mlflow_experiment": (mlflow_experiment or "").strip() or "mls-training",
            "dvc_remote": (dvc_remote or "").strip() or "b2remote",
            "upload_to_b2": bool(upload_to_b2),
        },
    }


def _append_value(command, flag, value):
    if value is not None:
        command.extend([flag, str(value)])


def build_training_command(conf):
    args = conf["script_args"]
    command = [
        "python",
        conf["script"]["remote_execution_path"],
    ]

    for flag, key in [
        ("--dataset_id", "dataset_id"),
        ("--dataset_name", "dataset_name"),
        ("--prep_version", "prep_version"),
        ("--model_type", "model_type"),
        ("--run_id", "run_id"),
        ("--gold_data_uri", "gold_data_uri"),
        ("--preprocessed_dataset_root", "preprocessed_dataset_root"),
        ("--artifact_root", "artifact_root"),
        ("--b2_bucket", "b2_bucket"),
        ("--b2_training_prefix", "b2_training_prefix"),
        ("--b2_segmentation_prefix", "b2_segmentation_prefix"),
        ("--b2_logs_prefix", "b2_logs_prefix"),
        ("--num_epochs", "num_epochs"),
        ("--batch_size", "batch_size"),
        ("--learning_rate", "learning_rate"),
        ("--mlflow_tracking_uri", "mlflow_tracking_uri"),
        ("--mlflow_experiment", "mlflow_experiment"),
        ("--dvc_remote", "dvc_remote"),
    ]:
        _append_value(command, flag, args.get(key))

    if args.get("upload_to_b2"):
        command.append("--upload_to_b2")

    return command


def persist_training_request(conf):
    TRAINING_RUN_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    run_id = conf.get("run_id") or _utc_run_id(
        conf["dataset_id"],
        conf["prep_version"],
        conf["model_type"],
    )
    payload = {
        "dag_run_id": run_id,
        "conf": conf,
        "remote_command": build_training_command(conf),
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    output_path = TRAINING_RUN_REQUEST_DIR / f"{run_id}.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, str(output_path)


def trigger_training_dag(conf):
    if not AIRFLOW_API_BASE_URL:
        raise ValueError("AIRFLOW_API_BASE_URL is not configured, so only a local training payload was created.")

    payload, payload_path = persist_training_request(conf)
    url = f"{AIRFLOW_API_BASE_URL.rstrip('/')}/api/v1/dags/{AIRFLOW_TRAINING_DAG_ID}/dagRuns"
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
