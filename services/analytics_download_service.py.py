"""
Download analytics files from B2 after silver/gold upload tasks succeed.

Silver:  silver_preprocessed_data/{dataset}/{version}/silver/  → JSON + parquet
Gold:    gold_model_ready_data/{dataset}/{version}/eval/        → JSON
         gold_model_ready_data/{dataset}/{version}/meta/        → JSON
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_LOCAL_DIR   = Path(os.getenv("LOCAL_ANALYTICS_DIR", "data/analytics_downloads"))
_KEEP_EXTS   = {".json", ".parquet", ".csv"}


def _s3_client():
    import boto3
    from botocore.config import Config
    ep  = os.getenv("B2_ENDPOINT_URL", "")
    kid = os.getenv("B2_KEY_ID", "")
    key = os.getenv("B2_APPLICATION_KEY", "")
    if not all([ep, kid, key]):
        raise ValueError("B2 credentials not set: B2_ENDPOINT_URL / B2_KEY_ID / B2_APPLICATION_KEY")
    return boto3.client(
        "s3",
        endpoint_url=ep,
        aws_access_key_id=kid,
        aws_secret_access_key=key,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "adaptive"},
            connect_timeout=30,
            read_timeout=120,
        ),
    )


def _sync_prefix(s3, bucket: str, prefix: str, dest_dir: Path) -> tuple[list, list]:
    downloaded, errors = [], []
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(prefix):]
                if not rel or rel.endswith("/") or Path(rel).suffix.lower() not in _KEEP_EXTS:
                    continue
                dest = dest_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists() and dest.stat().st_size == obj["Size"]:
                    continue
                try:
                    s3.download_file(bucket, key, str(dest))
                    downloaded.append(rel)
                except Exception as exc:
                    errors.append(f"{rel}: {exc}")
    except Exception as exc:
        errors.append(f"list {prefix}: {exc}")
    return downloaded, errors


def download_silver_analytics(dataset_id: str, prep_version: str, run_id: str, bucket: str) -> dict:
    """Download silver analytics JSON/parquet from B2. Safe to call multiple times (size-skip)."""
    try:
        s3 = _s3_client()
    except Exception as exc:
        return {"downloaded": [], "errors": [str(exc)]}
    prefix  = f"silver_preprocessed_data/{dataset_id}/{prep_version}/silver/"
    out_dir = _LOCAL_DIR / dataset_id / prep_version / run_id / "silver"
    out_dir.mkdir(parents=True, exist_ok=True)
    dl, err = _sync_prefix(s3, bucket, prefix, out_dir)
    return {"downloaded": dl, "errors": err}


def download_gold_analytics(dataset_id: str, prep_version: str, run_id: str, bucket: str) -> dict:
    """Download gold eval + meta analytics JSON from B2."""
    try:
        s3 = _s3_client()
    except Exception as exc:
        return {"downloaded": [], "errors": [str(exc)]}
    base = _LOCAL_DIR / dataset_id / prep_version / run_id
    downloaded, errors = [], []
    for sub, prefix in [
        ("eval", f"gold_model_ready_data/{dataset_id}/{prep_version}/eval/"),
        ("meta", f"gold_model_ready_data/{dataset_id}/{prep_version}/meta/"),
    ]:
        dl, err = _sync_prefix(s3, bucket, prefix, base / sub)
        downloaded.extend(dl)
        errors.extend(err)
    return {"downloaded": downloaded, "errors": errors}
