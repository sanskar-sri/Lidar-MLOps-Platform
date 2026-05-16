from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task


DAG_ID = "dag_health_b2"
HEALTH_INTERVAL_SECONDS = int(os.getenv("B2_HEALTH_INTERVAL_SECONDS", "90"))
B2_HEALTH_PREFIX = os.getenv("B2_HEALTH_PREFIX", "bronze_raw_data/").strip()
B2_HEALTH_MAX_FILES = int(os.getenv("B2_HEALTH_MAX_FILES", "1000"))


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _upload_timestamp_to_iso(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


@dag(
    dag_id=DAG_ID,
    description="Scheduled Backblaze B2 reachability and prefix listing health check for Dash.",
    schedule=timedelta(seconds=HEALTH_INTERVAL_SECONDS),
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(seconds=30),
    tags=["health", "b2", "dash"],
)
def dag_health_b2():
    @task(task_id="check_b2_health", execution_timeout=timedelta(seconds=30))
    def check_b2_health():
        bucket_name = os.getenv("B2_BUCKET_NAME", "").strip()
        key_id = os.getenv("B2_KEY_ID", "").strip()
        application_key = os.getenv("B2_APPLICATION_KEY", "").strip()
        prefix = B2_HEALTH_PREFIX

        if not key_id or not application_key or not bucket_name:
            return {
                "status": "not_configured",
                "bucket": bucket_name,
                "prefix": prefix,
                "file_count": None,
                "last_modified": None,
                "detail": "B2_KEY_ID, B2_APPLICATION_KEY, or B2_BUCKET_NAME is missing in Airflow.",
                "checked_at": _utc_now(),
            }

        try:
            from b2sdk.v2 import B2Api, InMemoryAccountInfo

            info = InMemoryAccountInfo()
            b2_api = B2Api(info)
            b2_api.authorize_account("production", key_id, application_key)
            bucket = b2_api.get_bucket_by_name(bucket_name)

            file_count = 0
            last_modified = None
            for file_version, _folder_name in bucket.ls(folder_to_list=prefix, recursive=True):
                if file_version is None:
                    continue
                file_count += 1
                uploaded_at = _upload_timestamp_to_iso(getattr(file_version, "upload_timestamp", None))
                if uploaded_at and (last_modified is None or uploaded_at > last_modified):
                    last_modified = uploaded_at
                if file_count >= B2_HEALTH_MAX_FILES:
                    break

            return {
                "status": "ok",
                "bucket": getattr(bucket, "name", bucket_name),
                "prefix": prefix,
                "file_count": file_count,
                "last_modified": last_modified,
                "truncated": file_count >= B2_HEALTH_MAX_FILES,
                "checked_at": _utc_now(),
            }
        except Exception as exc:
            return {
                "status": "offline",
                "bucket": bucket_name,
                "prefix": prefix,
                "file_count": None,
                "last_modified": None,
                "error": str(exc),
                "checked_at": _utc_now(),
            }

    check_b2_health()


dag_health_b2()
