import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime

from services.b2_paths import bronze_tiles_prefix, bronze_label_maps_prefix
from services.b2_service import (
    B2_BUCKET_NAME,
    SUPPORTED_LABEL_MAP_EXTENSIONS,
    SUPPORTED_TILE_EXTENSIONS,
    build_checksum_manifest,
    build_upload_manifest,
    get_b2_bucket,
    upload_json_to_b2,
    upload_local_file_to_b2_path,
)
from services.metadata_service import generate_dataset_metadata_and_analytics
from services.upload_progress import (
    mark_upload_completed,
    mark_upload_failed,
    save_upload_progress,
    update_metadata_progress,
)


BROWSER_UPLOAD_SESSION_DIR = os.getenv(
    "BROWSER_UPLOAD_SESSION_DIR",
    "data/upload_sessions",
)
BROWSER_UPLOAD_STAGING_DIR = os.getenv(
    "BROWSER_UPLOAD_STAGING_DIR",
    "data/browser_upload_staging",
)
BROWSER_UPLOAD_CHUNK_BYTES = int(
    os.getenv("BROWSER_UPLOAD_CHUNK_BYTES", str(16 * 1024 * 1024))
)
MAX_BROWSER_UPLOAD_FILES = int(os.getenv("MAX_BROWSER_UPLOAD_FILES", "250"))
BROWSER_B2_UPLOAD_MAX_ATTEMPTS = int(
    os.getenv("BROWSER_B2_UPLOAD_MAX_ATTEMPTS", "5")
)
BROWSER_B2_RETRY_BASE_SECONDS = int(
    os.getenv("BROWSER_B2_RETRY_BASE_SECONDS", "15")
)
BROWSER_B2_RETRY_MAX_SECONDS = int(
    os.getenv("BROWSER_B2_RETRY_MAX_SECONDS", "120")
)

_SESSION_LOCK = threading.RLock()
_FINALIZER_THREADS = {}


def _now():
    return datetime.now().isoformat()


def _ensure_dirs():
    os.makedirs(BROWSER_UPLOAD_SESSION_DIR, exist_ok=True)
    os.makedirs(BROWSER_UPLOAD_STAGING_DIR, exist_ok=True)


def _normalize_dataset_id(dataset_id):
    dataset_id = str(dataset_id or "").strip()
    if not dataset_id:
        raise ValueError("Dataset ID is required.")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", dataset_id):
        raise ValueError(
            "Dataset ID may contain letters, numbers, dots, dashes, and underscores only."
        )
    return dataset_id


def _normalize_filename(filename):
    filename = os.path.basename(str(filename or "").strip())
    if not filename or filename in {".", ".."}:
        raise ValueError("Each upload file needs a valid file name.")
    return filename


def _classify_file(filename):
    filename = _normalize_filename(filename)
    ext = os.path.splitext(filename)[1].lower()

    if ext in SUPPORTED_TILE_EXTENSIONS:
        return {
            "extension": ext,
            "file_role": "point_cloud_tile",
            "folder": "tiles",
        }

    if ext in SUPPORTED_LABEL_MAP_EXTENSIONS:
        return {
            "extension": ext,
            "file_role": "label_mapping",
            "folder": "label_maps",
        }

    raise ValueError(
        f"Unsupported file type for {filename}. "
        f"Tiles: {sorted(SUPPORTED_TILE_EXTENSIONS)}. "
        f"Label maps: {sorted(SUPPORTED_LABEL_MAP_EXTENSIONS)}."
    )


def _session_path(session_id):
    _ensure_dirs()
    return os.path.join(BROWSER_UPLOAD_SESSION_DIR, f"{session_id}.json")


def _save_session(session):
    with _SESSION_LOCK:
        session["updated_at"] = _now()
        temp_path = f"{_session_path(session['session_id'])}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=4)
        os.replace(temp_path, _session_path(session["session_id"]))
    return session


def load_browser_upload_session(session_id):
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required.")

    path = _session_path(session_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Upload session not found: {session_id}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_file(session, file_id):
    for item in session.get("files", []):
        if item.get("file_id") == file_id:
            return item
    raise FileNotFoundError(f"Upload file not found in session: {file_id}")


def _session_total_bytes(session):
    return sum(int(item.get("file_size_bytes") or 0) for item in session.get("files", []))


def _session_received_bytes(session):
    total = 0
    for item in session.get("files", []):
        if item.get("status") in {"staged", "b2_uploaded", "completed"}:
            total += int(item.get("file_size_bytes") or 0)
        else:
            total += int(item.get("received_bytes") or 0)
    return min(total, _session_total_bytes(session))


def _save_browser_progress(session, message=None, status=None, stage=None, percentage=None):
    total_files = len(session.get("files", []))
    staged_files = sum(
        1
        for item in session.get("files", [])
        if item.get("status") in {"staged", "b2_uploaded", "completed"}
    )
    failed_files = sum(
        1 for item in session.get("files", []) if item.get("status") == "failed"
    )
    total_bytes = _session_total_bytes(session)
    received_bytes = _session_received_bytes(session)

    if percentage is None:
        percentage = 0
        if total_bytes > 0:
            percentage = round((received_bytes / total_bytes) * 100, 2)

    current_file = ""
    for item in session.get("files", []):
        if item.get("status") not in {"staged", "b2_uploaded", "completed", "failed"}:
            current_file = item.get("filename", "")
            break

    progress = {
        "dataset_id": session["dataset_id"],
        "status": status or session.get("status", "uploading"),
        "stage": stage or session.get("stage", "browser_staging"),
        "total_files": total_files,
        "uploaded_files": staged_files,
        "failed_files": failed_files,
        "current_file": current_file,
        "percentage": max(0, min(float(percentage), 100)),
        "message": message or session.get("message", ""),
        "uploaded_bytes": received_bytes,
        "total_bytes": total_bytes,
        "session_id": session["session_id"],
        "updated_at": _now(),
    }
    save_upload_progress(session["dataset_id"], progress)


def create_browser_upload_session(payload):
    dataset_id = _normalize_dataset_id(payload.get("dataset_id"))
    dataset_name = str(payload.get("dataset_name") or dataset_id).strip()
    upload_mode = str(payload.get("upload_mode") or "browser_chunked").strip()
    description = str(payload.get("description") or "").strip()
    files = payload.get("files") or []

    if not isinstance(files, list) or not files:
        raise ValueError("Choose at least one raw tile or folder before uploading.")
    if len(files) > MAX_BROWSER_UPLOAD_FILES:
        raise ValueError(f"Too many files selected. Limit: {MAX_BROWSER_UPLOAD_FILES}.")

    session_id = uuid.uuid4().hex
    stage_dir = os.path.join(BROWSER_UPLOAD_STAGING_DIR, session_id)
    os.makedirs(stage_dir, exist_ok=True)

    session_files = []
    seen_targets = set()
    has_point_cloud_tile = False

    for index, raw_file in enumerate(files, start=1):
        filename = _normalize_filename(raw_file.get("name"))
        classified = _classify_file(filename)
        size_bytes = int(raw_file.get("size") or 0)
        if size_bytes <= 0:
            raise ValueError(f"File is empty or size is unknown: {filename}")

        has_point_cloud_tile = (
            has_point_cloud_tile or classified["file_role"] == "point_cloud_tile"
        )
        if classified['folder'] == 'tiles':
            b2_path = f"{bronze_tiles_prefix(dataset_id)}/{filename}"
        else:
            b2_path = f"{bronze_label_maps_prefix(dataset_id)}/{filename}"
        if b2_path in seen_targets:
            raise ValueError(
                f"Duplicate target file name: {filename}. Rename duplicate files before upload."
            )
        seen_targets.add(b2_path)

        file_id = uuid.uuid4().hex
        staged_name = f"{index:05d}_{file_id}_{filename}"
        staged_path = os.path.join(stage_dir, staged_name)

        session_files.append(
            {
                "file_id": file_id,
                "filename": filename,
                "relative_path": str(raw_file.get("relative_path") or filename).strip(),
                "extension": classified["extension"],
                "file_role": classified["file_role"],
                "file_size_bytes": size_bytes,
                "b2_path": b2_path,
                "local_staged_path": staged_path,
                "chunk_size_bytes": BROWSER_UPLOAD_CHUNK_BYTES,
                "total_chunks": max(1, math.ceil(size_bytes / BROWSER_UPLOAD_CHUNK_BYTES)),
                "received_chunks": [],
                "received_bytes": 0,
                "status": "pending",
                "error": "",
                "created_at": _now(),
                "updated_at": _now(),
            }
        )

    if not has_point_cloud_tile:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise ValueError(
            "Select at least one point-cloud tile (.ply, .las, .laz, etc.). "
            "XML/JSON/YAML label maps are optional companion files."
        )

    session = {
        "session_id": session_id,
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "upload_mode": upload_mode,
        "description": description,
        "status": "staging",
        "stage": "browser_staging",
        "message": "Ready to receive browser chunks",
        "bucket": B2_BUCKET_NAME,
        "chunk_size_bytes": BROWSER_UPLOAD_CHUNK_BYTES,
        "stage_dir": stage_dir,
        "files": session_files,
        "upload_results": [],
        "created_at": _now(),
        "updated_at": _now(),
    }

    _save_session(session)
    _save_browser_progress(session, "Ready to receive browser chunks", status="started")
    return session


def receive_browser_upload_chunk(form, files):
    session = load_browser_upload_session(form.get("session_id"))
    file_item = _find_file(session, form.get("file_id"))
    chunk_index = int(form.get("chunk_index") or -1)
    upload = files.get("chunk")

    if session.get("status") == "aborted":
        raise ValueError("Upload session was aborted.")
    if file_item.get("status") in {"staged", "b2_uploaded", "completed"}:
        raise ValueError(f"{file_item['filename']} is already staged.")
    if chunk_index < 0 or chunk_index >= int(file_item.get("total_chunks") or 0):
        raise ValueError("Invalid chunk index.")
    if upload is None:
        raise ValueError("Chunk payload is missing.")

    received_chunks = set(int(value) for value in file_item.get("received_chunks", []))
    if chunk_index in received_chunks:
        return {
            "file": file_item,
            "session": session,
            "duplicate": True,
        }

    chunk_size = int(file_item.get("chunk_size_bytes") or BROWSER_UPLOAD_CHUNK_BYTES)
    expected_offset = chunk_index * chunk_size
    os.makedirs(os.path.dirname(file_item["local_staged_path"]), exist_ok=True)

    mode = "r+b" if os.path.exists(file_item["local_staged_path"]) else "w+b"
    written = 0
    with open(file_item["local_staged_path"], mode) as f:
        f.seek(expected_offset)
        while True:
            block = upload.stream.read(1024 * 1024)
            if not block:
                break
            f.write(block)
            written += len(block)

    if written <= 0:
        raise ValueError("Received an empty chunk.")

    received_chunks.add(chunk_index)
    file_item["received_chunks"] = sorted(received_chunks)
    file_item["received_bytes"] = min(
        int(file_item.get("file_size_bytes") or 0),
        int(file_item.get("received_bytes") or 0) + written,
    )
    file_item["status"] = "receiving"
    file_item["updated_at"] = _now()

    session["status"] = "staging"
    session["stage"] = "browser_staging"
    session["message"] = (
        f"Received chunk {chunk_index + 1}/{file_item['total_chunks']} "
        f"for {file_item['filename']}"
    )
    _save_session(session)
    _save_browser_progress(session, session["message"], status="uploading")

    return {
        "file": file_item,
        "session": session,
        "duplicate": False,
    }


def complete_browser_upload_file(payload):
    session = load_browser_upload_session(payload.get("session_id"))
    file_item = _find_file(session, payload.get("file_id"))

    expected_chunks = int(file_item.get("total_chunks") or 0)
    received_chunks = set(int(value) for value in file_item.get("received_chunks", []))
    if len(received_chunks) != expected_chunks:
        raise ValueError(
            f"{file_item['filename']} has {len(received_chunks)}/{expected_chunks} chunks."
        )

    staged_path = file_item.get("local_staged_path")
    if not staged_path or not os.path.exists(staged_path):
        raise FileNotFoundError(f"Staged file is missing: {file_item['filename']}")

    staged_size = os.path.getsize(staged_path)
    expected_size = int(file_item.get("file_size_bytes") or 0)
    if staged_size != expected_size:
        raise ValueError(
            f"Staged size mismatch for {file_item['filename']}: "
            f"{staged_size} != {expected_size}"
        )

    file_item["status"] = "staged"
    file_item["received_bytes"] = expected_size
    file_item["staged_at"] = _now()
    file_item["updated_at"] = _now()

    if all(item.get("status") == "staged" for item in session.get("files", [])):
        session["status"] = "staged"
        session["message"] = "All files are staged locally. Ready for B2 upload."
    else:
        session["status"] = "staging"
        session["message"] = f"Staged {file_item['filename']}"

    _save_session(session)
    _save_browser_progress(session, session["message"], status=session["status"])
    return {"file": file_item, "session": session}


def _save_b2_progress(session, message, percentage, current_file="", status="uploading"):
    total_files = len(session.get("files", []))
    uploaded_files = sum(
        1 for item in session.get("files", []) if item.get("status") == "b2_uploaded"
    )
    failed_files = sum(
        1 for item in session.get("files", []) if item.get("status") == "failed"
    )
    save_upload_progress(
        session["dataset_id"],
        {
            "dataset_id": session["dataset_id"],
            "status": status,
            "stage": "b2_upload",
            "total_files": total_files,
            "uploaded_files": uploaded_files,
            "failed_files": failed_files,
            "current_file": current_file,
            "percentage": max(0, min(float(percentage), 100)),
            "message": message,
            "uploaded_bytes": _session_total_bytes(session),
            "total_bytes": _session_total_bytes(session),
            "session_id": session["session_id"],
            "updated_at": _now(),
        },
    )


def _result_for_file(upload_results, file_item):
    for result in upload_results or []:
        if result.get("b2_path") == file_item.get("b2_path"):
            return result
    return None


def _dedupe_upload_results(upload_results):
    deduped = {}
    for item in upload_results or []:
        b2_path = item.get("b2_path")
        if b2_path:
            deduped[b2_path] = item
    return list(deduped.values())


def _validate_staged_file(file_item):
    staged_path = file_item.get("local_staged_path")
    if not staged_path or not os.path.exists(staged_path):
        raise FileNotFoundError(f"Staged file is missing: {file_item.get('filename')}")

    staged_size = os.path.getsize(staged_path)
    expected_size = int(file_item.get("file_size_bytes") or 0)
    if staged_size != expected_size:
        raise ValueError(
            f"Staged size mismatch for {file_item.get('filename')}: "
            f"{staged_size} != {expected_size}"
        )


def _upload_staged_file_to_b2(session, file_item):
    _validate_staged_file(file_item)

    result = upload_local_file_to_b2_path(
        local_file_path=file_item["local_staged_path"],
        b2_path=file_item["b2_path"],
    )

    verified = False
    verified_size = None
    try:
        verified_file = get_b2_bucket().get_file_info_by_name(file_item["b2_path"])
        verified_size = getattr(verified_file, "size", None)
        verified = True
    except Exception as verify_error:
        print("=" * 80)
        print("[B2 VERIFY FAILED]")
        print(str(verify_error))
        print("=" * 80)

    return {
        "dataset_id": session["dataset_id"],
        "filename": file_item["filename"],
        "extension": file_item["extension"],
        "file_role": file_item["file_role"],
        "local_file_path": file_item["local_staged_path"],
        "b2_path": file_item["b2_path"],
        "file_size_bytes": int(file_item["file_size_bytes"]),
        "sha1": result.get("sha1", ""),
        "status": "uploaded" if verified else "uploaded_but_not_verified",
        "verified_in_b2": verified,
        "verified_size_bytes": verified_size,
        "b2_file_id": result.get("b2_file_id", ""),
        "uploaded_at": _now(),
        "upload_method": "browser_chunked_server_staged",
    }


def _upload_staged_file_to_b2_with_retries(session, file_item, index, total_files):
    last_error = None

    for attempt in range(1, BROWSER_B2_UPLOAD_MAX_ATTEMPTS + 1):
        session = load_browser_upload_session(session["session_id"])
        if session.get("status") == "aborted":
            raise RuntimeError("Upload session was aborted.")

        file_item = _find_file(session, file_item["file_id"])
        file_item["status"] = "b2_uploading"
        file_item["b2_attempt"] = attempt
        file_item["updated_at"] = _now()
        session["status"] = "b2_uploading"
        session["stage"] = "b2_upload"
        session["message"] = (
            f"Uploading {file_item['filename']} to B2 "
            f"(attempt {attempt}/{BROWSER_B2_UPLOAD_MAX_ATTEMPTS})"
        )
        _save_session(session)

        percentage = 80 + round(((index - 1) / max(total_files, 1)) * 10, 2)
        _save_b2_progress(
            session,
            session["message"],
            percentage,
            current_file=file_item["filename"],
        )

        try:
            return _upload_staged_file_to_b2(session, file_item)
        except Exception as exc:
            last_error = exc
            file_item["last_b2_error"] = str(exc)
            file_item["updated_at"] = _now()
            session["last_b2_error"] = str(exc)

            if attempt >= BROWSER_B2_UPLOAD_MAX_ATTEMPTS:
                _save_session(session)
                break

            delay = min(
                BROWSER_B2_RETRY_MAX_SECONDS,
                BROWSER_B2_RETRY_BASE_SECONDS * attempt,
            )
            session["message"] = (
                f"B2 upload failed for {file_item['filename']} "
                f"(attempt {attempt}/{BROWSER_B2_UPLOAD_MAX_ATTEMPTS}): {exc}. "
                f"Retrying in {delay}s."
            )
            _save_session(session)
            _save_b2_progress(
                session,
                session["message"],
                percentage,
                current_file=file_item["filename"],
                status="uploading",
            )
            time.sleep(delay)

    raise RuntimeError(
        f"B2 upload failed for {file_item.get('filename')} after "
        f"{BROWSER_B2_UPLOAD_MAX_ATTEMPTS} attempts: {last_error}"
    )


def _finalize_session_worker(session_id):
    session = load_browser_upload_session(session_id)
    dataset_id = session["dataset_id"]
    upload_results = _dedupe_upload_results(session.get("upload_results", []))

    try:
        total_files = len(session.get("files", []))
        for index, file_item in enumerate(session.get("files", []), start=1):
            session = load_browser_upload_session(session_id)
            if session.get("status") == "aborted":
                raise RuntimeError("Upload session was aborted.")

            file_item = _find_file(session, file_item["file_id"])
            existing_result = _result_for_file(upload_results, file_item)
            if file_item.get("status") == "b2_uploaded" and existing_result:
                done_percentage = 80 + round((index / max(total_files, 1)) * 10, 2)
                _save_b2_progress(
                    session,
                    f"Already uploaded {file_item['filename']} to B2",
                    done_percentage,
                    current_file=file_item["filename"],
                )
                continue

            result = _upload_staged_file_to_b2_with_retries(
                session,
                file_item,
                index,
                total_files,
            )
            upload_results = [
                item for item in upload_results if item.get("b2_path") != result["b2_path"]
            ]
            upload_results.append(result)

            session = load_browser_upload_session(session_id)
            file_item = _find_file(session, file_item["file_id"])
            file_item["status"] = "b2_uploaded"
            file_item["b2_uploaded_at"] = _now()
            file_item["updated_at"] = _now()
            session["upload_results"] = upload_results
            session["message"] = f"Uploaded {file_item['filename']} to B2"
            _save_session(session)

            done_percentage = 80 + round((index / max(total_files, 1)) * 10, 2)
            _save_b2_progress(
                session,
                session["message"],
                done_percentage,
                current_file=file_item["filename"],
            )

        upload_manifest = build_upload_manifest(dataset_id, upload_results)
        upload_manifest["dataset_name"] = session["dataset_name"]
        upload_manifest["upload_method"] = "browser_chunked_server_staged"
        checksum_manifest = build_checksum_manifest(dataset_id, upload_results)
        checksum_manifest["upload_method"] = "browser_chunked_server_staged"

        upload_json_to_b2(
            dataset_id=dataset_id,
            object_name="upload_manifest.json",
            payload=upload_manifest,
        )
        upload_json_to_b2(
            dataset_id=dataset_id,
            object_name="checksum_manifest.json",
            payload=checksum_manifest,
        )

        update_metadata_progress(
            dataset_id,
            message="Upload complete. Generating metadata and analytics.",
            percentage=92,
        )
        point_cloud_filenames = [
            item["filename"]
            for item in upload_results
            if item.get("file_role") == "point_cloud_tile"
        ]
        generate_dataset_metadata_and_analytics(
            dataset_id=dataset_id,
            dataset_name=session["dataset_name"],
            upload_mode=session["upload_mode"],
            description=session.get("description", ""),
            filenames=point_cloud_filenames,
            uploaded_files=upload_results,
        )

        session = load_browser_upload_session(session_id)
        for item in session.get("files", []):
            item["status"] = "completed"
        session["status"] = "completed"
        session["stage"] = "completed"
        session["message"] = "Upload, B2 verification, and metadata completed."
        session["completed_at"] = _now()
        _save_session(session)
        mark_upload_completed(dataset_id)
        shutil.rmtree(session.get("stage_dir", ""), ignore_errors=True)

    except Exception as exc:
        session = load_browser_upload_session(session_id)
        session["status"] = "failed"
        session["stage"] = "failed"
        session["message"] = str(exc)
        session["error"] = str(exc)
        _save_session(session)
        mark_upload_failed(dataset_id, str(exc))


def complete_browser_upload_session(payload):
    session = load_browser_upload_session(payload.get("session_id"))

    if not session.get("files"):
        raise ValueError("Upload session has no files.")

    allowed_statuses = {"staged", "b2_uploading", "b2_uploaded", "failed"}
    for item in session["files"]:
        if item.get("status") not in allowed_statuses:
            raise ValueError("All files must be staged before B2 upload can start.")
        if item.get("status") != "b2_uploaded":
            _validate_staged_file(item)

    session["status"] = "queued"
    session["stage"] = "b2_upload"
    session["message"] = "Files staged. B2 upload is running in the background."
    session["queued_at"] = _now()
    _save_session(session)
    _save_b2_progress(session, session["message"], 80, status="uploading")

    with _SESSION_LOCK:
        thread = _FINALIZER_THREADS.get(session["session_id"])
        if thread is None or not thread.is_alive():
            thread = threading.Thread(
                target=_finalize_session_worker,
                args=(session["session_id"],),
                daemon=True,
            )
            _FINALIZER_THREADS[session["session_id"]] = thread
            thread.start()

    return session


def abort_browser_upload_session(payload):
    session = load_browser_upload_session(payload.get("session_id"))
    was_b2_uploading = session.get("status") == "b2_uploading"
    session["status"] = "aborted"
    session["stage"] = "aborted"
    session["message"] = "Browser upload session aborted"
    session["aborted_at"] = _now()
    for item in session.get("files", []):
        if item.get("status") not in {"b2_uploaded", "completed"}:
            item["status"] = "aborted"
    _save_session(session)
    _save_browser_progress(session, session["message"], status="failed", stage="aborted")
    if not was_b2_uploading:
        shutil.rmtree(session.get("stage_dir", ""), ignore_errors=True)
    return session
