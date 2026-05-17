import base64
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

from dotenv import load_dotenv

from services.b2_service import (
    B2_BUCKET_NAME,
    download_b2_file_to_local,
    get_b2_file_info_by_name,
    list_b2_files_with_prefix,
)


load_dotenv()


AIRFLOW_BASE_URL = (
    os.getenv("AIRFLOW_BASE_URL", "").strip()
    or os.getenv("AIRFLOW_API_BASE_URL", "").strip()
)
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "").strip()
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "").strip()
AIRFLOW_PREPROCESSING_DAG_ID = os.getenv(
    "AIRFLOW_PREPROCESSING_DAG_ID",
    "mls_preprocessing_v9",
).strip()

B2_ENDPOINT_URL = os.getenv("B2_ENDPOINT_URL", "").strip()
B2_ACCESS_KEY_ID = (
    os.getenv("B2_ACCESS_KEY_ID", "").strip()
    or os.getenv("B2_KEY_ID", "").strip()
)
B2_SECRET_ACCESS_KEY = (
    os.getenv("B2_SECRET_ACCESS_KEY", "").strip()
    or os.getenv("B2_APPLICATION_KEY", "").strip()
)

SILVER_LOCAL_CACHE_DIR = Path(
    os.getenv("SILVER_LOCAL_CACHE_DIR", "data/local_staging/silver_outputs")
)
GOLD_LOCAL_CACHE_DIR = Path(
    os.getenv("GOLD_LOCAL_CACHE_DIR", "data/local_staging/gold_outputs")
)
DEMO_MODE = os.getenv("DEMO_MODE", "0").strip().lower() in {"1", "true", "yes"}

EXPECTED_SILVER_OUTPUTS = [
    {
        "id": "processed_cloud_meta",
        "label": "processed_cloud_meta.json",
        "candidates": ["processed_cloud_meta.json"],
        "required": True,
    },
    {
        "id": "silver_stats",
        "label": "silver_stats.json",
        "candidates": ["silver_stats.json"],
        "required": True,
    },
    {
        "id": "silver_density_grid",
        "label": "silver_density_grid.parquet",
        "candidates": ["silver_density_grid.parquet"],
        "required": True,
    },
    {
        "id": "silver_npz",
        "label": "silver.npz",
        "candidates": ["silver_npz/silver.npz", "silver.npz"],
        "required": True,
    },
]

SILVER_DENSITY_REQUIRED_COLUMNS = {
    "cx",
    "cy",
    "total_pts",
    "building_pts",
    "non_building_pts",
    "gap_buffer_pts",
    "building_ratio",
}

GOLD_FOLDERS = ["blocks", "train", "val", "test", "eval", "meta"]
GOLD_SUPPORT_FILES = [
    ("meta/preprocessing_contract.json", "preprocessing_contract.json"),
    ("meta/dataset_stats.csv", "dataset_stats.csv"),
    ("meta/label_map.json", "label_map.json"),
    ("meta/splits.json", "splits.json"),
    ("eval/split_stats.json", "split_stats.json"),
    ("eval/density_report.json", "density_report.json"),
    ("eval/model_configs.json", "model_configs.json"),
    ("eval/preprocessing_profile.json", "preprocessing_profile.json"),
    ("eval/site_boundary_map.json", "site_boundary_map.json"),
    ("eval/class_weights.npy", "class_weights.npy"),
]


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_prefix(prefix):
    return str(prefix or "").strip().strip("/")


def _join_key(prefix, relative_path):
    prefix = _normalize_prefix(prefix)
    relative_path = str(relative_path or "").strip().lstrip("/")
    return f"{prefix}/{relative_path}" if prefix else relative_path


def _human_bytes(value):
    if value is None:
        return "n/a"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "n/a"
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "n/a"


def _airflow_headers(content_type=None):
    headers = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    if AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
        token = base64.b64encode(
            f"{AIRFLOW_USERNAME}:{AIRFLOW_PASSWORD}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def _airflow_json(method, path, payload=None, timeout=15):
    if not AIRFLOW_BASE_URL:
        raise ValueError("AIRFLOW_BASE_URL or AIRFLOW_API_BASE_URL is not configured.")

    url = f"{AIRFLOW_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    body = None
    headers = _airflow_headers()
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers = _airflow_headers("application/json")

    req = request.Request(url, data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def trigger_airflow_preprocessing_dag(dag_id, conf):
    """Trigger the remote Airflow preprocessing DAG with a JSON conf payload."""

    dag_id = (dag_id or AIRFLOW_PREPROCESSING_DAG_ID).strip()
    run_id = (
        conf.get("run_id")
        or conf.get("dag_run_id")
        or f"{conf.get('dataset_id', 'dataset')}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    )
    payload = {
        "dag_run_id": run_id,
        "conf": conf,
    }
    result = _airflow_json(
        "POST",
        f"/api/v1/dags/{parse.quote(dag_id, safe='')}/dagRuns",
        payload=payload,
        timeout=30,
    )
    return {
        "status": "triggered",
        "dag_id": dag_id,
        "dag_run_id": result.get("dag_run_id") or run_id,
        "state": result.get("state") or "queued",
        "start_time": result.get("start_date") or result.get("logical_date"),
        "response": result,
    }


def fetch_airflow_dag_status(dag_id, dag_run_id):
    """Fetch high-level DAG run status from the Airflow REST API."""

    if not dag_run_id:
        return {
            "dag_id": dag_id,
            "dag_run_id": "",
            "state": "not_started",
            "detail": "No DAG run has been triggered from this page.",
        }
    try:
        payload = _airflow_json(
            "GET",
            "/api/v1/dags/{}/dagRuns/{}".format(
                parse.quote(dag_id, safe=""),
                parse.quote(dag_run_id, safe=""),
            ),
            timeout=10,
        )
    except Exception as exc:
        return {
            "dag_id": dag_id,
            "dag_run_id": dag_run_id,
            "state": "unknown",
            "detail": str(exc),
        }

    return {
        "dag_id": dag_id,
        "dag_run_id": payload.get("dag_run_id") or dag_run_id,
        "state": payload.get("state") or "unknown",
        "start_time": payload.get("start_date") or payload.get("logical_date"),
        "end_time": payload.get("end_date"),
        "logical_date": payload.get("logical_date") or payload.get("execution_date"),
        "raw": payload,
    }


def fetch_airflow_task_status(dag_id, dag_run_id):
    """Fetch Airflow task instances and compute progress for the run."""

    if not dag_run_id:
        return {
            "tasks": [],
            "current_task": "",
            "completed_tasks": 0,
            "total_tasks": 0,
            "progress_pct": 0,
            "failed_task": "",
        }
    try:
        payload = _airflow_json(
            "GET",
            "/api/v1/dags/{}/dagRuns/{}/taskInstances".format(
                parse.quote(dag_id, safe=""),
                parse.quote(dag_run_id, safe=""),
            ),
            timeout=15,
        )
    except Exception as exc:
        return {
            "tasks": [],
            "current_task": "",
            "completed_tasks": 0,
            "total_tasks": 0,
            "progress_pct": 0,
            "failed_task": "",
            "detail": str(exc),
        }

    tasks = payload.get("task_instances") or []
    total = len(tasks)
    completed_states = {"success", "skipped"}
    active_states = {"running", "queued", "scheduled", "up_for_retry"}
    completed = sum(1 for item in tasks if item.get("state") in completed_states)
    failed = [item for item in tasks if item.get("state") in {"failed", "upstream_failed"}]
    active = [item for item in tasks if item.get("state") in active_states]
    progress_pct = round((completed / total) * 100, 1) if total else 0
    current = active[0].get("task_id") if active else ""

    return {
        "tasks": tasks,
        "current_task": current,
        "completed_tasks": completed,
        "total_tasks": total,
        "progress_pct": progress_pct,
        "failed_task": failed[0].get("task_id") if failed else "",
    }


def fetch_latest_airflow_error(dag_id, dag_run_id):
    """Return a concise latest failure summary for an Airflow DAG run."""

    task_status = fetch_airflow_task_status(dag_id, dag_run_id)
    failed_task = task_status.get("failed_task")
    if not failed_task:
        return {"failed_task": "", "error": ""}

    log_path = "/api/v1/dags/{}/dagRuns/{}/taskInstances/{}/logs/1".format(
        parse.quote(dag_id, safe=""),
        parse.quote(dag_run_id, safe=""),
        parse.quote(failed_task, safe=""),
    )
    try:
        payload = _airflow_json("GET", log_path, timeout=10)
        raw = payload.get("content") or payload.get("message") or json.dumps(payload)
    except Exception as exc:
        raw = str(exc)

    summary = str(raw or "").strip().splitlines()[-8:]
    return {
        "failed_task": failed_task,
        "error": "\n".join(summary)[-1200:],
    }


def build_airflow_status_snapshot(dag_id, dag_run_id):
    dag = fetch_airflow_dag_status(dag_id, dag_run_id)
    tasks = fetch_airflow_task_status(dag_id, dag_run_id)
    latest_error = fetch_latest_airflow_error(dag_id, dag_run_id) if dag.get("state") == "failed" else {}
    return {
        **dag,
        **tasks,
        "latest_error": latest_error.get("error") or "",
        "failed_task": latest_error.get("failed_task") or tasks.get("failed_task") or "",
        "checked_at": _utc_now(),
    }


def _try_boto3_list(prefix):
    if not (B2_ENDPOINT_URL and B2_ACCESS_KEY_ID and B2_SECRET_ACCESS_KEY):
        return None
    try:
        import boto3
    except Exception:
        return None

    client = boto3.client(
        "s3",
        endpoint_url=B2_ENDPOINT_URL,
        aws_access_key_id=B2_ACCESS_KEY_ID,
        aws_secret_access_key=B2_SECRET_ACCESS_KEY,
    )
    paginator = client.get_paginator("list_objects_v2")
    files = []
    for page in paginator.paginate(Bucket=B2_BUCKET_NAME, Prefix=prefix):
        for item in page.get("Contents") or []:
            files.append(
                {
                    "file_name": item.get("Key"),
                    "size": item.get("Size"),
                    "last_modified": str(item.get("LastModified") or ""),
                }
            )
    return files


def _list_b2_prefix(prefix):
    prefix = _normalize_prefix(prefix)
    if not prefix:
        return []

    boto3_files = _try_boto3_list(prefix)
    if boto3_files is not None:
        return boto3_files

    return list_b2_files_with_prefix(prefix)


def _verify_candidate(prefix, candidates, listing):
    by_name = {item.get("file_name"): item for item in listing if item.get("file_name")}
    for relative_path in candidates:
        key = _join_key(prefix, relative_path)
        if key in by_name:
            item = by_name[key]
            return {
                "exists": True,
                "b2_key": key,
                "size": item.get("size"),
                "size_display": _human_bytes(item.get("size")),
                "status": "verified",
            }

        try:
            item = get_b2_file_info_by_name(key)
            return {
                "exists": True,
                "b2_key": key,
                "size": item.get("size"),
                "size_display": _human_bytes(item.get("size")),
                "status": "verified",
            }
        except Exception:
            continue

    primary = _join_key(prefix, candidates[0] if candidates else "")
    return {
        "exists": False,
        "b2_key": primary,
        "size": None,
        "size_display": "missing",
        "status": "missing",
    }


def verify_b2_silver_outputs(dataset_id, b2_prefix):
    """Verify expected Silver artifacts using real B2 object listings."""

    prefix = _normalize_prefix(b2_prefix) or f"silver_preprocessed_data/{dataset_id}/prep_v001"
    try:
        listing = _list_b2_prefix(prefix)
        rows = []
        for spec in EXPECTED_SILVER_OUTPUTS:
            result = _verify_candidate(prefix, spec["candidates"], listing)
            rows.append(
                {
                    "artifact": spec["label"],
                    "required": "yes" if spec.get("required") else "no",
                    **result,
                }
            )
        verified = sum(1 for row in rows if row["exists"])
        missing = [row["artifact"] for row in rows if row["required"] == "yes" and not row["exists"]]
        status = "passed" if not missing else ("partial" if verified else "failed")
        return {
            "dataset_id": dataset_id,
            "b2_prefix": prefix,
            "bucket": B2_BUCKET_NAME,
            "status": status,
            "verified_count": verified,
            "expected_count": len(rows),
            "missing": missing,
            "rows": rows,
            "checked_at": _utc_now(),
            "error": "",
        }
    except Exception as exc:
        return {
            "dataset_id": dataset_id,
            "b2_prefix": prefix,
            "bucket": B2_BUCKET_NAME,
            "status": "unknown",
            "verified_count": 0,
            "expected_count": len(EXPECTED_SILVER_OUTPUTS),
            "missing": [item["label"] for item in EXPECTED_SILVER_OUTPUTS],
            "rows": [],
            "checked_at": _utc_now(),
            "error": str(exc),
        }


def build_gold_output_contract(dataset_id, prep_version):
    prefix = f"gold_model_ready_data/{dataset_id}/{prep_version}"
    return {
        "bucket": B2_BUCKET_NAME,
        "prefix": prefix,
        "b2_uri": f"b2://{B2_BUCKET_NAME}/{prefix}/",
        "folders": [
            {"artifact": folder, "b2_key": f"{prefix}/{folder}/", "kind": "folder"}
            for folder in GOLD_FOLDERS
        ],
        "files": [
            {
                "artifact": label,
                "b2_key": f"{prefix}/{relative_path}",
                "relative_path": relative_path,
                "kind": "file",
            }
            for relative_path, label in GOLD_SUPPORT_FILES
        ],
    }


def compute_gold_output_status(expected_gold_files, b2_listing):
    names = {item.get("file_name") for item in b2_listing if item.get("file_name")}
    rows = []
    for item in expected_gold_files:
        key = item["b2_key"].rstrip("/")
        exists = any(name == key or name.startswith(f"{key}/") for name in names)
        match = next((entry for entry in b2_listing if entry.get("file_name") == key), {})
        rows.append(
            {
                **item,
                "exists": exists,
                "status": "generated" if exists else "planned",
                "size": match.get("size"),
                "size_display": _human_bytes(match.get("size")) if exists else "planned",
            }
        )
    generated = sum(1 for row in rows if row["exists"])
    status = "passed" if generated == len(rows) and rows else ("partial" if generated else "planned")
    return {
        "status": status,
        "generated_count": generated,
        "expected_count": len(rows),
        "rows": rows,
    }


def verify_b2_gold_outputs(dataset_id, prep_version, b2_prefix=None):
    """Verify existing Gold outputs without marking planned artifacts as generated."""

    contract = build_gold_output_contract(dataset_id, prep_version)
    prefix = _normalize_prefix(b2_prefix) or contract["prefix"]
    try:
        listing = _list_b2_prefix(prefix)
        expected = contract["folders"] + contract["files"]
        status = compute_gold_output_status(expected, listing)
        return {
            **contract,
            **status,
            "prefix": prefix,
            "checked_at": _utc_now(),
            "error": "",
        }
    except Exception as exc:
        expected = contract["folders"] + contract["files"]
        return {
            **contract,
            "prefix": prefix,
            "status": "unknown",
            "generated_count": 0,
            "expected_count": len(expected),
            "rows": [{**item, "exists": False, "status": "planned", "size_display": "unknown"} for item in expected],
            "checked_at": _utc_now(),
            "error": str(exc),
        }


def _cache_path(cache_root, b2_key):
    clean = str(b2_key or "").strip().lstrip("/")
    return Path(cache_root) / clean


def _local_silver_dir(dataset_id, b2_prefix):
    prefix_parts = _normalize_prefix(b2_prefix).split("/")
    prep_version = prefix_parts[-1] if prefix_parts else "prep_v001"
    return SILVER_LOCAL_CACHE_DIR / str(dataset_id or "dataset") / prep_version


def _read_local_json(path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_b2_json_file(b2_key):
    """Download and parse one real JSON object from B2."""

    local_path = _cache_path(tempfile.gettempdir(), f"data-platform-b2-cache/{b2_key}")
    try:
        download_b2_file_to_local(b2_key=b2_key, local_path=str(local_path))
        return {
            "data": _read_local_json(local_path),
            "b2_key": b2_key,
            "local_path": str(local_path),
            "error": "",
        }
    except Exception as exc:
        return {"data": None, "b2_key": b2_key, "local_path": str(local_path), "error": str(exc)}


def load_b2_parquet_file(b2_key):
    """Download and read one real Parquet object from B2 with clear error reporting."""

    local_path = _cache_path(tempfile.gettempdir(), f"data-platform-b2-cache/{b2_key}")
    try:
        import pandas as pd
    except Exception as exc:
        return {"data": None, "b2_key": b2_key, "local_path": str(local_path), "error": f"pandas unavailable: {exc}"}

    try:
        download_b2_file_to_local(b2_key=b2_key, local_path=str(local_path))
        frame = pd.read_parquet(local_path)
        return {"data": frame, "b2_key": b2_key, "local_path": str(local_path), "error": ""}
    except ImportError as exc:
        return {"data": None, "b2_key": b2_key, "local_path": str(local_path), "error": f"Parquet engine missing: {exc}"}
    except Exception as exc:
        return {"data": None, "b2_key": b2_key, "local_path": str(local_path), "error": str(exc)}


def _load_local_or_b2_json(local_path, b2_key):
    try:
        data = _read_local_json(local_path)
        if data is not None:
            return {"data": data, "source": str(local_path), "error": ""}
    except Exception as exc:
        return {"data": None, "source": str(local_path), "error": str(exc)}
    loaded = load_b2_json_file(b2_key)
    return {
        "data": loaded.get("data"),
        "source": loaded.get("b2_key"),
        "error": loaded.get("error", ""),
    }


def _load_local_or_b2_parquet(local_path, b2_key):
    try:
        if local_path.exists():
            import pandas as pd

            return {
                "data": pd.read_parquet(local_path),
                "source": str(local_path),
                "error": "",
            }
    except ImportError as exc:
        return {"data": None, "source": str(local_path), "error": f"Parquet engine missing: {exc}"}
    except Exception as exc:
        return {"data": None, "source": str(local_path), "error": str(exc)}

    loaded = load_b2_parquet_file(b2_key)
    return {
        "data": loaded.get("data"),
        "source": loaded.get("b2_key"),
        "error": loaded.get("error", ""),
    }


def load_local_or_b2_silver_metadata(dataset_id, b2_prefix):
    """Load actual Silver JSON and Parquet outputs from cache first, then B2."""

    prefix = _normalize_prefix(b2_prefix) or f"silver_preprocessed_data/{dataset_id}/prep_v001"
    local_dir = _local_silver_dir(dataset_id, prefix)
    meta_result = _load_local_or_b2_json(
        local_dir / "processed_cloud_meta.json",
        _join_key(prefix, "processed_cloud_meta.json"),
    )
    stats_result = _load_local_or_b2_json(
        local_dir / "silver_stats.json",
        _join_key(prefix, "silver_stats.json"),
    )
    density_result = _load_local_or_b2_parquet(
        local_dir / "silver_density_grid.parquet",
        _join_key(prefix, "silver_density_grid.parquet"),
    )

    density_df = density_result.get("data")
    density_columns = set(list(getattr(density_df, "columns", []))) if density_df is not None else set()
    missing_density_columns = sorted(SILVER_DENSITY_REQUIRED_COLUMNS - density_columns)
    density_error = density_result.get("error") or ""
    if density_df is not None and missing_density_columns:
        density_error = (
            "silver_density_grid.parquet is missing required columns: "
            + ", ".join(missing_density_columns)
        )

    return {
        "dataset_id": dataset_id,
        "b2_prefix": prefix,
        "metadata": meta_result.get("data"),
        "stats": stats_result.get("data"),
        "density_df": density_df,
        "sources": {
            "metadata": meta_result.get("source"),
            "stats": stats_result.get("source"),
            "density": density_result.get("source"),
        },
        "errors": {
            "metadata": meta_result.get("error", ""),
            "stats": stats_result.get("error", ""),
            "density": density_error,
        },
        "missing_density_columns": missing_density_columns,
    }


def load_gold_metadata_if_available(dataset_id, prep_version, b2_prefix):
    prefix = _normalize_prefix(b2_prefix) or f"gold_model_ready_data/{dataset_id}/{prep_version}"
    keys = {
        "preprocessing_contract": _join_key(prefix, "meta/preprocessing_contract.json"),
        "label_map": _join_key(prefix, "meta/label_map.json"),
        "splits": _join_key(prefix, "meta/splits.json"),
        "density_report": _join_key(prefix, "eval/density_report.json"),
        "model_configs": _join_key(prefix, "eval/model_configs.json"),
    }
    payload = {}
    errors = {}
    for name, key in keys.items():
        result = load_b2_json_file(key)
        payload[name] = result.get("data")
        if result.get("error"):
            errors[name] = result["error"]
    return {"data": payload, "errors": errors, "prefix": prefix}


def compute_silver_readiness(silver_verification, silver_payload):
    verification = silver_verification or {}
    metadata = (silver_payload or {}).get("metadata") or {}
    stats = (silver_payload or {}).get("stats") or {}
    errors = (silver_payload or {}).get("errors") or {}
    density_df = (silver_payload or {}).get("density_df")

    failed = []
    if verification.get("status") != "passed":
        missing = ", ".join(verification.get("missing") or [])
        failed.append(f"Missing B2 Silver artifacts: {missing or 'verification did not pass'}")
    if not metadata:
        failed.append(errors.get("metadata") or "processed_cloud_meta.json could not be loaded")
    if not stats:
        failed.append(errors.get("stats") or "silver_stats.json could not be loaded")
    if density_df is None:
        failed.append(errors.get("density") or "silver_density_grid.parquet could not be loaded")
    elif errors.get("density"):
        failed.append(errors["density"])

    try:
        num_points = int(metadata.get("num_points") or 0)
    except (TypeError, ValueError):
        num_points = 0
    if num_points <= 0:
        failed.append("processed point count is missing or zero")
    if metadata and not metadata.get("has_labels"):
        failed.append("labels are not present in Silver metadata")
    if metadata and not metadata.get("has_density"):
        failed.append("density feature is not present in Silver metadata")

    if not failed:
        status = "passed"
    elif metadata or stats or verification.get("verified_count"):
        status = "partial"
    else:
        status = "failed"

    return {
        "status": status,
        "failed_checks": failed,
        "passed": status == "passed",
    }
