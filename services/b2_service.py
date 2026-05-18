import os
import json
import base64
import hashlib
import time
from datetime import datetime

from dotenv import load_dotenv
from b2sdk.v2 import InMemoryAccountInfo, B2Api

from services.upload_progress import (
    init_upload_progress,
    update_upload_progress,
    mark_upload_completed,
    mark_upload_failed,
)

load_dotenv()

B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME", "Building-Identification-MLS")
LOCAL_STAGING_DIR = os.getenv("LOCAL_STAGING_DIR", "data/local_staging")


SUPPORTED_TILE_EXTENSIONS = {
    ".ply",
    ".las",
    ".laz",
    ".pts",
    ".xyz",
    ".txt",
    ".csv",
}

SUPPORTED_LABEL_MAP_EXTENSIONS = {
    ".xml",
    ".json",
    ".yaml",
    ".yml",
}


_B2_BUCKET_CACHE = None


# -------------------------------------------------------------------
# B2 connection
# -------------------------------------------------------------------

def get_b2_bucket():
    """
    Connect to Backblaze B2 and return the configured bucket.
    Uses a simple in-process cache to avoid repeated authorization calls.
    """

    global _B2_BUCKET_CACHE

    if _B2_BUCKET_CACHE is not None:
        return _B2_BUCKET_CACHE

    if not B2_KEY_ID or not B2_APPLICATION_KEY:
        raise ValueError("B2 credentials are missing. Please check your .env file.")

    if not B2_BUCKET_NAME:
        raise ValueError("B2_BUCKET_NAME is missing. Please check your .env file.")

    info = InMemoryAccountInfo()
    b2_api = B2Api(info)

    print("=" * 80)
    print("[B2 AUTHORIZATION]")
    print(f"Bucket name from .env: {B2_BUCKET_NAME}")
    print("=" * 80)

    b2_api.authorize_account(
        "production",
        B2_KEY_ID,
        B2_APPLICATION_KEY,
    )

    bucket = b2_api.get_bucket_by_name(B2_BUCKET_NAME)

    print("[B2 BUCKET FOUND]")
    print(f"Bucket name: {bucket.name}")
    print("=" * 80)

    _B2_BUCKET_CACHE = bucket

    return bucket


# -------------------------------------------------------------------
# Utility functions
# -------------------------------------------------------------------

def calculate_sha1(file_path, chunk_size=1024 * 1024 * 4):
    """
    Calculate SHA-1 in 4 MB chunks using raw (unbuffered) I/O.
    Unbuffered mode avoids Python's BufferedReader which can trigger EDEADLK
    (errno 35 on Linux) when reading from macOS Docker VirtioFS/FUSE mounts.
    """
    sha1 = hashlib.sha1()
    with open(file_path, "rb", buffering=0) as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha1.update(chunk)
    return sha1.hexdigest()


def normalize_local_path(path):
    """
    Clean quotes and spaces from a user-entered local path.
    """

    if not path:
        return ""

    return str(path).strip().strip('"').strip("'")


def safe_file_id(file_obj):
    """
    Safely get B2 file id from SDK objects.
    """

    return getattr(file_obj, "id_", None) or getattr(file_obj, "id", None) or ""


def safe_file_name(file_obj):
    """
    Safely get B2 file name from SDK objects.
    """

    return getattr(file_obj, "file_name", None) or getattr(file_obj, "name", None) or ""


def get_b2_destination_path(dataset_id, local_file_path):
    """
    Decide the correct B2 destination path based on file extension.

    Point cloud tiles:
        bronze_raw_data/<dataset_id>/source_files/tiles/<filename>

    Label maps:
        bronze_raw_data/<dataset_id>/source_files/label_maps/<filename>
    """

    filename = os.path.basename(local_file_path)
    ext = os.path.splitext(filename)[1].lower()

    if ext in SUPPORTED_TILE_EXTENSIONS:
        file_role = "point_cloud_tile"
        b2_path = f"bronze_raw_data/{dataset_id}/source_files/tiles/{filename}"

    elif ext in SUPPORTED_LABEL_MAP_EXTENSIONS:
        file_role = "label_mapping"
        b2_path = f"bronze_raw_data/{dataset_id}/source_files/label_maps/{filename}"

    else:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Supported tile types: {sorted(SUPPORTED_TILE_EXTENSIONS)}. "
            f"Supported label-map types: {sorted(SUPPORTED_LABEL_MAP_EXTENSIONS)}."
        )

    return filename, ext, file_role, b2_path


# -------------------------------------------------------------------
# Standard upload functions
# -------------------------------------------------------------------

def _stage_host_file(src_path, suffix):
    """
    Copy a file from a Docker host-mount (e.g. /datasets/…) to a temp file
    on the container's own filesystem before any SHA-1 or B2 SDK operations.

    Uses subprocess cp so the copy runs in a fresh process with its own file
    descriptors — this avoids the VirtioFS FUSE errno-35 (EDEADLK on Linux /
    EAGAIN on macOS) that permanently breaks the parent process's fd table for
    sequential reads off the mount after ~2 large files.
    """
    import subprocess
    import tempfile

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp")
    tmp.close()
    print(f"[STAGE] {src_path} -> {tmp.name}")
    subprocess.run(["cp", "--", src_path, tmp.name], check=True, timeout=7200)
    print(f"[STAGE] done ({os.path.getsize(tmp.name):,} bytes)")
    return tmp.name


def upload_local_file_to_b2_standard(dataset_id, local_file_path):
    """
    Upload one file to the standard bronze_raw_data structure.

    - .ply/.las/.laz/.pts/.xyz/.txt/.csv -> source_files/tiles/
    - .xml/.json/.yaml/.yml             -> source_files/label_maps/

    Files from Docker host mounts (/datasets/…) are staged to /tmp first so
    that SHA-1 and the B2 SDK never read directly from the VirtioFS FUSE layer,
    which breaks after 2 large sequential reads (errno 35 EDEADLK/EAGAIN).
    """

    if not dataset_id:
        raise ValueError("Dataset ID is empty.")

    dataset_id = dataset_id.strip()
    local_file_path = normalize_local_path(local_file_path)

    if not local_file_path:
        raise ValueError("Local file path is empty.")

    if not os.path.exists(local_file_path):
        raise FileNotFoundError(f"File not found: {local_file_path}")

    if not os.path.isfile(local_file_path):
        raise ValueError(f"Path is not a file: {local_file_path}")

    filename, ext, file_role, b2_path = get_b2_destination_path(
        dataset_id=dataset_id,
        local_file_path=local_file_path,
    )

    file_size = os.path.getsize(local_file_path)

    print("=" * 80)
    print("[LOCAL FILE CHECK]")
    print(f"Dataset ID : {dataset_id}")
    print(f"Local file : {local_file_path}")
    print(f"File name  : {filename}")
    print(f"Extension  : {ext}")
    print(f"File role  : {file_role}")
    print(f"File size  : {file_size:,} bytes")
    print(f"B2 path    : {b2_path}")
    print("=" * 80)

    # Stage to container-local /tmp before SHA-1 and B2 SDK reads.
    staged_path = None
    read_path = local_file_path
    if local_file_path.startswith("/datasets"):
        staged_path = _stage_host_file(local_file_path, ext)
        read_path = staged_path

    try:
        print("[SHA-1 STARTED]")
        sha1_checksum = calculate_sha1(read_path)
        print("[SHA-1 COMPLETED]")
        print(f"SHA-1: {sha1_checksum}")
        print("=" * 80)

        bucket = get_b2_bucket()

        print("[B2 UPLOAD STARTED]")
        print("Please wait. Large files may take time.")
        print("=" * 80)

        uploaded_file = bucket.upload_local_file(
            local_file=read_path,
            file_name=b2_path,
            sha1_sum=sha1_checksum,
        )
    finally:
        if staged_path:
            try:
                os.unlink(staged_path)
            except Exception:
                pass

    uploaded_file_id = safe_file_id(uploaded_file)
    uploaded_file_name = safe_file_name(uploaded_file) or b2_path

    print("[B2 UPLOAD FINISHED]")
    print(f"B2 file ID : {uploaded_file_id}")
    print(f"B2 file    : {uploaded_file_name}")
    print("=" * 80)

    verified = False
    verified_size = None

    try:
        verified_file = bucket.get_file_info_by_name(b2_path)
        verified_size = getattr(verified_file, "size", None)

        print("[B2 VERIFY SUCCESS]")
        print(f"Verified file : {b2_path}")
        print(f"Verified size : {verified_size}")
        print("=" * 80)

        verified = True

    except Exception as verify_error:
        print("[B2 VERIFY FAILED]")
        print(str(verify_error))
        print("=" * 80)

    return {
        "dataset_id": dataset_id,
        "filename": filename,
        "extension": ext,
        "file_role": file_role,
        "local_file_path": local_file_path,
        "b2_path": b2_path,
        "file_size_bytes": file_size,
        "sha1": sha1_checksum,
        "status": "uploaded" if verified else "uploaded_but_not_verified",
        "verified_in_b2": verified,
        "verified_size_bytes": verified_size,
        "b2_file_id": uploaded_file_id,
        "uploaded_at": datetime.now().isoformat(),
    }


def upload_large_file_to_b2(
    dataset_id,
    local_file_path,
    total_files=1,
    file_index=1,
    reset_progress=True,
    complete_progress=True,
):
    """
    Used by Data Explorer for one large tile or one label-map file.
    This also updates progress JSON and can participate in a small local-path
    batch, such as one tile plus one label map.
    """

    if not dataset_id:
        raise ValueError("Dataset ID is empty.")

    dataset_id = dataset_id.strip()
    local_file_path = normalize_local_path(local_file_path)
    filename = os.path.basename(local_file_path) if local_file_path else ""
    total_files = max(int(total_files or 1), 1)
    file_index = max(1, min(int(file_index or 1), total_files))

    if reset_progress:
        init_upload_progress(dataset_id, total_files=total_files)

    try:
        update_upload_progress(
            dataset_id=dataset_id,
            uploaded_files=file_index - 1,
            total_files=total_files,
            current_file=filename,
            failed_files=0,
            status="uploading",
            message=f"Uploading {filename}",
        )

        result = upload_local_file_to_b2_standard(
            dataset_id=dataset_id,
            local_file_path=local_file_path,
        )

        update_upload_progress(
            dataset_id=dataset_id,
            uploaded_files=file_index,
            total_files=total_files,
            current_file=filename,
            failed_files=0,
            status="uploading",
            message=f"Uploaded {filename}",
        )

        if complete_progress:
            mark_upload_completed(dataset_id)

        return result

    except Exception as e:
        mark_upload_failed(dataset_id, str(e))
        raise


def save_base64_upload_locally(dataset_id, filename, file_content_base64):
    """
    Small-file upload helper for Dash dcc.Upload.
    Do not use this for large MLS/LiDAR files.
    """

    os.makedirs(
        os.path.join(LOCAL_STAGING_DIR, dataset_id),
        exist_ok=True,
    )

    header, encoded = file_content_base64.split(",", 1)
    file_bytes = base64.b64decode(encoded)

    local_path = os.path.join(
        LOCAL_STAGING_DIR,
        dataset_id,
        filename,
    )

    with open(local_path, "wb") as f:
        f.write(file_bytes)

    return local_path


def upload_file_to_b2(dataset_id, filename, file_content_base64):
    """
    Small file upload through Dash dcc.Upload.
    Uses standard bronze structure based on file type.
    """

    local_path = save_base64_upload_locally(
        dataset_id=dataset_id,
        filename=filename,
        file_content_base64=file_content_base64,
    )

    return upload_local_file_to_b2_standard(
        dataset_id=dataset_id,
        local_file_path=local_path,
    )


# -------------------------------------------------------------------
# Folder upload functions with progress
# -------------------------------------------------------------------

def find_supported_files_in_folder(folder_path):
    """
    Find all supported raw files inside a local folder.
    """

    folder_path = normalize_local_path(folder_path)

    if not folder_path:
        raise ValueError("Folder path is empty.")

    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    if not os.path.isdir(folder_path):
        raise ValueError(f"Path is not a folder: {folder_path}")

    supported_files = []

    all_supported_extensions = SUPPORTED_TILE_EXTENSIONS.union(
        SUPPORTED_LABEL_MAP_EXTENSIONS
    )

    for root, dirs, files in os.walk(folder_path):
        for file in files:
            ext = os.path.splitext(file)[1].lower()

            if ext in all_supported_extensions:
                full_path = os.path.join(root, file)
                supported_files.append(full_path)

    supported_files.sort()

    print("=" * 80)
    print("[FOLDER SCAN COMPLETED]")
    print(f"Folder: {folder_path}")
    print(f"Supported files found: {len(supported_files)}")

    for path in supported_files:
        print(path)

    print("=" * 80)

    return supported_files


def upload_folder_to_b2(dataset_id, folder_path, complete_progress=True):
    """
    Upload all supported files from a local folder to the standard B2 structure.
    Updates data/upload_progress/<dataset_id>.json.
    """

    if not dataset_id:
        raise ValueError("Dataset ID is empty.")

    dataset_id = dataset_id.strip()

    supported_files = find_supported_files_in_folder(folder_path)

    if not supported_files:
        raise ValueError("No supported point cloud or label-map files found in the folder.")

    total_files = len(supported_files)

    init_upload_progress(dataset_id, total_files)

    results = []
    failed_files = 0
    failed_file_details = []

    try:
        for index, local_file_path in enumerate(supported_files, start=1):
            current_file = os.path.basename(local_file_path)

            update_upload_progress(
                dataset_id=dataset_id,
                uploaded_files=index - 1,
                total_files=total_files,
                current_file=current_file,
                failed_files=failed_files,
                status="uploading",
                message=f"Uploading {current_file}",
            )

            print("=" * 80)
            print(f"[FOLDER UPLOAD] File {index}/{total_files}")
            print(local_file_path)
            print("=" * 80)

            max_attempts = 3
            retry_delays = [5, 15]
            last_error = None

            for attempt in range(1, max_attempts + 1):
                try:
                    result = upload_local_file_to_b2_standard(
                        dataset_id=dataset_id,
                        local_file_path=local_file_path,
                    )

                    results.append(result)
                    last_error = None

                    update_upload_progress(
                        dataset_id=dataset_id,
                        uploaded_files=index,
                        total_files=total_files,
                        current_file=current_file,
                        failed_files=failed_files,
                        status="uploading",
                        message=f"Uploaded {current_file}",
                    )

                    break

                except Exception as file_error:
                    last_error = file_error

                    print("=" * 80)
                    print(f"[FILE UPLOAD FAILED — attempt {attempt}/{max_attempts}]")
                    print(f"File : {local_file_path}")
                    print(f"Error: {file_error}")
                    print("=" * 80)

                    if attempt < max_attempts:
                        delay = retry_delays[attempt - 1]
                        print(f"Retrying in {delay}s...")
                        time.sleep(delay)

            if last_error is not None:
                failed_files += 1
                failed_file_details.append({
                    "file": current_file,
                    "path": local_file_path,
                    "error": str(last_error),
                })

                update_upload_progress(
                    dataset_id=dataset_id,
                    uploaded_files=index - 1,
                    total_files=total_files,
                    current_file=current_file,
                    failed_files=failed_files,
                    status="uploading",
                    message=f"Skipped {current_file} after {max_attempts} attempts: {str(last_error)}",
                )

                print("=" * 80)
                print(f"[FILE SKIPPED] All {max_attempts} attempts exhausted.")
                print(f"File : {local_file_path}")
                print("=" * 80)

        if failed_file_details:
            print("=" * 80)
            print(f"[UPLOAD SUMMARY] {failed_files} file(s) failed:")
            for item in failed_file_details:
                print(f"  - {item['file']}: {item['error']}")
            print("=" * 80)

        if not results:
            failed_names = ", ".join(d["file"] for d in failed_file_details)
            raise RuntimeError(
                f"All {total_files} file(s) failed to upload. Failed tiles: {failed_names}"
            )

        upload_manifest = build_upload_manifest(dataset_id, results)
        checksum_manifest = build_checksum_manifest(dataset_id, results)

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

        if complete_progress:
            if failed_file_details:
                failed_names = ", ".join(d["file"] for d in failed_file_details)
                mark_upload_completed(dataset_id)
                update_upload_progress(
                    dataset_id=dataset_id,
                    uploaded_files=total_files - failed_files,
                    total_files=total_files,
                    current_file="",
                    failed_files=failed_files,
                    status="completed_with_errors",
                    message=(
                        f"Upload finished. {total_files - failed_files}/{total_files} succeeded. "
                        f"Failed tiles: {failed_names}"
                    ),
                )
            else:
                mark_upload_completed(dataset_id)

        return results

    except Exception as e:
        mark_upload_failed(dataset_id, str(e))
        raise


# -------------------------------------------------------------------
# Manifest functions
# -------------------------------------------------------------------

def build_upload_manifest(dataset_id, upload_results):
    """
    Create upload_manifest.json content.
    """

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_id,
        "uploaded_at": datetime.now().isoformat(),
        "bucket": B2_BUCKET_NAME,
        "raw_tile_prefix": f"bronze_raw_data/{dataset_id}/source_files/tiles/",
        "label_map_prefix": f"bronze_raw_data/{dataset_id}/source_files/label_maps/",
        "manifest_prefix": f"bronze_raw_data/{dataset_id}/manifests/",
        "files": [
            {
                "file_name": item["filename"],
                "b2_path": item["b2_path"],
                "file_role": item["file_role"],
                "format": item["extension"].replace(".", ""),
                "file_size_bytes": item["file_size_bytes"],
                "uploaded_at": item["uploaded_at"],
                "verified_in_b2": item["verified_in_b2"],
            }
            for item in upload_results
        ],
    }


def build_checksum_manifest(dataset_id, upload_results):
    """
    Create checksum_manifest.json content.
    """

    return {
        "dataset_id": dataset_id,
        "checksum_algorithm": "sha1",
        "created_at": datetime.now().isoformat(),
        "files": [
            {
                "file_name": item["filename"],
                "b2_path": item["b2_path"],
                "file_role": item["file_role"],
                "sha1": item["sha1"],
                "file_size_bytes": item["file_size_bytes"],
                "verified_in_b2": item["verified_in_b2"],
                "verified_size_bytes": item["verified_size_bytes"],
                "b2_file_id": item["b2_file_id"],
            }
            for item in upload_results
        ],
    }


def upload_json_to_b2(dataset_id, object_name, payload):
    """
    Upload a JSON manifest to:
        bronze_raw_data/<dataset_id>/manifests/<object_name>
    """

    os.makedirs(
        os.path.join(LOCAL_STAGING_DIR, dataset_id, "manifests"),
        exist_ok=True,
    )

    local_json_path = os.path.join(
        LOCAL_STAGING_DIR,
        dataset_id,
        "manifests",
        object_name,
    )

    with open(local_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4)

    b2_path = f"bronze_raw_data/{dataset_id}/manifests/{object_name}"

    result = upload_local_file_to_b2_path(
        local_file_path=local_json_path,
        b2_path=b2_path,
    )

    print("=" * 80)
    print("[MANIFEST UPLOADED]")
    print(f"Local: {local_json_path}")
    print(f"B2   : {b2_path}")
    print(f"ID   : {result['b2_file_id']}")
    print("=" * 80)

    return result


# -------------------------------------------------------------------
# Metadata / analytics upload helpers
# -------------------------------------------------------------------

def upload_local_file_to_b2_path(local_file_path, b2_path):
    """
    Upload any local file to an explicit B2 path.
    """

    if not local_file_path:
        raise ValueError("local_file_path is empty.")

    if not b2_path:
        raise ValueError("b2_path is empty.")

    local_file_path = normalize_local_path(local_file_path)

    if not os.path.exists(local_file_path):
        raise FileNotFoundError(local_file_path)

    if not os.path.isfile(local_file_path):
        raise ValueError(f"Path is not a file: {local_file_path}")

    sha1_checksum = calculate_sha1(local_file_path)

    bucket = get_b2_bucket()

    uploaded_file = bucket.upload_local_file(
        local_file=local_file_path,
        file_name=b2_path,
        sha1_sum=sha1_checksum,
    )

    uploaded_file_id = safe_file_id(uploaded_file)

    print("=" * 80)
    print("[B2 EXPLICIT PATH UPLOAD]")
    print(f"Local: {local_file_path}")
    print(f"B2   : {b2_path}")
    print(f"ID   : {uploaded_file_id}")
    print("=" * 80)

    return {
        "local_file_path": local_file_path,
        "b2_path": b2_path,
        "b2_file_id": uploaded_file_id,
        "sha1": sha1_checksum,
    }


def upload_local_directory_to_b2(local_dir, b2_prefix):
    """
    Upload every file from a local directory to a B2 prefix.
    """

    if not local_dir:
        raise ValueError("local_dir is empty.")

    if not b2_prefix:
        raise ValueError("b2_prefix is empty.")

    local_dir = normalize_local_path(local_dir)

    if not os.path.exists(local_dir):
        raise FileNotFoundError(local_dir)

    if not os.path.isdir(local_dir):
        raise ValueError(f"Path is not a directory: {local_dir}")

    uploaded = []

    for root, dirs, files in os.walk(local_dir):
        for file in files:
            local_path = os.path.join(root, file)

            rel_path = os.path.relpath(
                local_path,
                local_dir,
            ).replace("\\", "/")

            b2_path = f"{b2_prefix.rstrip('/')}/{rel_path}"

            result = upload_local_file_to_b2_path(
                local_file_path=local_path,
                b2_path=b2_path,
            )

            uploaded.append(result)

    return uploaded


# -------------------------------------------------------------------
# B2 list/download functions for metadata extraction and Rerun
# -------------------------------------------------------------------

def list_b2_files_with_prefix(prefix):
    """
    List files in B2 under a prefix.
    """

    if not prefix:
        raise ValueError("prefix is empty.")

    bucket = get_b2_bucket()

    files = []

    for file_version, folder_name in bucket.ls(
        folder_to_list=prefix,
        recursive=True,
    ):
        if file_version is not None:
            files.append(
                {
                    "file_name": file_version.file_name,
                    "size": getattr(file_version, "size", None),
                    "id": safe_file_id(file_version),
                }
            )

    return files


def get_b2_file_info_by_name(b2_file_name):
    """
    Return metadata for one B2 file by object key/name.
    """

    if not b2_file_name:
        raise ValueError("b2_file_name is empty.")

    bucket = get_b2_bucket()
    file_info = bucket.get_file_info_by_name(b2_file_name)

    return {
        "file_name": getattr(file_info, "file_name", b2_file_name),
        "size": getattr(file_info, "size", None),
        "id": safe_file_id(file_info),
    }


def download_b2_file_to_local(
    b2_file_name=None,
    local_output_path=None,
    b2_key=None,
    local_path=None,
):
    """
    Download one real B2 file to a local temporary path.

    Supports both call styles:

        download_b2_file_to_local(
            b2_file_name="bronze_raw_data/...",
            local_output_path="data/local_staging/..."
        )

    and:

        download_b2_file_to_local(
            b2_key="bronze_raw_data/...",
            local_path="data/local_staging/..."
        )

    This is used by metadata extraction and Rerun preview generation.
    """

    if b2_key and not b2_file_name:
        b2_file_name = b2_key

    if local_path and not local_output_path:
        local_output_path = local_path

    if not b2_file_name:
        raise ValueError("b2_file_name / b2_key is empty.")

    if not local_output_path:
        raise ValueError("local_output_path / local_path is empty.")

    b2_file_name = str(b2_file_name).strip()
    local_output_path = normalize_local_path(local_output_path)

    os.makedirs(
        os.path.dirname(local_output_path),
        exist_ok=True,
    )

    bucket = get_b2_bucket()

    print("=" * 80)
    print("[B2 DOWNLOAD STARTED]")
    print(f"B2 file : {b2_file_name}")
    print(f"Local   : {local_output_path}")
    print("=" * 80)

    downloaded_file = bucket.download_file_by_name(b2_file_name)
    downloaded_file.save_to(local_output_path)

    if not os.path.exists(local_output_path):
        raise RuntimeError(f"B2 download failed. Local file was not created: {local_output_path}")

    if os.path.getsize(local_output_path) == 0:
        raise RuntimeError(f"B2 download failed. Local file is empty: {local_output_path}")

    print("[B2 DOWNLOAD FINISHED]")
    print(f"Size: {os.path.getsize(local_output_path):,} bytes")
    print("=" * 80)

    return local_output_path


def get_b2_tiles_for_dataset(dataset_id):
    """
    Return all point cloud tiles stored in:
        bronze_raw_data/<dataset_id>/source_files/tiles/
    """

    prefix = f"bronze_raw_data/{dataset_id}/source_files/tiles/"
    files = list_b2_files_with_prefix(prefix)

    supported = []

    for item in files:
        ext = os.path.splitext(item["file_name"])[1].lower()

        if ext in SUPPORTED_TILE_EXTENSIONS:
            supported.append(item)

    return supported


def get_b2_label_maps_for_dataset(dataset_id):
    """
    Return all label mapping files stored in:
        bronze_raw_data/<dataset_id>/source_files/label_maps/
    """

    prefix = f"bronze_raw_data/{dataset_id}/source_files/label_maps/"
    files = list_b2_files_with_prefix(prefix)

    supported = []

    for item in files:
        ext = os.path.splitext(item["file_name"])[1].lower()

        if ext in SUPPORTED_LABEL_MAP_EXTENSIONS:
            supported.append(item)

    return supported


# -------------------------------------------------------------------
# Delete functions
# -------------------------------------------------------------------

def delete_b2_prefix(prefix):
    """
    Delete all B2 files under a prefix.
    """

    if not prefix:
        raise ValueError("prefix is empty.")

    bucket = get_b2_bucket()

    deleted = []

    for file_version, folder_name in bucket.ls(
        folder_to_list=prefix,
        recursive=True,
    ):
        if file_version is not None:
            file_id = safe_file_id(file_version)
            file_name = file_version.file_name

            bucket.delete_file_version(
                file_id,
                file_name,
            )

            deleted.append(file_name)

            print("=" * 80)
            print("[B2 FILE DELETED]")
            print(file_name)
            print("=" * 80)

    return deleted


def delete_b2_file_by_name(b2_file_name):
    """
    Delete one B2 file by exact object name.
    """

    if not b2_file_name:
        raise ValueError("b2_file_name is empty.")

    bucket = get_b2_bucket()

    file_info = bucket.get_file_info_by_name(b2_file_name)
    file_id = safe_file_id(file_info)
    file_name = file_info.file_name

    bucket.delete_file_version(
        file_id,
        file_name,
    )

    print("=" * 80)
    print("[B2 FILE DELETED]")
    print(file_name)
    print("=" * 80)

    return file_name
