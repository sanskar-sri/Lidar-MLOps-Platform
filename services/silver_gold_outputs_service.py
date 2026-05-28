import json
import re
from pathlib import Path

from services.b2_paths import b2_prefix, gold_prefix, segmentation_prefix, silver_prefix

try:
    from services.b2_service import list_b2_files_with_prefix
except Exception as exc:
    print(f"[SILVER GOLD SERVICE] B2 listing unavailable: {exc}")
    list_b2_files_with_prefix = None


LOCAL_STAGING = Path("data/local_staging")
SILVER_ROOT = LOCAL_STAGING / "silver_outputs"
GOLD_ROOT = LOCAL_STAGING / "gold_outputs"
PREPROCESSING_REQUEST_ROOT = Path("data/airflow_preprocessing_requests")
BLOCK_EXTENSIONS = {".npz", ".npy", ".pt", ".pth", ".parquet"}
ACTIVE_AIRFLOW_STATES = {"queued", "running", "scheduled", "up_for_retry"}


def get_empty_silver_gold_summary():
    return {
        "message": "No Silver or Gold output manifests found yet.",
        "dataset_id": "",
        "prep_version": "",
        "preprocessing_status": "No dataset",
        "workflow_state": "empty",
        "silver_files": 0,
        "gold_files": 0,
        "gold_blocks": 0,
        "split_status": "Pending",
        "output_status": "Pending",
        "gold_ready_for_training": False,
        "split_availability": {"train": False, "val": False, "test": False},
        "silver": [],
        "gold": [],
        "feature_schema": [],
        "label_schema": [],
        "paths": [],
        "latest_request": None,
        "airflow_status": None,
        "errors": [],
    }


def _safe_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_prefix(prefix):
    return str(prefix or "").strip().strip("/")


def _safe_b2_list(prefix):
    prefix = _normalize_prefix(prefix)
    if not prefix:
        return [], ""
    if list_b2_files_with_prefix is None:
        return [], "B2 listing is not available."
    try:
        return list_b2_files_with_prefix(prefix), ""
    except Exception as exc:
        print(f"[SILVER GOLD SERVICE] B2 list failed prefix={prefix}: {exc}")
        return [], str(exc)


def _version_entry():
    return {"files": [], "local_files": [], "b2_files": []}


def _add_unique(items, value):
    if value and value not in items:
        items.append(value)


def _collect_local_versions(root, dataset_id):
    versions = {}
    dataset_root = root / dataset_id
    if not dataset_root.exists():
        return versions

    for version_dir in sorted(path for path in dataset_root.iterdir() if path.is_dir()):
        files = sorted(path for path in version_dir.rglob("*") if path.is_file())
        entry = versions.setdefault(version_dir.name, _version_entry())
        for path in files:
            text = str(path)
            entry["files"].append(text)
            entry["local_files"].append(text)
    return versions


def _collect_b2_versions(prefix_name, dataset_id):
    versions = {}
    errors = []
    root = f"{b2_prefix(prefix_name)}/{dataset_id}/"
    files, error = _safe_b2_list(root)
    if error:
        errors.append(error)

    for item in files or []:
        file_name = str(item.get("file_name") or "").strip()
        if not file_name.startswith(root):
            continue
        relative = file_name[len(root):].lstrip("/")
        parts = relative.split("/", 1)
        prep_version = parts[0] if parts else ""
        if not prep_version:
            continue
        entry = versions.setdefault(prep_version, _version_entry())
        entry["files"].append(file_name)
        entry["b2_files"].append(item)
    return versions, errors


def _merge_versions(*maps):
    merged = {}
    for version_map in maps:
        for prep_version, entry in (version_map or {}).items():
            target = merged.setdefault(prep_version, _version_entry())
            for key in ("files", "local_files", "b2_files"):
                for value in entry.get(key) or []:
                    _add_unique(target[key], value)
    return merged


def _extract_prep_version(*values):
    for value in values:
        match = re.search(r"(prep_v[0-9A-Za-z_-]+)", str(value or ""))
        if match:
            return match.group(1)
    return "prep_v001"


def _preprocessing_requests(dataset_id):
    if not PREPROCESSING_REQUEST_ROOT.exists():
        return []

    records = []
    for path in PREPROCESSING_REQUEST_ROOT.glob(f"{dataset_id}_*.json"):
        if path.name.endswith("_dataset_config.json"):
            continue
        payload = _safe_json(path)
        conf = payload.get("conf") if isinstance(payload, dict) else {}
        conf = conf if isinstance(conf, dict) else {}
        record_dataset = conf.get("dataset_id") or path.name.split("_prep_")[0]
        if record_dataset != dataset_id:
            continue
        dag_run_id = payload.get("dag_run_id") or conf.get("run_id") or path.stem
        records.append(
            {
                "path": str(path),
                "created_at": payload.get("created_at") or "",
                "dag_id": conf.get("dag_id") or payload.get("dag_id") or "",
                "dag_run_id": dag_run_id,
                "prep_version": conf.get("prep_version")
                or _extract_prep_version(dag_run_id, path.name),
                "state": payload.get("state") or conf.get("state") or "",
            }
        )
    return sorted(records, key=lambda item: Path(item["path"]).stat().st_mtime, reverse=True)


def _latest_airflow_status(request_record, include_airflow_status):
    if not include_airflow_status or not request_record:
        return None
    dag_run_id = request_record.get("dag_run_id")
    if not dag_run_id:
        return None
    try:
        from services.preprocessing_runtime_service import (
            AIRFLOW_PREPROCESSING_DAG_ID,
            build_airflow_status_snapshot,
        )

        dag_id = request_record.get("dag_id") or AIRFLOW_PREPROCESSING_DAG_ID
        return build_airflow_status_snapshot(dag_id, dag_run_id)
    except Exception as exc:
        return {"state": "unknown", "detail": str(exc), "dag_run_id": dag_run_id}


def _choose_prep_version(requests, silver_versions, gold_versions, requested_prep_version=None):
    requested_prep_version = str(requested_prep_version or "").strip()
    if requested_prep_version:
        return requested_prep_version
    for version_map in (gold_versions, silver_versions):
        versions = [key for key, entry in version_map.items() if entry.get("files")]
        if versions:
            return sorted(versions)[-1]
    if requests:
        return requests[0].get("prep_version") or "prep_v001"
    return ""


def _split_availability(paths):
    availability = {"train": False, "val": False, "test": False}
    for raw_path in paths or []:
        path = str(raw_path).replace("\\", "/").lower()
        name = Path(path).name.lower()
        for split in availability:
            tokens = (
                f"/{split}/",
                f"/{split}_",
                f"_{split}_",
                f"_{split}.",
                f"{split}_",
                f"split={split}",
            )
            if any(token in path or token in name for token in tokens):
                availability[split] = True
    return availability


def _block_count(paths):
    count = 0
    for path in paths or []:
        suffix = Path(str(path).split("?", 1)[0]).suffix.lower()
        if suffix in BLOCK_EXTENSIONS:
            count += 1
    return count


def _feature_schema(dataset_id, prep_version):
    model_configs = _safe_json(
        GOLD_ROOT / dataset_id / prep_version / "artifacts" / "eval" / "model_configs.json"
    )
    if not isinstance(model_configs, dict):
        return []
    return [
        {
            "dataset_id": dataset_id,
            "model": model_name,
            "channels": ", ".join(map(str, config.get("feature_channels", [])))
            if isinstance(config, dict)
            else "n/a",
        }
        for model_name, config in model_configs.items()
    ]


def _label_schema(dataset_id, prep_version):
    label_map = _safe_json(
        GOLD_ROOT / dataset_id / prep_version / "artifacts" / "meta" / "label_map.json"
    )
    if not isinstance(label_map, dict):
        return []
    return [
        {"dataset_id": dataset_id, "label": str(key), "class": str(value)}
        for key, value in label_map.items()
    ]


def _workflow_state(latest_request, airflow_status, silver_count, gold_count, gold_ready):
    state = str((airflow_status or {}).get("state") or (latest_request or {}).get("state") or "").lower()
    has_outputs = bool(silver_count or gold_count)
    if state == "failed":
        return "failed", "Preprocessing failed. View preprocessing logs."
    if state in ACTIVE_AIRFLOW_STATES:
        return "running", "Preprocessing is running. Silver and Gold outputs are not ready yet."
    if gold_ready:
        return "completed", ""
    if has_outputs:
        return "partial", "Preprocessing outputs are incomplete. Gold model-ready blocks are not ready for Training yet."
    if latest_request:
        return "running", "Preprocessing is running. Silver and Gold outputs are not ready yet."
    return "no_run", "No preprocessing run found for this dataset."


def load_silver_gold_summary(dataset_id=None, prep_version=None, include_airflow_status=False):
    try:
        summary = get_empty_silver_gold_summary()
        dataset_id = str(dataset_id or "").strip()
        if not dataset_id:
            return summary

        requests = _preprocessing_requests(dataset_id)
        latest_request = requests[0] if requests else None

        local_silver = _collect_local_versions(SILVER_ROOT, dataset_id)
        local_gold = _collect_local_versions(GOLD_ROOT, dataset_id)
        b2_silver, silver_errors = _collect_b2_versions("silver_preprocessed_data", dataset_id)
        b2_gold, gold_errors = _collect_b2_versions("gold_model_ready_data", dataset_id)

        silver_versions = _merge_versions(local_silver, b2_silver)
        gold_versions = _merge_versions(local_gold, b2_gold)
        effective_prep_version = _choose_prep_version(
            requests,
            silver_versions,
            gold_versions,
            requested_prep_version=prep_version,
        )

        silver_entry = silver_versions.get(effective_prep_version, _version_entry())
        gold_entry = gold_versions.get(effective_prep_version, _version_entry())
        silver_files = sorted(set(silver_entry.get("files") or []))
        gold_files = sorted(set(gold_entry.get("files") or []))
        split_availability = _split_availability(gold_files)
        gold_blocks = _block_count(gold_files)
        gold_ready = gold_blocks > 0
        airflow_status = _latest_airflow_status(latest_request, include_airflow_status)
        workflow_state, message = _workflow_state(
            latest_request,
            airflow_status,
            len(silver_files),
            len(gold_files),
            gold_ready,
        )

        silver_rows = []
        if effective_prep_version and silver_files:
            meta = _safe_json(
                SILVER_ROOT / dataset_id / effective_prep_version / "processed_cloud_meta.json"
            )
            silver_rows.append(
                {
                    "dataset_id": dataset_id,
                    "prep_version": effective_prep_version,
                    "files": len(silver_files),
                    "points": meta.get("num_points") or meta.get("point_count") or "n/a",
                    "status": "available",
                }
            )

        gold_rows = []
        if effective_prep_version and gold_files:
            gold_rows.append(
                {
                    "dataset_id": dataset_id,
                    "prep_version": effective_prep_version,
                    "files": len(gold_files),
                    "blocks": gold_blocks,
                    "train": "yes" if split_availability["train"] else "no",
                    "val": "yes" if split_availability["val"] else "no",
                    "test": "yes" if split_availability["test"] else "no",
                    "status": "ready_for_training" if gold_ready else "not_ready",
                }
            )

        paths = []
        if effective_prep_version:
            paths.extend(
                [
                    {"tier": "Silver B2", "path": silver_prefix(dataset_id, effective_prep_version)},
                    {"tier": "Gold B2", "path": gold_prefix(dataset_id, effective_prep_version)},
                    {
                        "tier": "Silver local cache",
                        "path": str(SILVER_ROOT / dataset_id / effective_prep_version),
                    },
                    {
                        "tier": "Gold local cache",
                        "path": str(GOLD_ROOT / dataset_id / effective_prep_version),
                    },
                ]
            )

        summary.update(
            {
                "message": message,
                "dataset_id": dataset_id,
                "prep_version": effective_prep_version,
                "preprocessing_status": workflow_state.replace("_", " ").title(),
                "workflow_state": workflow_state,
                "silver_files": len(silver_files),
                "gold_files": len(gold_files),
                "gold_blocks": gold_blocks,
                "split_status": "Available"
                if any(split_availability.values())
                else "Pending",
                "output_status": "Available" if silver_files or gold_files else "Pending",
                "gold_ready_for_training": gold_ready,
                "split_availability": split_availability,
                "silver": silver_rows,
                "gold": gold_rows,
                "feature_schema": _feature_schema(dataset_id, effective_prep_version)
                if effective_prep_version
                else [],
                "label_schema": _label_schema(dataset_id, effective_prep_version)
                if effective_prep_version
                else [],
                "paths": paths,
                "latest_request": latest_request,
                "airflow_status": airflow_status,
                "errors": [error for error in [*silver_errors, *gold_errors] if error],
            }
        )
        return summary
    except Exception as exc:
        result = get_empty_silver_gold_summary()
        result["message"] = f"Silver/Gold summary unavailable: {exc}"
        result["errors"] = [str(exc)]
        return result


def get_gold_readiness(dataset_id, prep_version=None):
    summary = load_silver_gold_summary(dataset_id, prep_version=prep_version)
    return {
        "dataset_id": summary.get("dataset_id") or str(dataset_id or "").strip(),
        "prep_version": summary.get("prep_version") or str(prep_version or "").strip(),
        "ready": bool(summary.get("gold_ready_for_training")),
        "gold_files": summary.get("gold_files", 0),
        "gold_blocks": summary.get("gold_blocks", 0),
        "split_availability": summary.get("split_availability") or {},
        "prefix": gold_prefix(
            str(dataset_id or "").strip(),
            summary.get("prep_version") or str(prep_version or "prep_v001").strip(),
        ),
        "message": summary.get("message") or "",
    }


def get_segmentation_output_summary(dataset_id, prep_version=None, model_name=None, run_id=None):
    dataset_id = str(dataset_id or "").strip()
    prep_version = str(prep_version or "").strip()
    model_name = str(model_name or "").strip()
    run_id = str(run_id or "").strip()
    if not dataset_id:
        return {"exists": False, "message": "Please select a dataset first.", "rows": [], "file_count": 0}

    if prep_version and model_name and run_id:
        root = f"{segmentation_prefix(dataset_id, prep_version, model_name, run_id)}/"
    else:
        root = f"{b2_prefix('segmentation_outputs')}/{dataset_id}/"

    files, error = _safe_b2_list(root)
    grouped = {}
    for item in files or []:
        file_name = str(item.get("file_name") or "").strip()
        if not file_name.startswith(root):
            continue
        relative = file_name[len(root):].strip("/")
        parts = relative.split("/")
        if prep_version and model_name and run_id:
            key = (prep_version, model_name, run_id)
        elif len(parts) >= 4:
            key = (parts[0], parts[1], parts[2])
        else:
            key = ("Unknown", "Unknown", "Unknown")
        grouped.setdefault(key, []).append(file_name)

    rows = [
        {
            "prep_version": key[0],
            "model_name": key[1],
            "run_id": key[2],
            "files": len(paths),
            "prefix": segmentation_prefix(dataset_id, key[0], key[1], key[2]),
        }
        for key, paths in sorted(grouped.items())
        if paths
    ]
    file_count = sum(row["files"] for row in rows)
    exists = file_count > 0
    return {
        "exists": exists,
        "message": ""
        if exists
        else "Dataset selected, but no segmentation output has been generated yet.",
        "rows": rows,
        "file_count": file_count,
        "prefix": root.rstrip("/"),
        "error": error,
    }
