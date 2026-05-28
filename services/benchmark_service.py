import json
from pathlib import Path


BENCHMARK_PATH = Path("results/benchmark_summary.json")


def get_empty_benchmark_summary():
    return {
        "message": "No benchmark runs recorded yet.",
        "rows": [],
        "best_model": "Pending",
        "best_building_iou": "n/a",
        "best_miou": "n/a",
        "fastest_inference": "n/a",
    }


def load_benchmark_summary():
    try:
        if not BENCHMARK_PATH.exists():
            return get_empty_benchmark_summary()
        payload = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
        rows = payload.get("rows") or payload.get("benchmarks") or []
        return {
            **get_empty_benchmark_summary(),
            **payload,
            "rows": rows,
            "message": payload.get("message") or ("" if rows else "No benchmark runs recorded yet."),
        }
    except Exception as exc:
        result = get_empty_benchmark_summary()
        result["message"] = f"Benchmark summary unavailable: {exc}"
        return result

