import json
import os
from urllib import error, request

from dotenv import load_dotenv


load_dotenv()


DEFAULT_TARGET_ID = "any_gpu_worker"
DEFAULT_TARGET_NAME = "Any online compute node"
DEFAULT_TARGET_QUEUE = os.getenv("DEFAULT_AIRFLOW_QUEUE", "system1").strip() or "system1"
COMPUTE_HEALTH_POLL_MS = int(os.getenv("COMPUTE_HEALTH_POLL_MS", "2000"))


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

    return [_node_from_env("SYSTEM_1", "system1", "System 1", "system1")]


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


def _short_detail(value, limit=180):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _as_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_percent(value):
    number = _as_number(value)
    if number is None:
        return "n/a"
    return f"{number:.0f}%" if number.is_integer() else f"{number:.1f}%"


def _fmt_gb(value_mb):
    number = _as_number(value_mb)
    if number is None:
        return "n/a"
    return f"{number / 1024:.1f} GB"


def _percent_from_values(used, total):
    used_number = _as_number(used)
    total_number = _as_number(total)
    if used_number is None or not total_number:
        return None
    return (used_number / total_number) * 100


def _metric(label, value, detail):
    return {
        "label": label,
        "value": str(value),
        "detail": str(detail or ""),
    }


def _build_metrics(payload):
    resources = payload.get("resources") or {}
    gpu = payload.get("gpu") or {}
    metrics = []

    cpu_percent = resources.get("cpu_percent")
    if cpu_percent is not None:
        metrics.append(_metric("CPU", _fmt_percent(cpu_percent), "processor load"))

    memory_used = resources.get("memory_used_mb")
    memory_total = resources.get("memory_total_mb")
    if memory_used is not None or memory_total is not None:
        memory_percent = resources.get("memory_percent")
        if memory_percent is None:
            memory_percent = _percent_from_values(memory_used, memory_total)
        metrics.append(
            _metric(
                "RAM",
                f"{_fmt_gb(memory_used)} / {_fmt_gb(memory_total)}",
                _fmt_percent(memory_percent),
            )
        )

    if gpu.get("available"):
        gpu_percent = (
            gpu.get("gpu_3d_percent")
            if gpu.get("gpu_3d_percent") is not None
            else gpu.get("utilization_percent")
        )
        gpu_detail = gpu.get("name") or "NVIDIA GPU"
        if gpu.get("gpu_3d_percent") is not None:
            gpu_detail = f"{gpu_detail} | Task Manager 3D"
        elif gpu.get("nvidia_utilization_percent") is not None:
            gpu_detail = f"{gpu_detail} | nvidia-smi"
        metrics.append(
            _metric(
                "GPU",
                _fmt_percent(gpu_percent),
                gpu_detail,
            )
        )
        metrics.append(
            _metric(
                "VRAM",
                f"{_fmt_gb(gpu.get('memory_used_mb'))} / {_fmt_gb(gpu.get('memory_total_mb'))}",
                _fmt_percent(
                    gpu.get("memory_percent")
                    if gpu.get("memory_percent") is not None
                    else _percent_from_values(gpu.get("memory_used_mb"), gpu.get("memory_total_mb"))
                ),
            )
        )

    return metrics


def _status(node, state, detail, tone, payload=None):
    payload = payload or {}
    return {
        "id": node["id"],
        "name": node["name"],
        "health_url": node.get("health_url", ""),
        "airflow_queue": node.get("airflow_queue", ""),
        "roles": node.get("roles", []),
        "state": state,
        "detail": _short_detail(detail),
        "tone": tone,
        "metrics": _build_metrics(payload),
        "payload": payload,
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
        resources = payload.get("resources") or {}
        gpu_name = gpu.get("name") or payload.get("gpu_name")
        docker = payload.get("docker") or "unknown"
        detail_parts = [
            f"Docker {docker}",
            f"queue {node.get('airflow_queue') or node['id']}",
        ]
        if resources.get("cpu_percent") is not None:
            detail_parts.append(f"CPU {_fmt_percent(resources.get('cpu_percent'))}")
        if resources.get("memory_percent") is not None:
            detail_parts.append(
                f"RAM {_fmt_gb(resources.get('memory_used_mb'))}/{_fmt_gb(resources.get('memory_total_mb'))} ({_fmt_percent(resources.get('memory_percent'))})"
            )
        if gpu_name:
            gpu_detail = f"GPU {gpu_name}"
            if gpu.get("gpu_3d_percent") is not None:
                gpu_detail = f"{gpu_detail} {_fmt_percent(gpu.get('gpu_3d_percent'))} 3D"
            elif gpu.get("utilization_percent") is not None:
                gpu_detail = f"{gpu_detail} {_fmt_percent(gpu.get('utilization_percent'))}"
            if gpu.get("memory_used_mb") is not None or gpu.get("memory_total_mb") is not None:
                gpu_detail = (
                    f"{gpu_detail}; VRAM {_fmt_gb(gpu.get('memory_used_mb'))}/"
                    f"{_fmt_gb(gpu.get('memory_total_mb'))}"
                )
            detail_parts.append(gpu_detail)
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
