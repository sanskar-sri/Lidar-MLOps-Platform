import os
import json
from datetime import datetime
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROGRESS_DIR = str(_PROJECT_ROOT / "data" / "upload_progress")


def ensure_progress_dir():
    os.makedirs(PROGRESS_DIR, exist_ok=True)


def normalize_dataset_id(dataset_id):
    if not dataset_id:
        return ""
    return str(dataset_id).strip()


def get_progress_path(dataset_id):
    ensure_progress_dir()

    dataset_id = normalize_dataset_id(dataset_id)

    if not dataset_id:
        raise ValueError("dataset_id is empty.")

    return os.path.join(PROGRESS_DIR, f"{dataset_id}.json")


def default_progress(dataset_id):
    return {
        "dataset_id": normalize_dataset_id(dataset_id),
        "status": "not_started",
        "stage": "idle",
        "total_files": 0,
        "uploaded_files": 0,
        "failed_files": 0,
        "current_file": "",
        "percentage": 0,
        "message": "No upload started",
        "updated_at": datetime.now().isoformat(),
    }


def save_upload_progress(dataset_id, progress):
    dataset_id = normalize_dataset_id(dataset_id)

    if not dataset_id:
        return

    path = get_progress_path(dataset_id)
    temp_path = f"{path}.tmp"

    progress["dataset_id"] = dataset_id
    progress["updated_at"] = datetime.now().isoformat()

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=4)

    os.replace(temp_path, path)


def load_upload_progress(dataset_id):
    dataset_id = normalize_dataset_id(dataset_id)

    if not dataset_id:
        return default_progress(dataset_id)

    try:
        path = get_progress_path(dataset_id)
    except ValueError:
        return default_progress(dataset_id)

    if not os.path.exists(path):
        return default_progress(dataset_id)

    try:
        with open(path, "r", encoding="utf-8") as f:
            progress = json.load(f)

        return {
            **default_progress(dataset_id),
            **progress,
        }

    except Exception as e:
        return {
            **default_progress(dataset_id),
            "status": "warning",
            "stage": "progress_read",
            "message": f"Could not read upload progress file: {str(e)}",
        }


def init_upload_progress(dataset_id, total_files):
    dataset_id = normalize_dataset_id(dataset_id)
    total_files = max(int(total_files or 0), 0)

    progress = {
        "dataset_id": dataset_id,
        "status": "started",
        "stage": "upload",
        "total_files": total_files,
        "uploaded_files": 0,
        "failed_files": 0,
        "current_file": "",
        "percentage": 0,
        "message": "Upload started",
        "updated_at": datetime.now().isoformat(),
    }

    save_upload_progress(dataset_id, progress)


def update_upload_progress(
    dataset_id,
    uploaded_files,
    total_files,
    current_file="",
    failed_files=0,
    status="uploading",
    message="Uploading files",
    stage="upload",
):
    dataset_id = normalize_dataset_id(dataset_id)

    total_files = max(int(total_files or 0), 0)
    uploaded_files = max(int(uploaded_files or 0), 0)
    failed_files = max(int(failed_files or 0), 0)

    percentage = 0

    if total_files > 0:
        percentage = round((uploaded_files / total_files) * 100, 2)

    percentage = max(0, min(percentage, 100))

    progress = {
        "dataset_id": dataset_id,
        "status": status,
        "stage": stage,
        "total_files": total_files,
        "uploaded_files": uploaded_files,
        "failed_files": failed_files,
        "current_file": current_file or "",
        "percentage": percentage,
        "message": message or "",
        "updated_at": datetime.now().isoformat(),
    }

    save_upload_progress(dataset_id, progress)


def update_metadata_progress(
    dataset_id,
    message="Generating metadata and analytics",
    percentage=95,
):
    dataset_id = normalize_dataset_id(dataset_id)

    progress = load_upload_progress(dataset_id)
    progress["status"] = "processing"
    progress["stage"] = "metadata"
    progress["percentage"] = max(0, min(float(percentage), 100))
    progress["message"] = message
    progress["updated_at"] = datetime.now().isoformat()

    save_upload_progress(dataset_id, progress)


def mark_upload_completed(dataset_id):
    dataset_id = normalize_dataset_id(dataset_id)

    progress = load_upload_progress(dataset_id)

    total_files = int(progress.get("total_files", 0))

    progress["status"] = "completed"
    progress["stage"] = "completed"
    progress["uploaded_files"] = total_files
    progress["percentage"] = 100
    progress["message"] = "Upload completed successfully"
    progress["updated_at"] = datetime.now().isoformat()

    save_upload_progress(dataset_id, progress)


def mark_upload_failed(dataset_id, message):
    dataset_id = normalize_dataset_id(dataset_id)

    progress = load_upload_progress(dataset_id)

    progress["status"] = "failed"
    progress["stage"] = "failed"
    progress["message"] = str(message)
    progress["updated_at"] = datetime.now().isoformat()

    save_upload_progress(dataset_id, progress)


def clear_upload_progress(dataset_id):
    dataset_id = normalize_dataset_id(dataset_id)

    if not dataset_id:
        return

    try:
        path = get_progress_path(dataset_id)

        if os.path.exists(path):
            os.remove(path)

    except Exception:
        pass