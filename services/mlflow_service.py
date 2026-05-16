import json
import os
from urllib import error, request

from dotenv import load_dotenv


load_dotenv()


MLFLOW_INTERNAL_URL = os.getenv("MLFLOW_INTERNAL_URL", "").strip()
MLFLOW_PUBLIC_URL = os.getenv("MLFLOW_PUBLIC_URL", "http://localhost:5000").strip()
DEFAULT_MLFLOW_TRACKING_URI = (
    os.getenv("MLFLOW_TRACKING_URI", "").strip()
    or MLFLOW_PUBLIC_URL
    or "./mlruns"
)
DEFAULT_TRAINING_MLFLOW_TRACKING_URI = (
    os.getenv("TRAINING_MLFLOW_TRACKING_URI", "").strip()
    or "http://100.88.150.103:5003"
)


def mlflow_browser_url(value, fallback="#"):
    uri = str(value or "").strip()
    if uri.startswith(("http://", "https://")):
        return uri
    return fallback


def _short_detail(value, limit=160):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _candidate_urls():
    urls = []
    for value in [MLFLOW_INTERNAL_URL, MLFLOW_PUBLIC_URL]:
        if value and value not in urls:
            urls.append(value)
    return urls


def _status(status, detail, tone, url=None):
    return {
        "service": "MLflow",
        "status": status,
        "detail": _short_detail(detail),
        "tone": tone,
        "url": url or MLFLOW_PUBLIC_URL,
        "public_url": MLFLOW_PUBLIC_URL,
        "tracking_uri": DEFAULT_MLFLOW_TRACKING_URI,
        "training_tracking_uri": DEFAULT_TRAINING_MLFLOW_TRACKING_URI,
    }


def _read_health(url, timeout_seconds):
    health_url = f"{url.rstrip('/')}/health"
    req = request.Request(health_url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace").strip()
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}


def check_mlflow_service(timeout_seconds=3):
    urls = _candidate_urls()
    if not urls:
        return _status(
            "Not Configured",
            "Set MLFLOW_PUBLIC_URL or MLFLOW_INTERNAL_URL to track the MLflow server.",
            "warning",
        )

    errors = []
    for url in urls:
        try:
            _read_health(url, timeout_seconds)
            detail = f"Tracking server healthy at {url}."
            if MLFLOW_PUBLIC_URL and MLFLOW_PUBLIC_URL != url:
                detail = f"{detail} Browser URL: {MLFLOW_PUBLIC_URL}."
            return _status("Online", detail, "connected", url=url)
        except error.HTTPError as exc:
            errors.append(f"{url} returned HTTP {exc.code}")
        except error.URLError as exc:
            errors.append(f"{url} unreachable: {exc.reason}")
        except Exception as exc:
            errors.append(f"{url} error: {exc}")

    return _status("Offline", " | ".join(errors), "offline", url=urls[0])
