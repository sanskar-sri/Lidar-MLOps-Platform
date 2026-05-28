from pathlib import Path


PLANNED_MODELS = [
    {"model": "PointNet++ SSG", "status": "available", "role": "hierarchical baseline"},
    {"model": "PointNet++ MSG", "status": "available", "role": "multi-scale baseline"},
    {"model": "RandLA-Net", "status": "available", "role": "large-scale efficient baseline"},
    {"model": "PointNeXt-XL", "status": "planned", "role": "engineering-safe advanced model"},
    {"model": "PTv3", "status": "planned", "role": "SOTA transformer extension"},
]


def list_training_models():
    try:
        return list(PLANNED_MODELS)
    except Exception:
        return []


def get_empty_training_summary():
    return {
        "message": "No training runs recorded yet.",
        "models": list_training_models(),
        "runs": [],
        "gold_datasets": 0,
        "best_miou": "n/a",
    }


def load_latest_training_runs():
    try:
        root = Path("data/airflow_training_requests")
        rows = []
        if root.exists():
            for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:12]:
                rows.append(
                    {
                        "run_id": path.stem,
                        "artifact": str(path),
                        "status": "recorded",
                    }
                )
        return rows
    except Exception:
        return []

