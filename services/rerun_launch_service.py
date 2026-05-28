"""
services/rerun_launch_service.py

Async Rerun job dispatch for the Data Explorer.

Each call to launch_rerun_job starts a daemon thread that runs
generate_rerun_preview() in the background so the Dash server stays
responsive during B2 download + PLY/LAS read + RRD write.

JOB_REGISTRY is module-level (works for single-process Dash deployments).
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from services.rerun_service import generate_rerun_preview
from services.rerun_viewer import open_saved_rrd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RERUN_OUTPUT_DIR = PROJECT_ROOT / "data" / "rerun_outputs"
LOCAL_DOWNLOAD_DIR = PROJECT_ROOT / "data" / "local_staging" / "rerun_downloads"
_RRD_MAX_AGE_SECS = 3600   # 1 hour
_TILE_MAX_AGE_SECS = 86400  # 24 hours — cached PLY/LAS tiles kept one day

JOB_REGISTRY: dict[str, dict[str, Any]] = {}


def _web_viewer_url(rrd_path: str) -> str:
    """
    Build an app.rerun.io link that loads the .rrd from the running Dash server.

    The DASH_PUBLIC_URL env var must be set to the Cloudflare tunnel URL
    (e.g. https://random-name.trycloudflare.com) for this link to work for
    remote users.  Falls back to localhost if not set.
    """
    import os
    filename = Path(rrd_path).name
    base = os.getenv("DASH_PUBLIC_URL", "http://localhost:8051").rstrip("/")
    rrd_url = f"{base}/api/rerun-files/{filename}"
    return f"https://app.rerun.io/?url={rrd_url}"


def launch_rerun_job(
    dataset_id: str,
    tile_items: list[dict[str, Any]],
    label_map_items: list[dict[str, Any]] | None,
    point_budget: int,
    color_mode: str,
    view_mode: str,
    open_viewer: bool = True,
) -> str:
    """
    Dispatch a Rerun recording job in a background thread.

    Returns a job_id immediately. Poll get_job_status(job_id) to track progress.

    Parameters
    ----------
    open_viewer:
        True  → save .rrd file AND open the native Rerun Viewer via subprocess.
        False → save .rrd file only ("Stream from B2" mode).
    """

    job_id = uuid.uuid4().hex
    JOB_REGISTRY[job_id] = {
        "status": "running",
        "message": "Downloading tile from B2…",
        "rrd_path": None,
        "error": None,
        "result": None,
        "started_at": time.time(),
    }
    _cleanup_old_rrds()
    _cleanup_old_downloads()

    def _run() -> None:
        try:
            JOB_REGISTRY[job_id]["message"] = "Reading point cloud fields…"
            result = generate_rerun_preview(
                dataset_id=dataset_id,
                tile_items=tile_items,
                label_map_items=label_map_items,
                point_budget=point_budget,
                color_mode=color_mode,
                view_mode=view_mode,
                open_viewer=False,
            )
            rrd_path = result["rrd_path"]
            JOB_REGISTRY[job_id]["rrd_path"] = rrd_path
            JOB_REGISTRY[job_id]["result"] = result

            if open_viewer:
                JOB_REGISTRY[job_id]["message"] = "Launching Rerun Viewer…"
                try:
                    open_saved_rrd(rrd_path)
                    viewer_msg = "Rerun Viewer opened."
                except RuntimeError as _headless_err:
                    # Headless environment (Docker) — recording succeeded but
                    # the viewer cannot open.  Surface as done, not error.
                    viewer_msg = str(_headless_err)
                JOB_REGISTRY[job_id].update({
                    "status": "done",
                    "message": viewer_msg,
                    "web_viewer_url": _web_viewer_url(rrd_path),
                })
            else:
                JOB_REGISTRY[job_id].update({
                    "status": "done",
                    "message": "Recording saved.",
                    "web_viewer_url": _web_viewer_url(rrd_path),
                })

        except Exception as exc:
            JOB_REGISTRY[job_id].update({
                "status": "error",
                "message": str(exc),
                "error": str(exc),
            })

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def get_job_status(job_id: str) -> dict[str, Any]:
    """Return the current status dict for a job, or not_found if unknown."""
    return JOB_REGISTRY.get(
        job_id,
        {"status": "not_found", "message": "Job not found or expired."},
    )


def _cleanup_old_rrds(max_age_secs: int = _RRD_MAX_AGE_SECS) -> None:
    """Delete .rrd files older than max_age_secs to prevent disk accumulation."""
    try:
        for f in RERUN_OUTPUT_DIR.glob("*.rrd"):
            try:
                if time.time() - f.stat().st_mtime > max_age_secs:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def _cleanup_old_downloads(max_age_secs: int = _TILE_MAX_AGE_SECS) -> None:
    """Delete cached PLY/LAS/LAZ tiles older than max_age_secs.

    Tiles are large (100 MB – 1 GB). Without cleanup the rerun_downloads/
    directory grows unboundedly across sessions.  24-hour TTL keeps recent
    tiles warm (fast re-launch) while preventing disk exhaustion.
    """
    try:
        for f in LOCAL_DOWNLOAD_DIR.rglob("*"):
            try:
                if f.is_file() and time.time() - f.stat().st_mtime > max_age_secs:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass
