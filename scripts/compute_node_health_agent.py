import argparse
import json
import os
import platform
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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


def _gpu_status():
    if not shutil.which("nvidia-smi"):
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
    return {
        "available": True,
        "name": parts[0] if len(parts) > 0 else "NVIDIA GPU",
        "memory_used_mb": parts[1] if len(parts) > 1 else "",
        "memory_total_mb": parts[2] if len(parts) > 2 else "",
        "utilization_percent": parts[3] if len(parts) > 3 else "",
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
        "gpu": _gpu_status(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
    }


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps(build_payload(), indent=2).encode("utf-8")
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

    server = ThreadingHTTPServer((args.host, args.port), HealthHandler)
    print(f"Compute health agent listening on http://{args.host}:{args.port}/health", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
