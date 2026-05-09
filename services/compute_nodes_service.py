import json
import os
from urllib import error, request

from dotenv import load_dotenv


load_dotenv()


DEFAULT_TARGET_ID = "any_gpu_worker"
DEFAULT_TARGET_NAME = "Any available GPU worker"
DEFAULT_TARGET_QUEUE = "gpu_worker"


def _split_roles(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _node_from_env(prefix, default_id, default_name, default_queue):
    health_url = os.getenv(f"{prefix}_HEALTH_URL", "").strip()
    node_id = os.getenv(f"{prefix}_ID", default_id).strip() or default_id
    name = os.getenv(f"{prefix}_NAME", default_name).strip() or default_name
    queue = os.getenv(f"{prefix}_AIRFLOW_QUEUE", default_queue).strip() or default_queue
    roles = _split_roles(os.getenv(f"{prefix}_ROLES", "preprocessing,training"))

    return {
        "id": node_id,
        "name": name,
        "health_url": health_url,
        "airflow_queue": queue,
        "roles": roles,
    }


def list_compute_nodes():
    raw_nodes = os.getenv("COMPUTE_NODES_JSON", "").strip()
    if raw_nodes:
        try:
            parsed = json.loads(raw_nodes)
            if isinstance(parsed, list):
                return [_normalize_node(item) for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

    return [
        _node_from_env("SYSTEM_1", "system1", "System 1", "system1"),
        _node_from_env("SYSTEM_2", "system2_gpu", "System 2 GPU", "system2_gpu"),
    ]


def _normalize_node(item):
    node_id = str(item.get("id") or item.get("name") or "").strip()
    if not node_id:
        node_id = "compute_node"

    roles = item.get("roles") or ["preprocessing", "training"]
    if isinstance(roles, str):
        roles = _split_roles(roles)

    return {
        "id": node_id,
        "name": str(item.get("name") or node_id).strip(),
        "health_url": str(item.get("health_url") or item.get("url") or "").strip(),
        "airflow_queue": str(item.get("airflow_queue") or item.get("queue") or node_id).strip(),
        "roles": roles,
    }


def get_compute_node(node_id):
    for node in list_compute_nodes():
        if node["id"] == node_id:
            return node
    return None


def build_compute_target_options():
    options = [
        {
            "label": DEFAULT_TARGET_NAME,
            "value": DEFAULT_TARGET_ID,
        }
    ]

    for node in list_compute_nodes():
        queue = node.get("airflow_queue") or node["id"]
        roles = ", ".join(node.get("roles") or [])
        label = f"{node['name']} - queue: {queue}"
        if roles:
            label = f"{label} - {roles}"
        options.append({"label": label, "value": node["id"]})

    return options


def resolve_airflow_queue(execution_target):
    node = get_compute_node(execution_target)
    if node:
        return node.get("airflow_queue") or node["id"]
    return DEFAULT_TARGET_QUEUE


def _short_detail(value, limit=120):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _status(node, state, detail, tone, payload=None):
    return {
        "id": node["id"],
        "name": node["name"],
        "health_url": node.get("health_url", ""),
        "airflow_queue": node.get("airflow_queue", ""),
        "roles": node.get("roles", []),
        "state": state,
        "detail": _short_detail(detail),
        "tone": tone,
        "payload": payload or {},
    }


def check_compute_node(node, timeout_seconds=3):
    if not node.get("health_url"):
        return _status(
            node,
            "Not Configured",
            "Set this node's health URL in .env before routing work here.",
            "warning",
        )

    try:
        req = request.Request(node["health_url"], headers={"Accept": "application/json"})
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            payload = json.loads(body) if body else {}
    except error.HTTPError as exc:
        return _status(node, "Offline", f"Health endpoint returned HTTP {exc.code}.", "offline")
    except error.URLError as exc:
        return _status(node, "Offline", f"Could not reach health endpoint: {exc.reason}", "offline")
    except json.JSONDecodeError:
        return _status(node, "Offline", "Health endpoint did not return valid JSON.", "offline")
    except Exception as exc:
        return _status(node, "Offline", str(exc), "offline")

    remote_status = str(payload.get("status") or "").lower()
    if remote_status in {"ok", "healthy", "online", "ready"}:
        gpu = payload.get("gpu") or {}
        gpu_name = gpu.get("name") or payload.get("gpu_name")
        docker = payload.get("docker") or "unknown"
        detail_parts = [
            f"Docker {docker}",
            f"queue {node.get('airflow_queue') or node['id']}",
        ]
        if gpu_name:
            detail_parts.append(f"GPU {gpu_name}")
        return _status(node, "Online", " | ".join(detail_parts), "connected", payload)

    return _status(
        node,
        "Attention",
        payload.get("detail") or payload.get("message") or "Health endpoint responded without ready status.",
        "warning",
        payload,
    )


def check_compute_nodes():
    return [check_compute_node(node) for node in list_compute_nodes()]
