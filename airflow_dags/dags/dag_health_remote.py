from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from urllib import error, request

from airflow.decorators import dag, task


DAG_ID = "dag_health_remote"
HEALTH_INTERVAL_SECONDS = int(os.getenv("REMOTE_HEALTH_INTERVAL_SECONDS", "90"))
MLFLOW_HEALTH_URL = os.getenv("MLFLOW_HEALTH_URL", "").strip()


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(command, timeout=5):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)

    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def _mlflow_status():
    if not MLFLOW_HEALTH_URL:
        return {
            "status": "not_configured",
            "detail": "MLFLOW_HEALTH_URL is not configured in Airflow.",
        }

    try:
        req = request.Request(MLFLOW_HEALTH_URL, headers={"Accept": "application/json"}, method="GET")
        with request.urlopen(req, timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace")
            payload = json.loads(body) if body else {}
            return {
                "status": "ok" if 200 <= response.status < 400 else "warning",
                "url": MLFLOW_HEALTH_URL,
                "http_status": response.status,
                "detail": payload or body or "MLflow health endpoint reachable.",
            }
    except error.HTTPError as exc:
        return {"status": "offline", "url": MLFLOW_HEALTH_URL, "detail": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"status": "offline", "url": MLFLOW_HEALTH_URL, "detail": str(exc)}


def _dvc_status():
    dvc_bin = shutil.which("dvc")
    if not dvc_bin:
        return {"status": "unavailable", "detail": "DVC CLI is not installed in the Airflow worker."}

    ok, output = _run([dvc_bin, "--version"], timeout=5)
    if ok:
        return {"status": "ok", "version": output}
    return {"status": "offline", "detail": output or "dvc --version failed."}


def _gpu_status():
    if not shutil.which("nvidia-smi"):
        return {"available": False, "status": "unavailable", "detail": "nvidia-smi not found"}

    ok, output = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        timeout=5,
    )
    if not ok or not output:
        return {"available": False, "status": "offline", "detail": output or "nvidia-smi returned no data"}

    first = output.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    return {
        "available": True,
        "status": "ok",
        "name": parts[0] if len(parts) > 0 else "NVIDIA GPU",
        "memory_used_mb": parts[1] if len(parts) > 1 else "",
        "memory_total_mb": parts[2] if len(parts) > 2 else "",
        "utilization_percent": parts[3] if len(parts) > 3 else "",
    }


@dag(
    dag_id=DAG_ID,
    description="Scheduled remote runtime, MLflow, DVC, and GPU health check for Dash.",
    schedule=timedelta(seconds=HEALTH_INTERVAL_SECONDS),
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(seconds=30),
    tags=["health", "remote", "dash"],
)
def dag_health_remote():
    @task(task_id="check_remote_health", execution_timeout=timedelta(seconds=30))
    def check_remote_health():
        mlflow = _mlflow_status()
        dvc = _dvc_status()
        gpu = _gpu_status()
        system = {
            "status": "ok",
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "node": platform.node(),
        }

        status_values = [mlflow.get("status"), dvc.get("status"), gpu.get("status"), system.get("status")]
        if any(item in {"offline", "failed", "error"} for item in status_values):
            overall = "degraded"
        elif any(item in {"not_configured", "unavailable", "warning"} for item in status_values):
            overall = "warning"
        else:
            overall = "ok"

        return {
            "status": overall,
            "airflow": {"status": "ok", "detail": "Health DAG executed successfully."},
            "mlflow": mlflow,
            "dvc": dvc,
            "system": system,
            "gpu": gpu,
            "checked_at": _utc_now(),
        }

    check_remote_health()


dag_health_remote()
