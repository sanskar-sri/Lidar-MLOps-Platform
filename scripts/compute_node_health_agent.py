import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_REFRESH_INTERVAL = float(os.getenv("METRICS_REFRESH_INTERVAL", "3"))


def _run(command, timeout=3):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)

    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, digits=1):
    number = _to_float(value)
    return round(number, digits) if number is not None else None


def _gpu_status():
    task_manager = _task_manager_gpu_status()
    if not shutil.which("nvidia-smi"):
        if task_manager:
            return {
                "available": True,
                **task_manager,
                "detail": "nvidia-smi not found; using Windows GPU engine counters",
            }
        return {"available": False, "detail": "nvidia-smi not found"}

    ok, output = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if not ok or not output:
        return {"available": False, "detail": output or "nvidia-smi returned no data"}

    first = output.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    used_mb = _round(parts[1], 0) if len(parts) > 1 else None
    total_mb = _round(parts[2], 0) if len(parts) > 2 else None
    return {
        "available": True,
        "name": parts[0] if len(parts) > 0 else "NVIDIA GPU",
        "memory_used_mb": used_mb,
        "memory_total_mb": total_mb,
        "memory_percent": _round((used_mb / total_mb) * 100, 1) if used_mb is not None and total_mb else None,
        "utilization_percent": (
            task_manager.get("gpu_3d_percent")
            if task_manager and task_manager.get("gpu_3d_percent") is not None
            else _round(parts[3], 1) if len(parts) > 3 else None
        ),
        "nvidia_utilization_percent": _round(parts[3], 1) if len(parts) > 3 else None,
        **(task_manager or {}),
    }


def _task_manager_gpu_status():
    if platform.system().lower() != "windows":
        return {}

    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        return {
            "task_manager_counter_status": "unavailable",
            "task_manager_counter_detail": "powershell not found",
        }

    command = [
        powershell,
        "-NoProfile",
        "-Command",
        (
            "$ErrorActionPreference = 'Stop'; "
            "$samples = (Get-Counter '\\GPU Engine(*)\\Utilization Percentage').CounterSamples; "
            "$all = ($samples | Measure-Object -Property CookedValue -Sum).Sum; "
            "$threeD = ($samples | Where-Object { $_.InstanceName -like '*engtype_3D*' } | "
            "Measure-Object -Property CookedValue -Sum).Sum; "
            "$copy = ($samples | Where-Object { $_.InstanceName -like '*engtype_Copy*' } | "
            "Measure-Object -Property CookedValue -Sum).Sum; "
            "$videoEncode = ($samples | Where-Object { $_.InstanceName -like '*engtype_VideoEncode*' } | "
            "Measure-Object -Property CookedValue -Sum).Sum; "
            "$videoDecode = ($samples | Where-Object { $_.InstanceName -like '*engtype_VideoDecode*' } | "
            "Measure-Object -Property CookedValue -Sum).Sum; "
            "[pscustomobject]@{"
            "gpu_3d_percent=[math]::Round([double]$threeD,1);"
            "gpu_engine_percent=[math]::Round([double]$all,1);"
            "gpu_copy_percent=[math]::Round([double]$copy,1);"
            "gpu_video_encode_percent=[math]::Round([double]$videoEncode,1);"
            "gpu_video_decode_percent=[math]::Round([double]$videoDecode,1)"
            "} | ConvertTo-Json -Compress"
        ),
    ]
    ok, output = _run(command, timeout=10)
    if not ok or not output:
        return {
            "gpu_3d_percent": None,
            "gpu_engine_percent": None,
            "task_manager_counter_status": "error",
            "task_manager_counter_detail": output or "Get-Counter returned no output",
        }

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {
            "gpu_3d_percent": None,
            "gpu_engine_percent": None,
            "task_manager_counter_status": "error",
            "task_manager_counter_detail": output,
        }

    return {
        "task_manager_counter_status": "ok",
        "gpu_3d_percent": _round(payload.get("gpu_3d_percent"), 1),
        "gpu_engine_percent": _round(payload.get("gpu_engine_percent"), 1),
        "gpu_copy_percent": _round(payload.get("gpu_copy_percent"), 1),
        "gpu_video_encode_percent": _round(payload.get("gpu_video_encode_percent"), 1),
        "gpu_video_decode_percent": _round(payload.get("gpu_video_decode_percent"), 1),
    }


def _resource_status_with_psutil():
    try:
        import psutil
    except Exception:
        return None

    memory = psutil.virtual_memory()
    return {
        "cpu_percent": _round(psutil.cpu_percent(interval=0.1), 1),
        "memory_used_mb": _round(memory.used / (1024 * 1024), 0),
        "memory_total_mb": _round(memory.total / (1024 * 1024), 0),
        "memory_available_mb": _round(memory.available / (1024 * 1024), 0),
        "memory_percent": _round(memory.percent, 1),
    }


def _resource_status_with_powershell():
    if platform.system().lower() != "windows":
        return None

    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        return None

    command = [
        powershell,
        "-NoProfile",
        "-Command",
        (
            "$cpu = (Get-CimInstance Win32_Processor | "
            "Measure-Object -Property LoadPercentage -Average).Average; "
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "$total = [double]$os.TotalVisibleMemorySize; "
            "$free = [double]$os.FreePhysicalMemory; "
            "$used = $total - $free; "
            "[pscustomobject]@{"
            "cpu_percent=[math]::Round($cpu,1);"
            "memory_used_mb=[math]::Round($used/1024,0);"
            "memory_total_mb=[math]::Round($total/1024,0);"
            "memory_available_mb=[math]::Round($free/1024,0);"
            "memory_percent=[math]::Round(($used/$total)*100,1)"
            "} | ConvertTo-Json -Compress"
        ),
    ]
    ok, output = _run(command, timeout=5)
    if not ok or not output:
        return None

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None

    return {
        "cpu_percent": _round(payload.get("cpu_percent"), 1),
        "memory_used_mb": _round(payload.get("memory_used_mb"), 0),
        "memory_total_mb": _round(payload.get("memory_total_mb"), 0),
        "memory_available_mb": _round(payload.get("memory_available_mb"), 0),
        "memory_percent": _round(payload.get("memory_percent"), 1),
    }


def _resource_status():
    payload = _resource_status_with_psutil() or _resource_status_with_powershell()
    if payload:
        return payload
    return {
        "cpu_percent": None,
        "memory_used_mb": None,
        "memory_total_mb": None,
        "memory_percent": None,
        "detail": "CPU/RAM metrics unavailable; install psutil or run on Windows with PowerShell.",
    }


def _docker_status():
    if not shutil.which("docker"):
        return "missing"

    ok, _ = _run(["docker", "info"], timeout=4)
    return "running" if ok else "unreachable"


def build_payload():
    return {
        "status": "ok",
        "node_id": os.getenv("COMPUTE_NODE_ID", platform.node()),
        "node_name": os.getenv("COMPUTE_NODE_NAME", platform.node()),
        "roles": [
            item.strip()
            for item in os.getenv("NODE_ROLES", "preprocessing,training").split(",")
            if item.strip()
        ],
        "airflow_queue": os.getenv("AIRFLOW_QUEUE", os.getenv("COMPUTE_NODE_ID", "gpu_worker")),
        "docker": _docker_status(),
        "resources": _resource_status(),
        "gpu": _gpu_status(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Background metrics cache — refreshes every METRICS_REFRESH_INTERVAL seconds
# so HTTP requests return instantly instead of blocking on nvidia-smi / psutil.
# ---------------------------------------------------------------------------

class _MetricsCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._payload = None

    def get(self):
        with self._lock:
            return self._payload

    def set(self, payload):
        with self._lock:
            self._payload = payload


_cache = _MetricsCache()


def _refresh_loop():
    while True:
        try:
            _cache.set(build_payload())
        except Exception:
            pass
        time.sleep(_REFRESH_INTERVAL)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return

        payload = _cache.get()
        if payload is None:
            # Should not happen after startup pre-warm, but guard anyway.
            payload = build_payload()
            _cache.set(payload)

        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("HEALTH_AGENT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("HEALTH_AGENT_PORT", "8899")))
    args = parser.parse_args()

    print("Collecting initial metrics...", flush=True)
    _cache.set(build_payload())

    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()
    print(f"Metrics refreshing every {_REFRESH_INTERVAL:.0f}s in background.", flush=True)

    server = ThreadingHTTPServer((args.host, args.port), HealthHandler)
    print(f"Compute health agent listening on http://{args.host}:{args.port}/health", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
