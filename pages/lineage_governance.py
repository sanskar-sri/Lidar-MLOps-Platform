import json
import re
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dcc, html

from components.ops_page_shell import data_table, info_card, kv_grid, page_shell, section
from services.b2_paths import b2_prefix as _b2_prefix, bronze_manifest_prefix, bronze_tiles_prefix, bronze_label_maps_prefix
from services.dataset_selection import resolve_selected_dataset_id
from services.lineage_service import load_lineage_events
from services.metadata_service import load_dataset_metadata

try:
    from services.b2_service import list_b2_files_with_prefix
except Exception as exc:
    print(f"[LINEAGE_GOVERNANCE] B2 listing import unavailable: {exc}")
    list_b2_files_with_prefix = None


dash.register_page(
    __name__,
    path="/lineage-governance",
    name="Lineage & Governance",
    title="Lineage & Governance - LiDAR Platform",
)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REFRESH_INTERVAL_MS = 60_000
_NO_DATASET_MESSAGE = "No dataset selected. Please select a dataset from Data Explorer first."


def _empty_alert(message, color="info"):
    return dbc.Alert(message, color=color, className="mb-0")


def _warning_card(title, message):
    return dbc.Alert(
        [
            html.Strong(title),
            html.Br(),
            message,
        ],
        color="warning",
        className="mb-0",
    )


def _error_card(title, message):
    return dbc.Alert(
        [
            html.Strong(title),
            html.Br(),
            message,
        ],
        color="danger",
        className="mb-0",
    )


def _format_number(value):
    if value in (None, "", "n/a"):
        return "Not available"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)


def _quality_flag(metadata, key):
    flags = metadata.get("quality_flags") or {}
    if not isinstance(flags, dict) or key not in flags:
        return None
    return bool(flags.get(key))


def _yes_no_unknown(value):
    if value is None:
        return "Unknown"
    return "Yes" if value else "No"


def _status_pill_class(status):
    value = str(status or "").strip().lower()
    if value in {"available", "pass", "ready", "yes"}:
        return "ops-small-status ops-small-status-ok"
    if value in {"pending", "warning", "not available yet"}:
        return "ops-small-status ops-small-status-warn"
    if value in {"missing", "fail", "failed", "error", "no"}:
        return "ops-small-status ops-small-status-danger"
    return "ops-small-status ops-small-status-info"


def _card_class(status):
    value = str(status or "").strip().lower()
    if value in {"missing", "fail", "failed", "error", "pending", "warning"}:
        return "ops-mini-card ops-mini-card-warn"
    return "ops-mini-card"


def _status_card(title, status, detail, paths=None, kicker=None):
    path_nodes = []
    for item in paths or []:
        if item:
            path_nodes.append(html.Code(str(item)))

    return html.Div(
        [
            html.Span(status, className=_status_pill_class(status)),
            html.Div(kicker, className="ops-section-kicker") if kicker else None,
            html.H3(title),
            html.P(detail),
            html.Div(path_nodes, className="ops-kv-grid") if path_nodes else None,
        ],
        className=_card_class(status),
    )


def _project_path(*parts):
    return _PROJECT_ROOT.joinpath(*parts)


def _relative_event_path(path_text):
    path = Path(str(path_text or ""))
    if path.is_absolute():
        return path
    return _PROJECT_ROOT / path


def _file_exists(path):
    try:
        return Path(path).is_file()
    except Exception as exc:
        print(f"[LINEAGE_GOVERNANCE] File check failed for {path}: {exc}")
        return False


def _files_for_patterns(root, patterns):
    root = Path(root)
    if not root.exists():
        return []
    files = []
    try:
        if root.is_file():
            return [root]
        for pattern in patterns:
            files.extend(path for path in root.glob(pattern) if path.is_file())
    except Exception as exc:
        print(f"[LINEAGE_GOVERNANCE] Local listing failed for {root}: {exc}")
        return []
    return sorted(set(files))


def _safe_b2_list(prefix):
    if list_b2_files_with_prefix is None:
        return None, "B2 listing is not available."
    try:
        return list_b2_files_with_prefix(prefix), ""
    except Exception as exc:
        print(f"[LINEAGE_GOVERNANCE] B2 list failed prefix={prefix}: {exc}")
        return None, str(exc)


def _status_from_files(files, unknown_message="", pending_status="Pending"):
    if files is None:
        return "Unknown", unknown_message or "Listing is unavailable."
    if files:
        return "Available", f"{len(files)} file(s) or object(s) found."
    return pending_status, "Not available yet."


def _extract_prep_versions(text):
    return re.findall(r"prep_v[0-9A-Za-z]+", str(text or ""))


def _read_event_payload(event):
    artifact = event.get("artifact") if isinstance(event, dict) else ""
    if not artifact:
        return {}

    path = _relative_event_path(artifact)
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[LINEAGE_GOVERNANCE] Could not read event artifact {path}: {exc}")
        return {}


def _append_unique(items, value):
    value = str(value or "").strip()
    if value and value not in items:
        items.append(value)


def _build_context(dataset_id, metadata, events):
    prep_versions = []
    run_ids = []
    models = []

    storage = metadata.get("storage") or {}
    for value in storage.values():
        for prep_version in _extract_prep_versions(value):
            _append_unique(prep_versions, prep_version)

    for event in events or []:
        artifact = event.get("artifact") if isinstance(event, dict) else ""
        for prep_version in _extract_prep_versions(artifact):
            _append_unique(prep_versions, prep_version)

        payload = _read_event_payload(event)
        conf = payload.get("conf") if isinstance(payload, dict) else {}
        if not isinstance(conf, dict):
            conf = {}

        _append_unique(prep_versions, conf.get("prep_version"))
        _append_unique(run_ids, conf.get("run_id") or payload.get("dag_run_id"))
        _append_unique(models, conf.get("model_type") or conf.get("model"))

        script_args = conf.get("script_args") or {}
        if isinstance(script_args, dict):
            _append_unique(prep_versions, script_args.get("prep_version"))
            _append_unique(run_ids, script_args.get("run_id"))

    gold_root = _project_path("data", "local_staging", "gold_outputs", dataset_id)
    if gold_root.exists():
        try:
            for path in sorted(item for item in gold_root.iterdir() if item.is_dir()):
                _append_unique(prep_versions, path.name)
        except Exception as exc:
            print(f"[LINEAGE_GOVERNANCE] Gold version listing failed for {dataset_id}: {exc}")

    return {
        "prep_version": prep_versions[0] if prep_versions else "Unknown",
        "run_id": run_ids[0] if run_ids else "Unknown",
        "model": models[0] if models else "Unknown",
        "prep_versions": prep_versions,
        "run_ids": run_ids,
        "models": models,
    }


def _manifest_status(dataset_id):
    local_manifest_dir = _project_path("data", "local_staging", dataset_id, "manifests")
    upload_manifest = local_manifest_dir / "upload_manifest.json"
    checksum_manifest = local_manifest_dir / "checksum_manifest.json"

    upload_exists = _file_exists(upload_manifest)
    checksum_exists = _file_exists(checksum_manifest)
    if upload_exists and checksum_exists:
        return {
            "status": "Available",
            "detail": "Upload and checksum manifests are available locally.",
            "upload_manifest": "Available",
            "checksum_manifest": "Available",
        }

    new_manifest_prefix = f"{bronze_manifest_prefix(dataset_id)}/"
    files, error = _safe_b2_list(new_manifest_prefix)
    if files is None:
        # Legacy fallback for reads
        files, error = _safe_b2_list(f"bronze_raw_data/{dataset_id}/manifests/")
    if files is None:
        return {
            "status": "Unknown",
            "detail": f"Manifest listing unavailable: {error}",
            "upload_manifest": "Available" if upload_exists else "Unknown",
            "checksum_manifest": "Available" if checksum_exists else "Unknown",
        }

    names = {str(item.get("file_name") or "") for item in files}
    b2_upload = (
        f"{bronze_manifest_prefix(dataset_id)}/upload_manifest.json" in names
        or f"bronze_raw_data/{dataset_id}/manifests/upload_manifest.json" in names
    )
    b2_checksum = (
        f"{bronze_manifest_prefix(dataset_id)}/checksum_manifest.json" in names
        or f"bronze_raw_data/{dataset_id}/manifests/checksum_manifest.json" in names
    )

    upload_status = "Available" if upload_exists or b2_upload else "Missing"
    checksum_status = "Available" if checksum_exists or b2_checksum else "Missing"
    status = "Available" if upload_status == checksum_status == "Available" else "Missing"

    return {
        "status": status,
        "detail": f"{len(files)} manifest object(s) found.",
        "upload_manifest": upload_status,
        "checksum_manifest": checksum_status,
    }


def _analytics_status(dataset_id):
    local_files = _files_for_patterns(
        _project_path("data", "metadata_analytics", dataset_id),
        ["*.parquet"],
    )
    if local_files:
        return {
            "status": "Available",
            "detail": f"{len(local_files)} local analytics parquet file(s) found.",
        }

    files, error = _safe_b2_list(f"{_b2_prefix('metadata_analytics')}/{dataset_id}/")
    if files is None:
        # Legacy fallback for reads
        files, error = _safe_b2_list(f"metadata_analytics/{dataset_id}/")
    if files is None:
        return {"status": "Unknown", "detail": f"Analytics listing unavailable: {error}"}

    parquet_files = [
        item for item in files if str(item.get("file_name") or "").lower().endswith(".parquet")
    ]
    status, detail = _status_from_files(parquet_files, pending_status="Pending")
    return {"status": status, "detail": detail}


def _gold_status(dataset_id):
    gold_root = _project_path("data", "local_staging", "gold_outputs", dataset_id)
    local_versions = []
    if gold_root.exists():
        try:
            for version_dir in sorted(item for item in gold_root.iterdir() if item.is_dir()):
                files = _files_for_patterns(version_dir, ["**/*"])
                if files:
                    local_versions.append(version_dir.name)
        except Exception as exc:
            print(f"[LINEAGE_GOVERNANCE] Gold status failed for {dataset_id}: {exc}")

    if local_versions:
        return {
            "status": "Available",
            "detail": f"Gold model-ready output found for {', '.join(local_versions)}.",
        }

    files, error = _safe_b2_list(f"{_b2_prefix('gold_model_ready_data')}/{dataset_id}/")
    if files is None:
        # Legacy fallback for reads
        files, error = _safe_b2_list(f"gold_model_ready_data/{dataset_id}/")
    if files is None:
        return {"status": "Unknown", "detail": f"Gold listing unavailable: {error}"}

    status, detail = _status_from_files(files, pending_status="Pending")
    return {"status": status, "detail": detail}


def _prefix_output_status(dataset_id, layer):
    local_roots = [
        _project_path("data", layer, dataset_id),
        _project_path("data", "local_staging", layer, dataset_id),
    ]
    local_files = []
    for root in local_roots:
        local_files.extend(_files_for_patterns(root, ["**/*"]))

    if local_files:
        return {
            "status": "Available",
            "detail": f"{len(local_files)} local output file(s) found.",
        }

    # Try new structured prefix first, fall back to old flat prefix for reads
    try:
        new_prefix = f"{_b2_prefix(layer)}/{dataset_id}/"
    except KeyError:
        new_prefix = f"{layer}/{dataset_id}/"
    files, error = _safe_b2_list(new_prefix)
    if files is None:
        # Legacy fallback: try old flat prefix
        files, error = _safe_b2_list(f"{layer}/{dataset_id}/")
    if files is None:
        return {"status": "Unknown", "detail": f"{layer} listing unavailable: {error}"}

    status, detail = _status_from_files(files, pending_status="Pending")
    return {"status": status, "detail": detail}


def _rerun_status(dataset_id):
    rerun_dir = _project_path("data", "rerun_outputs")
    files = _files_for_patterns(rerun_dir, [f"{dataset_id}_*.rrd"])
    if files:
        return {
            "status": "Available",
            "detail": f"{len(files)} Rerun recording(s) found.",
        }
    return {
        "status": "Pending",
        "detail": "No Rerun recording is available yet for this dataset.",
    }


def _build_layer_state(dataset_id, metadata):
    storage = metadata.get("storage") or {}
    file_summaries = metadata.get("file_summaries") or []
    total_files = metadata.get("total_files")

    raw_available = bool(file_summaries) or bool(total_files) or bool(storage.get("raw_tile_prefix"))
    raw_status = "Available" if raw_available else "Unknown" if not metadata else "Missing"
    raw_detail = (
        f"{_format_number(total_files)} raw file(s) registered in metadata."
        if raw_available
        else "Raw upload registration could not be verified."
    )

    manifest = _manifest_status(dataset_id)
    metadata_status = "Available" if metadata else "Missing"
    metadata_detail = (
        f"Loaded data/metadata/datasets/{dataset_id}.json."
        if metadata
        else "Metadata JSON was not found for this dataset."
    )

    return {
        "bronze_raw_data": {"status": raw_status, "detail": raw_detail},
        "upload_manifests": manifest,
        "metadata": {"status": metadata_status, "detail": metadata_detail},
        "metadata_analytics": _analytics_status(dataset_id),
        "gold_model_ready_data": _gold_status(dataset_id),
        "segmentation_outputs": _prefix_output_status(dataset_id, "segmentation_outputs"),
        "clustered_final_outputs": _prefix_output_status(dataset_id, "clustered_final_outputs"),
        "rerun_outputs": _rerun_status(dataset_id),
    }


def _combine_statuses(statuses):
    normalized = [str(status or "Unknown") for status in statuses]
    if any(status == "Missing" for status in normalized):
        return "Missing"
    if any(status == "Unknown" for status in normalized):
        return "Unknown"
    if any(status == "Pending" for status in normalized):
        return "Pending"
    if all(status == "Available" for status in normalized):
        return "Available"
    return "Unknown"


def _render_dataset_summary(dataset_id, metadata):
    if not metadata:
        return _warning_card(
            f"No metadata found for dataset '{dataset_id}'.",
            "Upload and register the dataset from Data Explorer to generate lineage metadata.",
        )

    ready_for_training = _quality_flag(metadata, "ready_for_training")
    ready_for_inference = _quality_flag(metadata, "ready_for_inference")
    rows = [
        ("Dataset ID", dataset_id),
        ("Dataset Name", metadata.get("dataset_name") or "Unknown"),
        ("Upload Mode", metadata.get("upload_mode") or "Unknown"),
        ("Total Files", _format_number(metadata.get("total_files"))),
        ("Total Points", _format_number(metadata.get("total_points"))),
        ("Created At", metadata.get("created_at") or "Unknown"),
        ("Ready For Training", _yes_no_unknown(ready_for_training)),
        ("Ready For Inference", _yes_no_unknown(ready_for_inference)),
    ]

    return html.Div(
        [
            info_card(
                metadata.get("dataset_name") or dataset_id,
                f"Lineage is loaded live from {_b2_prefix('metadata')}/datasets/{dataset_id}.json.",
                "Selected Dataset",
            ),
            kv_grid(rows),
        ],
        className="ops-two-col",
    )


def _render_timeline(dataset_id, state, context):
    prep_version = context.get("prep_version") or "Unknown"
    model = context.get("model") or "Unknown"
    run_id = context.get("run_id") or "Unknown"

    bronze_status = _combine_statuses(
        [
            state["bronze_raw_data"]["status"],
            state["upload_manifests"]["status"],
        ]
    )

    stages = [
        {
            "stage": "bronze_raw_data",
            "title": "Raw Upload",
            "status": bronze_status,
            "detail": "Raw tiles, label maps, upload manifest, and checksum manifest.",
            "paths": [
                f"{bronze_tiles_prefix(dataset_id)}/",
                f"{bronze_label_maps_prefix(dataset_id)}/",
                f"{bronze_manifest_prefix(dataset_id)}/upload_manifest.json",
                f"{bronze_manifest_prefix(dataset_id)}/checksum_manifest.json",
            ],
        },
        {
            "stage": "metadata",
            "title": "Metadata Extraction",
            "status": state["metadata"]["status"],
            "detail": state["metadata"]["detail"],
            "paths": [f"{_b2_prefix('metadata')}/datasets/{dataset_id}.json"],
        },
        {
            "stage": "metadata_analytics",
            "title": "Metadata Analytics",
            "status": state["metadata_analytics"]["status"],
            "detail": state["metadata_analytics"]["detail"],
            "paths": [f"{_b2_prefix('metadata_analytics')}/{dataset_id}/*.parquet"],
        },
        {
            "stage": "gold_model_ready_data",
            "title": "Preprocessing",
            "status": state["gold_model_ready_data"]["status"],
            "detail": (
                "Active model-ready layer. silver_preprocessed_data is not required "
                "for this page."
            ),
            "paths": [f"{_b2_prefix('gold_model_ready_data')}/{dataset_id}/{prep_version}/"],
        },
        {
            "stage": "segmentation_outputs",
            "title": "Training / Inference",
            "status": state["segmentation_outputs"]["status"],
            "detail": state["segmentation_outputs"]["detail"],
            "paths": [f"{_b2_prefix('segmentation_outputs')}/{dataset_id}/{prep_version}/{model}/{run_id}/"],
        },
        {
            "stage": "clustered_final_outputs",
            "title": "Clustering",
            "status": state["clustered_final_outputs"]["status"],
            "detail": state["clustered_final_outputs"]["detail"],
            "paths": [f"{_b2_prefix('clustered_final_outputs')}/{dataset_id}/{prep_version}/{model}/{run_id}/"],
        },
        {
            "stage": "Rerun visualization",
            "title": "Rerun Visualization",
            "status": state["rerun_outputs"]["status"],
            "detail": state["rerun_outputs"]["detail"],
            "paths": [f"data/rerun_outputs/{dataset_id}_{run_id}.rrd"],
        },
    ]

    return html.Div(
        [
            _status_card(
                item["title"],
                item["status"],
                item["detail"],
                paths=item["paths"],
                kicker=item["stage"],
            )
            for item in stages
        ],
        className="ops-card-grid",
    )


def _check_status_from_flag(metadata, key):
    value = _quality_flag(metadata, key)
    if value is None:
        return "Unknown"
    return "Available" if value else "Missing"


def _render_governance_checks(metadata, state):
    raw_status = state["bronze_raw_data"]["status"]
    checksum_status = state["upload_manifests"].get("checksum_manifest") or "Unknown"
    metadata_status = state["metadata"]["status"]
    analytics_status = state["metadata_analytics"]["status"]

    label_maps = metadata.get("label_maps") or []
    label_flag = _quality_flag(metadata, "label_mapping_available")
    if label_maps or label_flag is True:
        label_status = "Available"
    elif label_flag is False:
        label_status = "Missing"
    else:
        label_status = "Unknown"

    checks = [
        (
            "Raw data registered",
            raw_status,
            state["bronze_raw_data"]["detail"],
        ),
        (
            "Checksum manifest available",
            checksum_status,
            state["upload_manifests"]["detail"],
        ),
        (
            "Metadata JSON available",
            metadata_status,
            state["metadata"]["detail"],
        ),
        (
            "Analytics parquet available",
            analytics_status,
            state["metadata_analytics"]["detail"],
        ),
        (
            "Label map available",
            label_status,
            "Label-map status is read from metadata label_maps and quality_flags.",
        ),
        (
            "Building mapping available",
            _check_status_from_flag(metadata, "building_mapping_available"),
            "Building-class mapping status is read from metadata quality_flags.",
        ),
        (
            "Preprocessing readiness",
            _check_status_from_flag(metadata, "ready_for_inference"),
            "A dataset can enter preprocessing when raw coordinates are available.",
        ),
        (
            "Training readiness",
            _check_status_from_flag(metadata, "ready_for_training"),
            "Training readiness is read from metadata quality_flags.ready_for_training.",
        ),
        (
            "Inference readiness",
            _check_status_from_flag(metadata, "ready_for_inference"),
            "Inference readiness is read from metadata quality_flags.ready_for_inference.",
        ),
    ]

    return html.Div(
        [
            _status_card(title, status, detail)
            for title, status, detail in checks
        ],
        className="ops-card-grid",
    )


def _render_lineage_events(lineage_result):
    message = str((lineage_result or {}).get("message") or "")
    if message.startswith("Lineage unavailable"):
        return _error_card("Lineage service failed.", message)

    events = (lineage_result or {}).get("events") or []
    rows = []
    for event in events:
        payload = _read_event_payload(event)
        conf = payload.get("conf") if isinstance(payload, dict) else {}
        if not isinstance(conf, dict):
            conf = {}

        rows.append(
            {
                "event": event.get("event") or "Unknown",
                "dataset_id": event.get("dataset_id") or conf.get("dataset_id") or "Unknown",
                "prep_version": conf.get("prep_version") or "Unknown",
                "run_id": conf.get("run_id") or payload.get("dag_run_id") or "Unknown",
                "artifact": event.get("artifact") or "Unknown",
                "status": event.get("status") or "Unknown",
            }
        )

    return data_table(
        rows,
        columns=[
            {"name": "Event", "id": "event"},
            {"name": "Dataset ID", "id": "dataset_id"},
            {"name": "Prep Version", "id": "prep_version"},
            {"name": "Run ID", "id": "run_id"},
            {"name": "Artifact", "id": "artifact"},
            {"name": "Status", "id": "status"},
        ],
        empty_title="No processing events",
        empty_detail="No processing events have been recorded yet for this dataset.",
    )


def _render_output_status(state, dataset_id, context):
    prep_version = context.get("prep_version") or "Unknown"
    model = context.get("model") or "Unknown"
    run_id = context.get("run_id") or "Unknown"

    rows = [
        (
            "bronze_raw_data",
            state["bronze_raw_data"],
            [f"{bronze_tiles_prefix(dataset_id)}/"],
        ),
        (
            "metadata",
            state["metadata"],
            [f"{_b2_prefix('metadata')}/datasets/{dataset_id}.json"],
        ),
        (
            "metadata_analytics",
            state["metadata_analytics"],
            [f"{_b2_prefix('metadata_analytics')}/{dataset_id}/"],
        ),
        (
            "gold_model_ready_data",
            state["gold_model_ready_data"],
            [f"{_b2_prefix('gold_model_ready_data')}/{dataset_id}/{prep_version}/"],
        ),
        (
            "segmentation_outputs",
            state["segmentation_outputs"],
            [f"{_b2_prefix('segmentation_outputs')}/{dataset_id}/{prep_version}/{model}/{run_id}/"],
        ),
        (
            "clustered_final_outputs",
            state["clustered_final_outputs"],
            [f"{_b2_prefix('clustered_final_outputs')}/{dataset_id}/{prep_version}/{model}/{run_id}/"],
        ),
        (
            "rerun_outputs",
            state["rerun_outputs"],
            [f"data/rerun_outputs/{dataset_id}_{run_id}.rrd"],
        ),
    ]

    return html.Div(
        [
            _status_card(layer, item["status"], item["detail"], paths=paths)
            for layer, item, paths in rows
        ],
        className="ops-card-grid",
    )


_EMPTY_SELECTED = _empty_alert(_NO_DATASET_MESSAGE, color="info")


layout = page_shell(
    active="Lineage",
    subtitle="Dataset traceability and audit contracts",
    status="Governance Shell",
    canvas_id="lineage-cv",
    eyebrow="Platform Operations",
    title="Lineage &",
    accent="Governance",
    description=(
        "Trace datasets from Bronze ingestion through metadata extraction, "
        "Gold model-ready outputs, training, inference, clustering, and Rerun artifacts."
    ),
    metrics=[
        ("Lineage Events", "Live"),
        ("Dataset", "Live"),
        ("Refresh", "60s"),
        ("Audit Status", "Live"),
    ],
    page_class="lineage-page",
    children=[
        dcc.Interval(
            id="lineage-governance-refresh",
            interval=_REFRESH_INTERVAL_MS,
            n_intervals=0,
        ),
        section(
            "Selection",
            "Selected dataset summary",
            "Dataset context is resolved from ?dataset_id=... first, then the session store.",
            html.Div(_EMPTY_SELECTED, id="lineage-dataset-summary"),
            "ops-panel-primary",
        ),
        section(
            "Timeline",
            "Dataset lineage timeline",
            "Live stage status for Bronze source data, metadata, Gold model-ready data, model outputs, clustering, and Rerun records.",
            html.Div("", id="lineage-timeline"),
        ),
        section(
            "Governance",
            "Governance checks",
            "Checks are derived from dataset metadata and real output-layer availability.",
            html.Div("", id="lineage-governance-checks"),
        ),
        section(
            "Audit",
            "Processing events",
            "Recorded preprocessing request events are loaded from the lineage service for the selected dataset.",
            html.Div("", id="lineage-events"),
        ),
        section(
            "Outputs",
            "Output layer status",
            "Each layer is marked Available, Pending, Missing, or Unknown from metadata, local artifacts, or service listing checks.",
            html.Div("", id="lineage-output-status"),
        ),
    ],
)


@callback(
    Output("lineage-dataset-summary", "children"),
    Output("lineage-timeline", "children"),
    Output("lineage-governance-checks", "children"),
    Output("lineage-events", "children"),
    Output("lineage-output-status", "children"),
    Input("selected-dataset-id", "data"),
    Input("lineage-governance-refresh", "n_intervals"),
    Input("url", "search"),
)
def update_lineage_governance(selected_dataset_id, _n_intervals, search):
    dataset_id = resolve_selected_dataset_id(search, selected_dataset_id)

    if not dataset_id:
        return _EMPTY_SELECTED, "", "", "", ""

    try:
        metadata = load_dataset_metadata(dataset_id)
    except Exception as exc:
        print(f"[LINEAGE_GOVERNANCE] Metadata load failed dataset_id={dataset_id}: {exc}")
        err = _error_card(
            f"Could not load metadata for dataset '{dataset_id}'.",
            str(exc),
        )
        return err, "", "", "", ""

    try:
        lineage_result = load_lineage_events(dataset_id)
    except Exception as exc:
        print(f"[LINEAGE_GOVERNANCE] Lineage service failed dataset_id={dataset_id}: {exc}")
        lineage_result = {"events": [], "message": f"Lineage unavailable: {exc}"}

    try:
        events = lineage_result.get("events") or []
        context = _build_context(dataset_id, metadata, events)
        state = _build_layer_state(dataset_id, metadata)

        summary = _render_dataset_summary(dataset_id, metadata)
        timeline = _render_timeline(dataset_id, state, context)
        checks = _render_governance_checks(metadata, state)
        event_table = _render_lineage_events(lineage_result)
        output_status = _render_output_status(state, dataset_id, context)

    except Exception as exc:
        print(f"[LINEAGE_GOVERNANCE] Render failed dataset_id={dataset_id}: {exc}")
        err = _error_card(
            f"Lineage for dataset '{dataset_id}' could not be rendered.",
            str(exc),
        )
        return err, err, err, err, err

    return summary, timeline, checks, event_table, output_status
