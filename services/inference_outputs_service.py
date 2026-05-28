from pathlib import Path


def get_empty_inference_summary():
    return {
        "message": "No inference outputs recorded yet.",
        "runs": [],
        "inference_runs": 0,
        "prediction_files": 0,
        "building_points": "n/a",
        "latest_output": "Pending",
    }


def list_inference_outputs(dataset_id=None):
    try:
        roots = [Path("data/inference_outputs"), Path("data/local_staging/inference_outputs")]
        rows = []
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if dataset_id and dataset_id not in str(path):
                    continue
                rows.append(
                    {
                        "dataset_id": dataset_id or "n/a",
                        "artifact": path.name,
                        "path": str(path),
                        "status": "available",
                    }
                )
        result = get_empty_inference_summary()
        result.update(
            {
                "runs": rows,
                "inference_runs": len({Path(row["path"]).parent for row in rows}),
                "prediction_files": len(rows),
                "latest_output": rows[0]["artifact"] if rows else "Pending",
                "message": "" if rows else result["message"],
            }
        )
        return result
    except Exception as exc:
        result = get_empty_inference_summary()
        result["message"] = f"Inference outputs unavailable: {exc}"
        return result

