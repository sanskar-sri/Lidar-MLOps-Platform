import base64
import ast
import json
import os
from urllib import error, parse, request

from dotenv import load_dotenv

from services.compute_nodes_service import check_compute_nodes
from services.mlflow_service import check_mlflow_service


load_dotenv()

AIRFLOW_API_BASE_URL = os.getenv("AIRFLOW_API_BASE_URL", "").strip()
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "").strip()
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "").strip()
AIRFLOW_HEALTH_B2_DAG_ID = os.getenv("AIRFLOW_HEALTH_B2_DAG_ID", "dag_health_b2").strip()
AIRFLOW_HEALTH_B2_TASK_ID = os.getenv("AIRFLOW_HEALTH_B2_TASK_ID", "check_b2_health").strip()
AIRFLOW_HEALTH_REMOTE_DAG_ID = os.getenv("AIRFLOW_HEALTH_REMOTE_DAG_ID", "dag_health_remote").strip()
AIRFLOW_HEALTH_REMOTE_TASK_ID = os.getenv("AIRFLOW_HEALTH_REMOTE_TASK_ID", "check_remote_health").strip()


def _short_detail(value, limit=92):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _status_result(service, status, detail, tone):
    return {
        "service": service,
        "status": status,
        "detail": _short_detail(detail),
        "tone": tone,
    }


def _headers():
    headers = {"Accept": "application/json"}
    if AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
        token = base64.b64encode(
            f"{AIRFLOW_USERNAME}:{AIRFLOW_PASSWORD}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def _get_json(path, timeout=6, query=None):
    if not AIRFLOW_API_BASE_URL:
        raise ValueError("AIRFLOW_API_BASE_URL is not configured.")

    url = f"{AIRFLOW_API_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"

    req = request.Request(url, headers=_headers(), method="GET")
    with request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}


def _airflow_api_card():
    if not AIRFLOW_API_BASE_URL:
        return _status_result(
            "Airflow",
            "Not Configured",
            "AIRFLOW_API_BASE_URL is missing; set it to the Airflow Docker API URL.",
            "warning",
        )

    try:
        payload = _get_json("/health", timeout=5)
    except error.HTTPError as exc:
        if exc.code in {401, 403}:
            return _status_result("Airflow", "Auth Failed", "Airflow API credentials were rejected.", "offline")
        return _status_result("Airflow", "Offline", f"Airflow returned HTTP {exc.code}.", "offline")
    except error.URLError as exc:
        return _status_result("Airflow", "Offline", f"Could not reach Airflow API: {exc.reason}", "offline")
    except Exception as exc:
        return _status_result("Airflow", "Offline", str(exc), "offline")

    metadb = (payload.get("metadatabase") or {}).get("status")
    scheduler = (payload.get("scheduler") or {}).get("status")
    if metadb == "healthy" and scheduler == "healthy":
        return _status_result(
            "Airflow",
            "Connected",
            f"API healthy at {AIRFLOW_API_BASE_URL}; scheduler healthy.",
            "connected",
        )

    return _status_result(
        "Airflow",
        "Attention",
        f"API reachable; metadatabase={metadb or 'unknown'}, scheduler={scheduler or 'unknown'}.",
        "warning",
    )


def _latest_dag_run(dag_id):
    dag_runs_path = f"/api/v1/dags/{parse.quote(dag_id, safe='')}/dagRuns"
    order_candidates = ["-logical_date", "-execution_date", "-start_date"]
    last_error = None

    for order_by in order_candidates:
        try:
            payload = _get_json(
                dag_runs_path,
                timeout=6,
                query={"limit": 1, "order_by": order_by},
            )
            dag_runs = payload.get("dag_runs") or []
            return dag_runs[0] if dag_runs else None
        except error.HTTPError as exc:
            last_error = exc
            if exc.code not in {400, 422}:
                raise

    if last_error:
        raise last_error
    return None


def _latest_successful_dag_run(dag_id):
    dag_runs_path = f"/api/v1/dags/{parse.quote(dag_id, safe='')}/dagRuns"
    order_candidates = ["-logical_date", "-execution_date", "-start_date"]
    last_error = None

    for order_by in order_candidates:
        try:
            payload = _get_json(
                dag_runs_path,
                timeout=6,
                query={"limit": 1, "order_by": order_by, "state": "success"},
            )
            dag_runs = payload.get("dag_runs") or []
            return dag_runs[0] if dag_runs else None
        except error.HTTPError as exc:
            last_error = exc
            if exc.code not in {400, 422}:
                raise

    if last_error:
        raise last_error
    return None


def _xcom_return_value(dag_id, dag_run_id, task_id):
    payload = _get_json(
        "/api/v1/dags/{}/dagRuns/{}/taskInstances/{}/xcomEntries/return_value".format(
            parse.quote(dag_id, safe=""),
            parse.quote(dag_run_id, safe=""),
            parse.quote(task_id, safe=""),
        ),
        timeout=6,
    )
    value = payload.get("value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return {"status": "unknown", "detail": value}
            return parsed if isinstance(parsed, dict) else {"status": "unknown", "detail": value}
    return value if isinstance(value, dict) else {}


def _latest_health_payload(dag_id, task_id):
    run = _latest_dag_run(dag_id)
    if not run:
        return {
            "run_state": "missing",
            "detail": f"No Airflow DAG run found for {dag_id}.",
            "payload": {},
        }

    run_id = run.get("dag_run_id") or run.get("run_id")
    state = run.get("state") or "unknown"
    result = {
        "run_state": state,
        "dag_run_id": run_id,
        "logical_date": run.get("logical_date") or run.get("execution_date"),
        "payload": {},
    }

    if state != "success":
        result["detail"] = f"Latest {dag_id} run is {state}."
        return result

    try:
        result["payload"] = _xcom_return_value(dag_id, run_id, task_id)
        return result
    except error.HTTPError as exc:
        result["detail"] = f"Latest {dag_id} run succeeded, but XCom read returned HTTP {exc.code}."
        return result
    except Exception as exc:
        result["detail"] = f"Latest {dag_id} run succeeded, but result read failed: {exc}"
        return result


def _latest_successful_health_payload(dag_id, task_id):
    run = _latest_successful_dag_run(dag_id)
    if not run:
        return {
            "run_state": "missing",
            "detail": f"No successful Airflow DAG run found for {dag_id}.",
            "payload": {},
        }

    run_id = run.get("dag_run_id") or run.get("run_id")
    result = {
        "run_state": "success",
        "dag_run_id": run_id,
        "logical_date": run.get("logical_date") or run.get("execution_date"),
        "payload": {},
    }
    try:
        result["payload"] = _xcom_return_value(dag_id, run_id, task_id)
        return result
    except error.HTTPError as exc:
        result["detail"] = f"Latest successful {dag_id} run XCom read returned HTTP {exc.code}."
        return result
    except Exception as exc:
        result["detail"] = f"Latest successful {dag_id} result read failed: {exc}"
        return result


def _tone_for_status(status, default="warning"):
    normalized = str(status or "").lower()
    if normalized in {"ok", "healthy", "connected", "online", "success"}:
        return "connected"
    if normalized in {"not_configured", "missing", "unavailable", "warning", "degraded"}:
        return "warning"
    if normalized in {"offline", "failed", "error", "timeout"}:
        return "offline"
    return default


def _format_checked_at(payload):
    checked_at = payload.get("checked_at") or payload.get("timestamp")
    if not checked_at:
        return ""
    return f" | checked {checked_at}"


def _b2_card():
    try:
        result = _latest_health_payload(AIRFLOW_HEALTH_B2_DAG_ID, AIRFLOW_HEALTH_B2_TASK_ID)
    except Exception as exc:
        return _status_result("B2 Storage", "Unknown", f"Could not read B2 health DAG: {exc}", "warning")

    if result.get("run_state") == "missing":
        return _status_result("B2 Storage", "No DAG Run", result.get("detail"), "warning")
    if result.get("run_state") != "success":
        return _status_result("B2 Storage", "Checking", result.get("detail"), "checking")

    payload = result.get("payload") or {}
    status = payload.get("status") or "unknown"
    bucket = payload.get("bucket") or "configured bucket"
    prefix = payload.get("prefix") or ""
    count = payload.get("file_count")
    last_modified = payload.get("last_modified") or "no files yet"

    if status in {"ok", "healthy"}:
        detail = f"{bucket}/{prefix} reachable; files={count}; last={last_modified}{_format_checked_at(payload)}"
        return _status_result("B2 Storage", "Connected", detail, "connected")

    detail = payload.get("detail") or payload.get("error") or f"Latest B2 health status: {status}."
    return _status_result("B2 Storage", status.replace("_", " ").title(), detail, _tone_for_status(status))


def get_b2_file_count():
    payloads = []
    try:
        latest = _latest_health_payload(AIRFLOW_HEALTH_B2_DAG_ID, AIRFLOW_HEALTH_B2_TASK_ID)
        if latest.get("run_state") == "success":
            payloads.append(latest.get("payload") or {})
    except Exception as exc:
        print(f"[B2 FILE COUNT LATEST WARNING] {exc}")

    try:
        successful = _latest_successful_health_payload(AIRFLOW_HEALTH_B2_DAG_ID, AIRFLOW_HEALTH_B2_TASK_ID)
        if successful.get("run_state") == "success":
            payloads.append(successful.get("payload") or {})
    except Exception as exc:
        print(f"[B2 FILE COUNT SUCCESS WARNING] {exc}")

    for payload in payloads:
        count = payload.get("file_count")
        if count is None:
            continue
        try:
            return int(count)
        except (TypeError, ValueError):
            continue
    return None


def _remote_payload():
    try:
        return _latest_health_payload(AIRFLOW_HEALTH_REMOTE_DAG_ID, AIRFLOW_HEALTH_REMOTE_TASK_ID)
    except Exception as exc:
        return {
            "run_state": "error",
            "detail": f"Could not read remote health DAG: {exc}",
            "payload": {},
        }


def _mlflow_card(remote):
    direct = check_mlflow_service()
    if direct.get("tone") == "connected":
        return _status_result("MLflow", direct.get("status"), direct.get("detail"), direct.get("tone"))

    if remote.get("run_state") == "missing":
        return _status_result("MLflow", "No DAG Run", remote.get("detail"), "warning")
    if remote.get("run_state") != "success":
        return _status_result("MLflow", "Checking", remote.get("detail"), "checking")

    payload = remote.get("payload") or {}
    mlflow = payload.get("mlflow") or {}
    status = mlflow.get("status") or "unknown"
    detail = mlflow.get("detail") or mlflow.get("url") or f"Latest MLflow status: {status}."
    return _status_result("MLflow", status.replace("_", " ").title(), detail, _tone_for_status(status))


def _dvc_card(remote):
    if remote.get("run_state") == "missing":
        return _status_result("DVC", "No DAG Run", remote.get("detail"), "warning")
    if remote.get("run_state") != "success":
        return _status_result("DVC", "Checking", remote.get("detail"), "checking")

    payload = remote.get("payload") or {}
    dvc = payload.get("dvc") or {}
    status = dvc.get("status") or "unknown"
    detail = dvc.get("version") or dvc.get("detail") or f"Latest DVC status: {status}."
    return _status_result("DVC", status.replace("_", " ").title(), detail, _tone_for_status(status))


def _system_card(remote):
    if remote.get("run_state") == "missing":
        return _status_result("Airflow Runtime", "No DAG Run", remote.get("detail"), "warning")
    if remote.get("run_state") != "success":
        return _status_result("Airflow Runtime", "Checking", remote.get("detail"), "checking")

    payload = remote.get("payload") or {}
    system = payload.get("system") or {}
    gpu = payload.get("gpu") or {}
    status = system.get("status") or payload.get("status") or "unknown"
    detail_parts = []
    if system:
        detail_parts.append(
            f"{system.get('system', 'system')} Python {system.get('python_version', 'unknown')}"
        )
    if gpu.get("available"):
        detail_parts.append(f"GPU {gpu.get('name', 'available')}")
    else:
        detail_parts.append(gpu.get("detail") or "GPU unavailable")

    detail = " | ".join(part for part in detail_parts if part) or f"Latest system status: {status}."
    return _status_result("Airflow Runtime", status.replace("_", " ").title(), detail, _tone_for_status(status))


def _compute_node_cards():
    cards = []
    for node in check_compute_nodes():
        if not node.get("health_url"):
            continue
        cards.append(
            _status_result(
                node.get("name") or node.get("id") or "Windows Workstation",
                node.get("state") or "Unknown",
                node.get("detail") or "Compute node health endpoint returned no detail.",
                node.get("tone") or "warning",
            )
        )
    return cards


def get_backend_status_cards():
    remote = _remote_payload()
    return [
        _b2_card(),
        _airflow_api_card(),
        _mlflow_card(remote),
        _dvc_card(remote),
        _system_card(remote),
        *_compute_node_cards(),
    ]


def get_pipeline_task_statuses(dag_run_id: str) -> list:
    """Return task instance list for an mls_preprocessing_v9 DAG run."""
    from services.preprocessing_service import AIRFLOW_DAG_ID
    try:
        payload = _get_json(
            f"/api/v1/dags/{parse.quote(AIRFLOW_DAG_ID, safe='')}"
            f"/dagRuns/{parse.quote(dag_run_id, safe='')}/taskInstances",
            timeout=10,
        )
        return payload.get("task_instances") or []
    except Exception:
        return []


def get_dag_run_state(dag_run_id: str) -> str:
    """Return the overall state (queued/running/success/failed) of a DAG run."""
    from services.preprocessing_service import AIRFLOW_DAG_ID
    try:
        payload = _get_json(
            f"/api/v1/dags/{parse.quote(AIRFLOW_DAG_ID, safe='')}"
            f"/dagRuns/{parse.quote(dag_run_id, safe='')}",
            timeout=10,
        )
        return payload.get("state") or "unknown"
    except Exception:
        return "unknown"

