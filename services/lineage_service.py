from pathlib import Path


def get_empty_lineage_graph():
    return {
        "message": "Lineage events are not recorded yet.",
        "events": [],
        "nodes": [
            {"stage": "Bronze", "status": "Registered"},
            {"stage": "Metadata", "status": "Profiled"},
            {"stage": "Silver", "status": "Pending"},
            {"stage": "Gold", "status": "Pending"},
            {"stage": "Training", "status": "Pending"},
            {"stage": "Inference", "status": "Pending"},
            {"stage": "Export", "status": "Pending"},
        ],
    }


def load_lineage_events(dataset_id=None):
    try:
        request_root = Path("data/airflow_preprocessing_requests")
        events = []
        if request_root.exists():
            for path in sorted(request_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:20]:
                if path.name.endswith("_dataset_config.json"):
                    continue
                if dataset_id and not path.name.startswith(f"{dataset_id}_"):
                    continue
                events.append(
                    {
                        "event": "preprocessing_request",
                        "dataset_id": path.name.split("_prep_")[0],
                        "artifact": str(path),
                        "status": "recorded",
                    }
                )
        result = get_empty_lineage_graph()
        result["events"] = events
        result["message"] = "" if events else result["message"]
        return result
    except Exception as exc:
        result = get_empty_lineage_graph()
        result["message"] = f"Lineage unavailable: {exc}"
        return result

