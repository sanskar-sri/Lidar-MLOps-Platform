import json
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from components.gold_layer_section import build_gold_layer_section
from components.platform_theme import (
    empty_state,
    ops_service_health_card,
    ops_table_style,
    ops_topbar,
    platform_hero,
    section_head,
    small_status,
    step_item,
)
from components.silver_layer_section import build_silver_layer_section
from services.compute_nodes_service import (
    COMPUTE_HEALTH_POLL_MS,
    check_compute_nodes,
    resolve_airflow_queue,
)
from services.metadata_service import list_registered_datasets, load_dataset_metadata
from services.mlflow_service import mlflow_browser_url
from services.preprocessing_runtime_service import (
    AIRFLOW_BASE_URL,
    AIRFLOW_PREPROCESSING_DAG_ID,
    build_airflow_status_snapshot,
    compute_silver_readiness,
    load_gold_metadata_if_available,
    load_local_or_b2_silver_metadata,
    trigger_airflow_preprocessing_dag,
    verify_b2_silver_outputs,
)
from services.preprocessing_service import (
    AIRFLOW_DAG_ID,
    DEFAULT_MLFLOW_TRACKING_URI,
    DEFAULT_NUM_SEGMENTS,
    DEFAULT_TEST_SEGMENTS,
    DEFAULT_TRAIN_SEGMENTS,
    DEFAULT_VAL_SEGMENTS,
    build_airflow_conf,
    build_dataset_config,
    build_minimal_trigger_conf,
    build_storage_contract,
    persist_airflow_request,
)


dash.register_page(__name__, path="/preprocessing", name="Preprocessing", title="Preprocessing - LiDAR Platform")


# Shared operations UI helpers live in components.platform_theme. Other pages can
# import COLORS, CARD_STYLE, HERO_STYLE, ops_topbar, status_badge, and platform_hero
# from that module when they are ready to remove their local duplicates.


POLL_MS = 5000
DEFAULT_PIPELINE_VERSION = "v9_airflow_compat"


def _dataset_options():
    try:
        datasets = list_registered_datasets()
    except Exception as exc:
        print(f"[PREPROCESSING DATASET LIST ERROR] {exc}")
        datasets = []

    return [
        {
            "label": f"{item.get('dataset_id', '')} - {item.get('dataset_name', '')}",
            "value": item.get("dataset_id", ""),
        }
        for item in datasets
        if item.get("dataset_id")
    ]


def _dataset_registry_row(dataset_id):
    if not dataset_id:
        return {}
    try:
        for row in list_registered_datasets():
            if row.get("dataset_id") == dataset_id:
                return row
    except Exception:
        return {}
    return {}


def _label_list_text(values):
    return ", ".join(str(value) for value in (values or []))


def _format_number(value):
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{int(number):,}" if number.is_integer() else f"{number:,.2f}"


def _duration_label(start_time, end_time):
    if not start_time:
        return "n/a"
    try:
        start = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        end = (
            datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
            if end_time
            else datetime.now(timezone.utc)
        )
        seconds = max(0, int((end - start).total_seconds()))
    except Exception:
        return "n/a"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _normalize_prefix(value):
    return str(value or "").strip().strip("/")


def _standard_silver_prefix(dataset_id, prep_version):
    dataset_id = (dataset_id or "<dataset_id>").strip() or "<dataset_id>"
    prep_version = (prep_version or "prep_v001").strip() or "prep_v001"
    return f"silver_preprocessed_data/{dataset_id}/{prep_version}"


def _standard_gold_prefix(dataset_id, prep_version):
    dataset_id = (dataset_id or "<dataset_id>").strip() or "<dataset_id>"
    prep_version = (prep_version or "prep_v001").strip() or "prep_v001"
    return f"gold_model_ready_data/{dataset_id}/{prep_version}"


def _storage_rows(storage):
    order = [
        ("raw_tiles", "Bronze raw tiles"),
        ("label_maps", "Bronze label maps"),
        ("registry_metadata", "Registry metadata"),
        ("analytics", "Metadata analytics"),
        ("silver_output", "Silver conformed cloud"),
        ("gold_output", "Gold model-ready data"),
        ("preprocessing_logs", "Run logs"),
        ("preprocessing_metadata", "Preprocessing metadata"),
    ]
    return [
        {"role": label, "path": storage.get(key)}
        for key, label in order
        if storage.get(key)
    ]


def _metric_pair(label, value):
    return html.Div(
        [html.Span(label, className="prep-summary-label"), html.Span(value, className="prep-summary-value")],
        className="prep-summary-pair",
    )


def _dataset_summary_card(dataset_id):
    if not dataset_id:
        return empty_state("Dataset identity", "Select a registered dataset to inspect source metadata and B2 prefixes.")

    row = _dataset_registry_row(dataset_id)
    metadata = load_dataset_metadata(dataset_id)
    file_count = row.get("total_files") or metadata.get("total_files")
    total_points = row.get("total_points") or metadata.get("total_points")
    labels = row.get("labels") or metadata.get("labels")
    source_prefix = f"bronze_raw_data/{dataset_id}/source_files/tiles/"
    return html.Div(
        [
            _metric_pair("Dataset ID", dataset_id),
            _metric_pair("Dataset name", row.get("dataset_name") or metadata.get("dataset_name") or dataset_id),
            _metric_pair("Registry status", row.get("status") or metadata.get("status") or "registered"),
            _metric_pair("Source tier", "Bronze raw data"),
            _metric_pair("Raw files", _format_number(file_count)),
            _metric_pair("Raw points", _format_number(total_points)),
            _metric_pair("Labels", "available" if labels else "not found"),
            _metric_pair("B2 source prefix", source_prefix),
        ],
        className="prep-summary-grid",
    )


def _status_panel(status):
    if not status:
        return empty_state("Live Airflow status", "Start preprocessing to begin polling the remote DAG run.")

    state = status.get("state", "unknown")
    progress = status.get("progress_pct", 0)
    rows = [
        {"field": "DAG ID", "value": status.get("dag_id") or AIRFLOW_PREPROCESSING_DAG_ID},
        {"field": "DAG run ID", "value": status.get("dag_run_id") or "n/a"},
        {"field": "State", "value": state},
        {"field": "Current task", "value": status.get("current_task") or "n/a"},
        {"field": "Completed tasks", "value": f"{status.get('completed_tasks', 0)} / {status.get('total_tasks', 0)}"},
        {"field": "Start time", "value": status.get("start_time") or "n/a"},
        {"field": "End time", "value": status.get("end_time") or "n/a"},
        {"field": "Duration", "value": _duration_label(status.get("start_time"), status.get("end_time"))},
        {"field": "Checked at", "value": status.get("checked_at") or "n/a"},
    ]

    completed = int(status.get("completed_tasks") or 0)
    total = int(status.get("total_tasks") or 0)
    task_chips = (
        html.Div(
            [
                small_status(
                    f"Task {index + 1}",
                    "success" if index < completed else ("running" if state in {"queued", "running", "scheduled"} and index == completed else "pending"),
                )
                for index in range(total)
            ],
            className="prep-task-chips",
        )
        if total
        else None
    )

    return html.Div(
        [
            html.Div(
                [
                    small_status("DAG state", state),
                    html.Div(
                        [
                            html.Span(f"{progress}%", className="airflow-progress-label"),
                            html.Span(f"elapsed {_duration_label(status.get('start_time'), status.get('end_time'))}"),
                        ],
                        className="airflow-progress-label-group",
                    ),
                ],
                className="airflow-status-head",
            ),
            dbc.Progress(value=min(max(float(progress or 0), 0), 100), striped=state in {"queued", "running"}, animated=state == "running", className="airflow-progress"),
            task_chips,
            dash_table.DataTable(
                columns=[{"name": "Field", "id": "field"}, {"name": "Value", "id": "value"}],
                data=rows,
                page_size=8,
                **ops_table_style(),
            ),
            dbc.Alert(
                [
                    html.Strong(f"Failed task: {status.get('failed_task') or 'unknown'}"),
                    html.Pre(status.get("latest_error") or "No Airflow log excerpt was available.", className="ops-code-box"),
                ],
                color="danger",
                className="mt-3",
                is_open=state == "failed",
            ),
        ],
        className="airflow-status-panel",
    )


def _verification_panel(verification):
    if not verification:
        return empty_state("B2 Silver verification", "Verification runs automatically after a successful Airflow run, or manually with the button below.")
    rows = verification.get("rows") or []
    return html.Div(
        [
            html.Div(
                [
                    small_status("Silver artifacts", verification.get("status", "unknown")),
                    html.Span(
                        f"{verification.get('verified_count', 0)} / {verification.get('expected_count', 0)} verified",
                        className="ops-muted-copy",
                    ),
                ],
                className="silver-status-row",
            ),
            dash_table.DataTable(
                columns=[
                    {"name": "Artifact", "id": "artifact"},
                    {"name": "Required", "id": "required"},
                    {"name": "Status", "id": "status"},
                    {"name": "Size", "id": "size_display"},
                    {"name": "B2 key", "id": "b2_key"},
                ],
                data=rows,
                page_size=8,
                **ops_table_style(),
            ),
            dbc.Alert(verification.get("error") or "", color="warning", className="mt-3", is_open=bool(verification.get("error"))),
        ],
        className="silver-verification-panel",
    )


def _parameter_summary(conf):
    if not conf:
        return []
    args = conf.get("script_args") or {}
    dataset_config = conf.get("dataset_config") or {}
    return [
        {"parameter": "dataset_id", "value": conf.get("dataset_id")},
        {"parameter": "dataset_name", "value": conf.get("dataset_name")},
        {"parameter": "execution_target", "value": conf.get("execution_target")},
        {"parameter": "airflow_queue", "value": conf.get("airflow_queue")},
        {"parameter": "pipeline_version", "value": conf.get("pipeline_version")},
        {"parameter": "prep_version", "value": conf.get("prep_version")},
        {"parameter": "output_tier", "value": conf.get("output_tier")},
        {"parameter": "voxel_size", "value": args.get("voxel_size")},
        {"parameter": "voxel_keep_strategy", "value": args.get("voxel_keep_strategy")},
        {"parameter": "input_features", "value": ", ".join(args.get("input_features") or [])},
        {"parameter": "label_mapping_mode", "value": args.get("label_mapping_mode")},
        {"parameter": "label_field", "value": dataset_config.get("label_field")},
        {"parameter": "coordinate_normalization", "value": args.get("coordinate_normalization")},
        {"parameter": "split_strategy", "value": args.get("split_strategy")},
        {"parameter": "train/val/test segments", "value": f"{args.get('train_segments')}/{args.get('val_segments')}/{args.get('test_segments')}"},
        {"parameter": "silver_b2_prefix", "value": args.get("b2_silver_prefix")},
        {"parameter": "gold_b2_prefix", "value": args.get("b2_output_prefix")},
        {"parameter": "mlflow_tracking_uri", "value": args.get("mlflow_tracking_uri")},
    ]


def _build_conf_from_values(
    dataset_id,
    dataset_name,
    label_field,
    building_labels,
    non_building_labels,
    ignore_labels,
    mode,
    prep_version,
    execution_target,
    output_mode,
    output_tier,
    pipeline_version,
    b2_output_prefix,
    input_features,
    label_mode,
    coordinate_normalization,
    split_strategy,
    voxel_size,
    voxel_keep_strategy,
    block_size,
    n_points,
    max_blocks_train,
    stride_val_test,
    split_gap_m,
    num_segments,
    train_segments,
    val_segments,
    test_segments,
    min_bldg_ratio,
    randla_overlap,
    ptv3_scene_length,
    num_workers,
    feature_flags,
    mlflow_tracking_uri,
    mlflow_experiment,
    mlflow_run_name,
    dvc_remote,
    mlops_flags,
):
    flags = set(feature_flags or [])
    mlops = set(mlops_flags or [])
    dataset_id = (dataset_id or "").strip()
    dataset_name = (dataset_name or dataset_id).strip()
    prep_version = (prep_version or "prep_v001").strip() or "prep_v001"
    pipeline_version = (pipeline_version or DEFAULT_PIPELINE_VERSION).strip() or DEFAULT_PIPELINE_VERSION
    silver_prefix = _normalize_prefix(b2_output_prefix) or _standard_silver_prefix(dataset_id, prep_version)
    gold_prefix = _standard_gold_prefix(dataset_id, prep_version)
    airflow_queue = resolve_airflow_queue(execution_target)

    conf = build_airflow_conf(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        mode=mode,
        prep_version=prep_version,
        output_mode=output_mode,
        voxel_size=voxel_size or 0.02,
        voxel_keep_strategy=voxel_keep_strategy or "representative",
        block_size=block_size or 2.0,
        n_points=n_points or 8192,
        max_blocks_train=max_blocks_train or 8000,
        stride_val_test=stride_val_test or 1.5,
        split_gap_m=split_gap_m or 2.0,
        num_segments=num_segments or DEFAULT_NUM_SEGMENTS,
        train_segments=train_segments or DEFAULT_TRAIN_SEGMENTS,
        val_segments=val_segments or DEFAULT_VAL_SEGMENTS,
        test_segments=test_segments or DEFAULT_TEST_SEGMENTS,
        min_bldg_ratio=min_bldg_ratio or 0.01,
        randla_overlap=randla_overlap or 0.0,
        ptv3_scene_length=ptv3_scene_length or 50.0,
        num_workers=num_workers or 24,
        compute_normals="compute_normals" in flags,
        include_density="include_density" in flags,
        save_ply="save_ply" in flags,
        compress_output="compress_output" in flags,
        write_silver="write_silver" in flags,
        label_field=label_field,
        building_labels=building_labels,
        non_building_labels=non_building_labels,
        ignore_labels=ignore_labels,
        execution_target=execution_target,
        airflow_queue=airflow_queue,
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_experiment=mlflow_experiment,
        mlflow_run_name=mlflow_run_name,
        dvc_remote=dvc_remote,
        disable_mlflow="disable_mlflow" in mlops,
        mlflow_log_artifacts="log_artifacts" in mlops,
    )

    conf["dag_id"] = AIRFLOW_PREPROCESSING_DAG_ID or AIRFLOW_DAG_ID
    conf["pipeline_version"] = pipeline_version
    conf["output_tier"] = output_tier or "silver_and_gold"
    conf["storage"]["silver_output"] = f"b2://{conf['storage']['bucket']}/{silver_prefix}/"
    conf["storage"]["gold_output"] = f"b2://{conf['storage']['bucket']}/{gold_prefix}/"
    conf["dataset_config"]["utm_offset_subtract"] = coordinate_normalization == "offset_subtract"
    conf["script_args"].update(
        {
            "pipeline_version": pipeline_version,
            "output_tier": output_tier or "silver_and_gold",
            "build_gold": output_tier != "silver_only",
            "input_features": input_features or ["xyz", "intensity", "rgb", "labels"],
            "label_mapping_mode": label_mode or "registry_or_raw_ids",
            "coordinate_normalization": coordinate_normalization or "offset_subtract",
            "split_strategy": split_strategy or "segment_spatial",
            "b2_silver_prefix": silver_prefix,
            "b2_output_prefix": gold_prefix,
        }
    )
    return conf



def _preproc_stepper(dataset_id=None, airflow_status=None, dag_run=None, silver_status=None):
    state = (airflow_status or dag_run or {}).get("state")
    silver_state = (silver_status or {}).get("status")
    has_run = bool((dag_run or {}).get("dag_run_id"))
    running = state in {"queued", "running", "scheduled"}
    airflow_done = state == "success"

    return [
        step_item("01", "Dataset", "Select source, labels, and routing", "blue", "done" if dataset_id else "active"),
        step_item("02", "Parameters", "Configure voxel, features, splits", "green", "done" if has_run else ("active" if dataset_id else None)),
        step_item("03", "Execution", "Trigger and monitor Airflow", "purple", "done" if airflow_done else ("active" if running else None)),
        step_item("04", "Outputs", "Verify Silver and preview Gold", "amber", "done" if silver_state == "passed" else ("active" if airflow_done else None)),
    ]


def _preflight_banner(dataset_id, seg_class, b2_prefix):
    checks = [
        ("Dataset", bool(dataset_id), dataset_id or "not set"),
        ("Segments", "ops-validation-badge-danger" not in (seg_class or ""), "valid" if "danger" not in (seg_class or "") else "check split"),
        ("B2 Prefix", bool(_normalize_prefix(b2_prefix)), _normalize_prefix(b2_prefix) or "not set"),
    ]
    all_ok = all(ok for _, ok, _ in checks)
    children = [
        small_status(label, "success" if ok else detail)
        for label, ok, detail in checks
    ]
    return children, f"ops-preflight-banner {'ops-preflight-banner-ok' if all_ok else 'ops-preflight-banner-warn'}"


def _segment_split_blocker(mode, num_segments, train_segments, val_segments, test_segments):
    if mode == "inference":
        return None
    total = int(num_segments or DEFAULT_NUM_SEGMENTS)
    train = int(train_segments or DEFAULT_TRAIN_SEGMENTS)
    val = int(val_segments or DEFAULT_VAL_SEGMENTS)
    test = int(test_segments or DEFAULT_TEST_SEGMENTS)
    if train + val + test == total:
        return None
    return f"Train + val + test segments must equal total segments ({train} + {val} + {test} != {total})."


# ---------------------------------------------------------------------------
# Layout helpers — DE-style visual language
# ---------------------------------------------------------------------------

def _stat_tile(label, display):
    return html.Div(
        [
            html.Div(str(display), className="de-stat-value lp-stat-value"),
            html.Div(label, className="de-stat-label"),
        ],
        className="de-stat",
    )


def _section_label(text):
    return html.Div(text, className="preproc-section-label")


def _field(label, *children, wide=False):
    cls = "ops-field ops-field-wide" if wide else "ops-field"
    return html.Div([dbc.Label(label), *children], className=cls)


def _preproc_sidebar():
    return html.Aside(
        className="data-explorer-sidebar",
        children=[
            html.Div(
                [
                    html.Div("Datasets", className="data-explorer-eyebrow"),
                    html.H4("Select a dataset"),
                    html.P(
                        "Pick a registered dataset to begin the medallion pipeline.",
                        className="mb-2",
                    ),
                ],
                className="data-explorer-sidebar-head",
            ),
            dcc.Dropdown(
                id="preproc-dataset-dropdown",
                options=_dataset_options(),
                placeholder="Select a dataset…",
                clearable=True,
                className="mb-3",
            ),
            html.Div(
                [
                    dbc.Input(id="preproc-dataset-id", persistence=True, persistence_type="session"),
                    dbc.Input(id="preproc-dataset-name", persistence=True, persistence_type="session"),
                    dbc.Input(id="preproc-execution-target", value="any_gpu_worker"),
                ],
                style={"display": "none"},
            ),
            html.Div(id="preproc-dataset-summary", className="mb-3"),
            _section_label("Compute Nodes"),
            html.Div(
                id="preproc-compute-health-grid",
                className="prep-node-grid ops-node-grid mb-3",
            ),
            dbc.Button(
                "Start Preprocessing",
                id="preproc-quick-start-button",
                color="success",
                disabled=True,
                className="w-100 mb-2",
            ),
            html.Span(
                "Select a dataset to enable quick-start with default parameters",
                id="preproc-quick-start-hint",
                className="ops-muted-copy",
                style={"fontSize": "11px"},
            ),
            html.Div(id="registry-action-message", className="mt-2"),
        ],
    )


def _tab_dataset():
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Step 01", className="data-explorer-eyebrow"),
                    html.H3("Dataset and Label Configuration"),
                    html.P(
                        "Configure label field mappings, binary class IDs, "
                        "spatial split routing, and B2 output path for this run."
                    ),
                ],
                className="preproc-tab-head",
            ),
            html.Div(
                [
                    _field("Label Field", dbc.Input(id="preproc-label-field", value="class", placeholder="class or scalar_Label", persistence=True, persistence_type="session")),
                    _field("Building Labels", dbc.Input(id="preproc-building-labels", value="2", placeholder="e.g. 2 or 4", persistence=True, persistence_type="session")),
                    _field("Non-Building Labels", dbc.Input(id="preproc-non-building-labels", value="1, 3, 4, 5, 6, 7, 8, 9", placeholder="Comma-separated raw class IDs", persistence=True, persistence_type="session"), wide=True),
                    _field("Ignore Labels", dbc.Input(id="preproc-ignore-labels", value="0", placeholder="Comma-separated raw class IDs", persistence=True, persistence_type="session")),
                    _field("Mode", dcc.Dropdown(
                        id="preproc-mode",
                        options=[
                            {"label": "Training — labels required", "value": "train"},
                            {"label": "Inference — labels optional", "value": "inference"},
                        ],
                        value="train",
                        clearable=False,
                    )),
                    _field("B2 Silver Output Prefix", dbc.Input(id="preproc-b2-output-prefix", placeholder="silver_preprocessed_data/<dataset>/<prep_version>", persistence=True, persistence_type="session"), wide=True),
                ],
                className="ops-field-grid",
            ),
            html.Hr(className="preproc-tab-divider"),
            _section_label("Segment Split"),
            html.P("Train + val + test must equal total segments.", className="ops-muted-copy"),
            html.Div(
                [
                    _field("Total", dbc.Input(id="preproc-num-segments", type="number", value=DEFAULT_NUM_SEGMENTS, min=1, step=1)),
                    _field("Train", dbc.Input(id="preproc-train-segments", type="number", value=DEFAULT_TRAIN_SEGMENTS, min=0, step=1)),
                    _field("Val", dbc.Input(id="preproc-val-segments", type="number", value=DEFAULT_VAL_SEGMENTS, min=0, step=1)),
                    _field("Test", dbc.Input(id="preproc-test-segments", type="number", value=DEFAULT_TEST_SEGMENTS, min=0, step=1)),
                    html.Div(id="preproc-segment-validation", className="ops-validation-badge"),
                ],
                className="ops-field-grid ops-segment-grid",
            ),
        ],
        className="preproc-tab-body",
    )


def _preprocessing_workflow_card():
    workflow_steps = [
        "Raw Point Cloud Data",
        "Read PLY / LAS / LAZ Files",
        "Extract XYZ Coordinates and Point Attributes",
        "Validate Required Fields",
        "Apply Dataset-Specific Label Mapping",
        "Generate Binary Labels: Building / Non-Building",
        "Split Large Scenes into Spatial Blocks",
        "Normalize Coordinates within Each Block",
        "Sample Fixed Number of Points per Block",
        "Save Model-Ready Training Data",
        "Use for Model Training / Validation / Testing",
    ]

    return html.Div(
        [
            html.Div(
                [
                    html.Div("Workflow Reference", className="data-explorer-eyebrow"),
                    html.H3("Preprocessing Workflow"),
                    html.P(
                        "The preprocessing workflow transforms raw LiDAR point cloud files into a "
                        "structured and consistent format that deep learning models can consume directly. "
                        "Raw point clouds are usually large, irregular, and dataset-specific, so the "
                        "pipeline standardizes geometry, attributes, labels, and block size before training."
                    ),
                    html.P(
                        "The script reads PLY, LAS, or LAZ files, extracts coordinates and attributes such "
                        "as RGB, intensity, classification, or semantic labels, validates required fields, "
                        "maps dataset labels into building and non-building classes, divides large scenes "
                        "into spatial blocks, normalizes coordinates, samples a fixed number of points, and "
                        "saves model-ready data for training, validation, and testing."
                    ),
                    html.Div(
                        [
                            html.Span("PointNet++"),
                            html.Span("PointNet++ MSG"),
                            html.Span("RandLA-Net"),
                            html.Span("KPConv"),
                            html.Span("DGCNN"),
                            html.Span("PointNeXt"),
                        ],
                        className="preproc-workflow-models",
                    ),
                ],
                className="preproc-workflow-copy",
            ),
            html.Ol(
                [
                    html.Li(
                        [
                            html.Span(f"{index:02d}", className="preproc-flow-index"),
                            html.Span(step, className="preproc-flow-label"),
                        ],
                        className="preproc-flow-step",
                    )
                    for index, step in enumerate(workflow_steps, start=1)
                ],
                className="preproc-flow-list",
            ),
        ],
        className="ops-review-card preproc-workflow-card",
    )


def _tab_parameters():
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Step 02", className="data-explorer-eyebrow"),
                    html.H3("Pipeline Parameters"),
                    html.P(
                        "Every value below is injected directly into the Airflow DAG conf — "
                        "no hidden defaults are applied by Dash."
                    ),
                ],
                className="preproc-tab-head",
            ),
            _preprocessing_workflow_card(),
            _section_label("Versioning and Output"),
            html.Div(
                [
                    _field("Preprocessing Version", dbc.Input(id="preproc-version", value="", placeholder="auto — workstation increments from last run", persistence=True, persistence_type="session")),
                    _field("Pipeline Version", dbc.Input(id="preproc-pipeline-version", value=DEFAULT_PIPELINE_VERSION, persistence=True, persistence_type="session")),
                    _field("Output Tier", dcc.Dropdown(
                        id="preproc-output-tier",
                        options=[
                            {"label": "Silver and Gold", "value": "silver_and_gold"},
                            {"label": "Silver only", "value": "silver_only"},
                        ],
                        value="silver_and_gold",
                        clearable=False,
                    )),
                    _field("Output Mode", dcc.Dropdown(
                        id="preproc-output-mode",
                        options=[
                            {"label": "All model formats", "value": "all"},
                            {"label": "Traditional blocks only", "value": "traditional"},
                            {"label": "PTv3 / Pointcept only", "value": "ptv3"},
                        ],
                        value="all",
                        clearable=False,
                    )),
                ],
                className="ops-field-grid",
            ),
            _section_label("Features and Encoding"),
            html.Div(
                [
                    _field("Input Features", dbc.Checklist(
                        id="preproc-input-features",
                        options=[
                            {"label": "XYZ", "value": "xyz"},
                            {"label": "Intensity", "value": "intensity"},
                            {"label": "RGB", "value": "rgb"},
                            {"label": "Labels", "value": "labels"},
                        ],
                        value=["xyz", "intensity", "rgb", "labels"],
                        inline=True,
                        switch=True,
                    ), wide=True),
                    _field("Label Mapping Mode", dcc.Dropdown(
                        id="preproc-label-mode",
                        options=[
                            {"label": "Registry or raw numeric IDs", "value": "registry_or_raw_ids"},
                            {"label": "Raw numeric IDs only", "value": "raw_numeric_ids"},
                            {"label": "External XML/JSON map required", "value": "mapping_file_required"},
                        ],
                        value="registry_or_raw_ids",
                        clearable=False,
                    )),
                    _field("Coordinate Normalization", dcc.Dropdown(
                        id="preproc-coordinate-normalization",
                        options=[
                            {"label": "Subtract coordinate offset", "value": "offset_subtract"},
                            {"label": "Keep original coordinates", "value": "none"},
                        ],
                        value="offset_subtract",
                        clearable=False,
                    )),
                    _field("Split Strategy", dcc.Dropdown(
                        id="preproc-split-strategy",
                        options=[
                            {"label": "Spatial segment split", "value": "segment_spatial"},
                            {"label": "Tile-level split", "value": "tile_split"},
                            {"label": "Existing split metadata", "value": "existing_split_metadata"},
                        ],
                        value="segment_spatial",
                        clearable=False,
                    )),
                ],
                className="ops-field-grid",
            ),
            _section_label("Voxelization and Blocking"),
            html.Div(
                [
                    _field("Voxel Size (m)", dbc.Input(id="preproc-voxel-size", type="number", value=0.02, step=0.01, min=0)),
                    _field("Voxel Keep Strategy", dcc.Dropdown(
                        id="preproc-voxel-strategy",
                        options=[
                            {"label": "Representative point", "value": "representative"},
                            {"label": "Centroid mean", "value": "centroid"},
                        ],
                        value="representative",
                        clearable=False,
                    )),
                    _field("Block Size (m)", dbc.Input(id="preproc-block-size", type="number", value=2.0, step=0.5)),
                    _field("Points / Block", dbc.Input(id="preproc-n-points", type="number", value=8192, step=1024)),
                    _field("Max Train Blocks", dbc.Input(id="preproc-max-blocks", type="number", value=8000, step=500)),
                    _field("Val / Test Stride", dbc.Input(id="preproc-stride", type="number", value=1.5, step=0.25)),
                    _field("Split Gap (m)", dbc.Input(id="preproc-split-gap", type="number", value=2.0, step=0.5)),
                    _field("Min Building Ratio", dbc.Input(id="preproc-min-bldg", type="number", value=0.01, step=0.005)),
                ],
                className="ops-field-grid ops-field-grid-four",
            ),
            _section_label("Model-Specific and Workers"),
            html.Div(
                [
                    _field("RandLA Overlap", dbc.Input(id="preproc-randla-overlap", type="number", value=0.0, step=0.05, min=0, max=0.95)),
                    _field("PTv3 Scene Length", dbc.Input(id="preproc-ptv3-length", type="number", value=50.0, step=5)),
                    _field("Workers", dbc.Input(id="preproc-workers", type="number", min=1, max=64, value=24)),
                    _field("Generated Features", dbc.Checklist(
                        id="preproc-feature-flags",
                        options=[
                            {"label": "Compute normals", "value": "compute_normals"},
                            {"label": "Include density", "value": "include_density"},
                            {"label": "Write silver tier", "value": "write_silver"},
                            {"label": "Save PLY previews", "value": "save_ply"},
                            {"label": "Compress output", "value": "compress_output"},
                        ],
                        value=["compute_normals", "include_density", "write_silver", "save_ply"],
                        inline=True,
                        switch=True,
                    ), wide=True),
                ],
                className="ops-field-grid",
            ),
        ],
        className="preproc-tab-body",
    )


def _tab_execute():
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Step 03", className="data-explorer-eyebrow"),
                    html.H3("MLOps and Airflow Execution"),
                    html.P(
                        "Set tracking destinations, review the generated DAG conf, "
                        "and trigger the remote Airflow preprocessing run."
                    ),
                ],
                className="preproc-tab-head",
            ),
            _section_label("Tracking and MLOps"),
            html.Div(
                [
                    html.Div(
                        [
                            dbc.Label("MLflow Tracking URI"),
                            dbc.Input(
                                id="preproc-mlflow-tracking-uri",
                                value=DEFAULT_MLFLOW_TRACKING_URI,
                                placeholder="./mlruns or http://mlflow-host:5000",
                                persistence=True,
                                persistence_type="session",
                            ),
                            html.A(
                                dbc.Button(
                                    "Open MLflow",
                                    id="preproc-open-mlflow-button",
                                    color="info",
                                    outline=True,
                                    size="sm",
                                    className="mt-2 w-100",
                                ),
                                id="preproc-open-mlflow-link",
                                href=mlflow_browser_url(DEFAULT_MLFLOW_TRACKING_URI),
                                target="_blank",
                                rel="noopener noreferrer",
                            ),
                        ],
                        className="ops-field",
                    ),
                    _field("MLflow Experiment", dbc.Input(id="preproc-mlflow-experiment", value="mls-preprocessing", persistence=True, persistence_type="session")),
                    _field("DVC Remote", dbc.Input(id="preproc-dvc-remote", value="b2remote", persistence=True, persistence_type="session")),
                    _field("MLflow Run Name", dbc.Input(id="preproc-mlflow-run-name", placeholder="Optional; defaults to dataset + run id", persistence=True, persistence_type="session")),
                    _field("Tracking Options", dbc.Checklist(
                        id="preproc-mlops-flags",
                        options=[
                            {"label": "Log small MLflow artifacts", "value": "log_artifacts"},
                            {"label": "Disable MLflow", "value": "disable_mlflow"},
                        ],
                        value=["log_artifacts"],
                        inline=True,
                        switch=True,
                    ), wide=True),
                ],
                className="ops-field-grid",
            ),
            html.Hr(className="preproc-tab-divider"),
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Preprocessing Parameter Summary"),
                            dash_table.DataTable(
                                id="preproc-parameter-summary-table",
                                columns=[{"name": "Parameter", "id": "parameter"}, {"name": "Value", "id": "value"}],
                                data=[],
                                page_size=18,
                                **ops_table_style(),
                            ),
                        ],
                        className="ops-review-card",
                    ),
                    html.Div(
                        [
                            html.H3("Bucket Contract"),
                            dash_table.DataTable(
                                id="preproc-storage-table",
                                columns=[{"name": "Role", "id": "role"}, {"name": "B2 Path", "id": "path"}],
                                data=[],
                                page_size=10,
                                **ops_table_style(),
                            ),
                        ],
                        className="ops-review-card",
                    ),
                ],
                className="ops-review-grid",
            ),
            html.Div(
                [
                    dbc.Button("Start Preprocessing", id="preproc-trigger-airflow-button", color="success", className="ops-trigger-button"),
                ],
                className="ops-trigger-row",
            ),
            html.Div(id="preproc-action-message", className="mt-3"),
        ],
        className="preproc-tab-body",
    )


def _tab_silver():
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Step 04a", className="data-explorer-eyebrow"),
                    html.H3("Silver Layer Verification"),
                    html.P(
                        "Verify that Silver artifacts exist in B2 before unlocking "
                        "the Gold output contract and analytics."
                    ),
                ],
                className="preproc-tab-head",
            ),
            html.Div(
                [
                    dbc.Button(
                        "Verify Silver Outputs in B2",
                        id="preproc-verify-silver-button",
                        color="info",
                        outline=True,
                    ),
                    html.Div(id="preproc-silver-verification-panel", className="mt-3"),
                ],
                className="ops-review-card",
            ),
            html.Div(id="preproc-silver-layer-container"),
        ],
        className="preproc-tab-body",
    )


def _tab_gold():
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Step 04b", className="data-explorer-eyebrow"),
                    html.H3("Gold Output Contract"),
                    html.P(
                        "Gold artifacts remain planned until Silver gates pass. "
                        "Training consumes the blocks, split files, and metadata from this contract."
                    ),
                ],
                className="preproc-tab-head",
            ),
            html.Div(id="preproc-gold-layer-container"),
        ],
        className="preproc-tab-body",
    )


def _form_state_specs(prefix="State"):
    return [
        State("preproc-dataset-id", "value"),
        State("preproc-dataset-name", "value"),
        State("preproc-label-field", "value"),
        State("preproc-building-labels", "value"),
        State("preproc-non-building-labels", "value"),
        State("preproc-ignore-labels", "value"),
        State("preproc-mode", "value"),
        State("preproc-version", "value"),
        State("preproc-execution-target", "value"),
        State("preproc-output-mode", "value"),
        State("preproc-output-tier", "value"),
        State("preproc-pipeline-version", "value"),
        State("preproc-b2-output-prefix", "value"),
        State("preproc-input-features", "value"),
        State("preproc-label-mode", "value"),
        State("preproc-coordinate-normalization", "value"),
        State("preproc-split-strategy", "value"),
        State("preproc-voxel-size", "value"),
        State("preproc-voxel-strategy", "value"),
        State("preproc-block-size", "value"),
        State("preproc-n-points", "value"),
        State("preproc-max-blocks", "value"),
        State("preproc-stride", "value"),
        State("preproc-split-gap", "value"),
        State("preproc-num-segments", "value"),
        State("preproc-train-segments", "value"),
        State("preproc-val-segments", "value"),
        State("preproc-test-segments", "value"),
        State("preproc-min-bldg", "value"),
        State("preproc-randla-overlap", "value"),
        State("preproc-ptv3-length", "value"),
        State("preproc-workers", "value"),
        State("preproc-feature-flags", "value"),
        State("preproc-mlflow-tracking-uri", "value"),
        State("preproc-mlflow-experiment", "value"),
        State("preproc-mlflow-run-name", "value"),
        State("preproc-dvc-remote", "value"),
        State("preproc-mlops-flags", "value"),
    ]


FORM_INPUTS = [
    Input("preproc-dataset-id", "value"),
    Input("preproc-dataset-name", "value"),
    Input("preproc-label-field", "value"),
    Input("preproc-building-labels", "value"),
    Input("preproc-non-building-labels", "value"),
    Input("preproc-ignore-labels", "value"),
    Input("preproc-mode", "value"),
    Input("preproc-version", "value"),
    Input("preproc-execution-target", "value"),
    Input("preproc-output-mode", "value"),
    Input("preproc-output-tier", "value"),
    Input("preproc-pipeline-version", "value"),
    Input("preproc-b2-output-prefix", "value"),
    Input("preproc-input-features", "value"),
    Input("preproc-label-mode", "value"),
    Input("preproc-coordinate-normalization", "value"),
    Input("preproc-split-strategy", "value"),
    Input("preproc-voxel-size", "value"),
    Input("preproc-voxel-strategy", "value"),
    Input("preproc-block-size", "value"),
    Input("preproc-n-points", "value"),
    Input("preproc-max-blocks", "value"),
    Input("preproc-stride", "value"),
    Input("preproc-split-gap", "value"),
    Input("preproc-num-segments", "value"),
    Input("preproc-train-segments", "value"),
    Input("preproc-val-segments", "value"),
    Input("preproc-test-segments", "value"),
    Input("preproc-min-bldg", "value"),
    Input("preproc-randla-overlap", "value"),
    Input("preproc-ptv3-length", "value"),
    Input("preproc-workers", "value"),
    Input("preproc-feature-flags", "value"),
    Input("preproc-mlflow-tracking-uri", "value"),
    Input("preproc-mlflow-experiment", "value"),
    Input("preproc-mlflow-run-name", "value"),
    Input("preproc-dvc-remote", "value"),
    Input("preproc-mlops-flags", "value"),
]


layout = dbc.Container(
    fluid=True,
    className="data-explorer-page preproc-page",
    children=[
        # ── Infrastructure ────────────────────────────────────────────────
        html.Div(id="preproc-stepper", style={"display": "none"}),
        dcc.Store(id="preproc-conf-store"),
        dcc.Store(id="preproc-dag-run-store"),
        dcc.Store(id="preproc-airflow-status-store"),
        dcc.Store(id="preproc-silver-status-store"),
        dcc.Interval(id="preproc-dataset-refresh", interval=60000, n_intervals=0),
        dcc.Interval(id="preproc-compute-health-refresh", interval=COMPUTE_HEALTH_POLL_MS, n_intervals=0),
        dcc.Interval(id="preproc-airflow-status-refresh", interval=POLL_MS, n_intervals=0, disabled=True),

        # ── Topbar ────────────────────────────────────────────────────────
        html.Div(
            className="de-topbar",
            children=[
                html.Div(
                    className="de-brand",
                    children=[
                        html.Span(className="de-brand-grid"),
                        html.Div(
                            [
                                html.Div("LiDAR Platform", className="de-brand-title"),
                                html.Div(
                                    "Preprocessing · Airflow · Medallion Pipeline",
                                    className="de-brand-subtitle",
                                ),
                            ],
                            className="de-brand-copy",
                        ),
                    ],
                ),
                html.Div(
                    [
                        dcc.Link("Home", href="/", className="de-nav-link"),
                        dcc.Link("Data Explorer", href="/data-explorer", className="de-nav-link"),
                        dcc.Link("Preprocessing", href="/preprocessing", className="de-nav-link de-nav-link-active"),
                        dcc.Link("Training", href="/training", className="de-nav-link"),
                        dcc.Link("Postprocessing", href="/postprocessing", className="de-nav-link"),
                        dcc.Link("Control", href="/control-panel", className="de-nav-link"),
                    ],
                    className="de-nav",
                ),
                html.Div(
                    [html.Span(className="de-live-dot"), "Pipeline Active"],
                    className="de-live-pill",
                ),
            ],
        ),

        # ── Hero ──────────────────────────────────────────────────────────
        html.Section(
            className="de-hero",
            children=[
                html.Canvas(
                    id="preprocessing-cv",
                    **{"aria-label": "Preprocessing pipeline particle field"},
                ),
                html.Div(
                    [
                        html.Div(
                            "Bronze → Silver → Gold · MLS Medallion Pipeline",
                            className="de-eyebrow",
                        ),
                        html.H1(
                            ["Preprocessing", html.Br(), html.Em("Workspace")],
                            className="de-hero-title",
                        ),
                        html.P(
                            "Route registered MLS datasets through the v9 medallion workflow. "
                            "Configure parameters, trigger remote Airflow runs, verify Silver outputs in B2, "
                            "and unlock Gold model-ready blocks for training.",
                            className="de-hero-copy",
                        ),
                        html.Div(
                            [
                                html.A(
                                    "Open Workspace →",
                                    href="#preproc-workspace",
                                    className="de-primary-cta",
                                ),
                                dcc.Link(
                                    "Data Explorer",
                                    href="/data-explorer",
                                    className="de-secondary-cta",
                                ),
                            ],
                            className="de-hero-actions",
                        ),
                    ],
                    className="de-hero-content",
                ),
            ],
        ),

        # ── Live stats strip ──────────────────────────────────────────────
        html.Div(id="preproc-live-stats", className="de-live-stats"),

        # ── Workspace ─────────────────────────────────────────────────────
        html.Section(
            id="preproc-workspace",
            className="data-explorer-workspace",
            children=[
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("Preprocessing", className="data-explorer-eyebrow"),
                                html.H2("Medallion pipeline workspace"),
                                html.P(
                                    "Select a registered dataset from the sidebar, then move through "
                                    "Dataset configuration, Parameters, Execution, "
                                    "Silver verification, and Gold output readiness.",
                                    className="mb-0",
                                ),
                            ],
                            className="data-explorer-title",
                        ),
                    ],
                    className="data-explorer-head",
                ),
                html.Div(
                    [
                        _preproc_sidebar(),
                        html.Section(
                            dbc.Tabs(
                                [
                                    dbc.Tab(label="Dataset", tab_id="dataset", children=_tab_dataset()),
                                    dbc.Tab(label="Parameters", tab_id="parameters", children=_tab_parameters()),
                                    dbc.Tab(label="Execute", tab_id="execute", children=_tab_execute()),
                                    dbc.Tab(label="Silver", tab_id="silver", children=_tab_silver()),
                                    dbc.Tab(label="Gold", tab_id="gold", children=_tab_gold()),
                                ],
                                id="preproc-tabs",
                                active_tab="dataset",
                                className="data-explorer-tabs",
                            ),
                            className="data-explorer-main",
                        ),
                    ],
                    className="data-explorer-grid",
                ),
            ],
        ),
    ],
)


@callback(
    Output("preproc-live-stats", "children"),
    Input("preproc-dataset-refresh", "n_intervals"),
)
def refresh_preproc_stats(_):
    try:
        n_datasets = len(list_registered_datasets())
    except Exception:
        n_datasets = 0
    run_dir = "data/airflow_preprocessing_requests"
    try:
        n_runs = (
            len([f for f in os.listdir(run_dir) if f.endswith(".json") and "_dataset_config" not in f])
            if os.path.isdir(run_dir)
            else 0
        )
    except Exception:
        n_runs = 0
    airflow_ok = bool(AIRFLOW_BASE_URL)
    return [
        _stat_tile("Registered", str(n_datasets)),
        _stat_tile("Runs Saved", str(n_runs)),
        _stat_tile("Active DAG", "preprocessing"),
        _stat_tile("Airflow", "connected" if airflow_ok else "local"),
    ]


@callback(
    Output("preproc-dataset-dropdown", "options"),
    Input("preproc-dataset-refresh", "n_intervals"),
)
def refresh_dataset_options(_):
    return _dataset_options()


@callback(
    Output("preproc-dataset-id", "value"),
    Output("preproc-dataset-name", "value"),
    Output("preproc-label-field", "value"),
    Output("preproc-building-labels", "value"),
    Output("preproc-non-building-labels", "value"),
    Output("preproc-ignore-labels", "value"),
    Input("preproc-dataset-dropdown", "value"),
    prevent_initial_call=True,
)
def apply_selected_dataset(dataset_id):
    if not dataset_id:
        raise PreventUpdate

    row = _dataset_registry_row(dataset_id)
    dataset_name = row.get("dataset_name") or dataset_id
    config = build_dataset_config(dataset_id, dataset_name)
    return (
        dataset_id,
        dataset_name,
        config["label_field"],
        _label_list_text(config["building_labels"]),
        _label_list_text(config["non_building_labels"]),
        _label_list_text(config["ignore_labels"]),
    )


@callback(
    Output("preproc-dataset-id", "value", allow_duplicate=True),
    Input("url", "search"),
    prevent_initial_call=True,
)
def apply_query_dataset(search):
    if not search:
        raise PreventUpdate
    dataset_id = (parse_qs(search.lstrip("?")).get("dataset_id") or [None])[0]
    if not dataset_id:
        raise PreventUpdate
    return dataset_id


@callback(
    Output("preproc-b2-output-prefix", "value"),
    Input("preproc-dataset-id", "value"),
    Input("preproc-version", "value"),
    State("preproc-b2-output-prefix", "value"),
)
def sync_silver_prefix(dataset_id, prep_version, current_prefix):
    standard = _standard_silver_prefix(dataset_id, prep_version)
    current = _normalize_prefix(current_prefix)
    if not current or current.startswith("silver_preprocessed_data/") or "<dataset_id>" in current:
        return standard
    return dash.no_update


@callback(
    Output("preproc-dataset-summary", "children"),
    Input("preproc-dataset-id", "value"),
)
def update_dataset_summary(dataset_id):
    return _dataset_summary_card(dataset_id)


@callback(
    Output("preproc-compute-health-grid", "children"),
    Input("preproc-compute-health-refresh", "n_intervals"),
)
def refresh_compute_health(_):
    statuses = check_compute_nodes()
    return [ops_service_health_card(item) for item in statuses]


@callback(
    Output("preproc-open-mlflow-link", "href"),
    Output("preproc-open-mlflow-button", "disabled"),
    Input("preproc-mlflow-tracking-uri", "value"),
)
def update_preprocessing_mlflow_link(uri):
    href = mlflow_browser_url(uri)
    return href, href == "#"


@callback(
    Output("preproc-segment-validation", "children"),
    Output("preproc-segment-validation", "className"),
    Input("preproc-mode", "value"),
    Input("preproc-num-segments", "value"),
    Input("preproc-train-segments", "value"),
    Input("preproc-val-segments", "value"),
    Input("preproc-test-segments", "value"),
)
def update_segment_validation_badge(mode, num_segments, train_segments, val_segments, test_segments):
    if mode == "inference":
        return "Inference mode: split validation relaxed", "ops-validation-badge ops-validation-badge-info"

    blocker = _segment_split_blocker(mode, num_segments, train_segments, val_segments, test_segments)
    total = int(num_segments or DEFAULT_NUM_SEGMENTS)
    train = int(train_segments or DEFAULT_TRAIN_SEGMENTS)
    val = int(val_segments or DEFAULT_VAL_SEGMENTS)
    test = int(test_segments or DEFAULT_TEST_SEGMENTS)
    if blocker:
        return blocker, "ops-validation-badge ops-validation-badge-danger"
    return f"Validated: {train + val + test}/{total} segments assigned", "ops-validation-badge ops-validation-badge-ok"



@callback(
    Output("preproc-storage-table", "data"),
    Output("preproc-parameter-summary-table", "data"),
    Output("preproc-conf-store", "data"),
    *FORM_INPUTS,
    prevent_initial_call=True,
)
def update_preprocessing_preview(*values):
    try:
        conf = _build_conf_from_values(*values)
    except Exception as exc:
        return [], [{"parameter": "configuration_error", "value": str(exc)}], None

    return _storage_rows(conf["storage"]), _parameter_summary(conf), conf


@callback(
    Output("preproc-action-message", "children"),
    Output("preproc-dag-run-store", "data"),
    Output("preproc-airflow-status-refresh", "disabled"),
    Input("preproc-trigger-airflow-button", "n_clicks"),
    Input("preproc-quick-start-button", "n_clicks"),
    *_form_state_specs(),
    prevent_initial_call=True,
)
def handle_preprocessing_action(trigger_clicks, quick_clicks, *values):
    dataset_id = values[0]
    if not dataset_id:
        return dbc.Alert("Select or enter a dataset ID before creating an Airflow run.", color="warning"), dash.no_update, True

    button_id = dash.ctx.triggered_id
    if button_id not in {"preproc-trigger-airflow-button", "preproc-quick-start-button"}:
        raise PreventUpdate

    split_blocker = _segment_split_blocker(values[6], values[24], values[25], values[26], values[27])
    if split_blocker:
        return dbc.Alert(split_blocker, color="warning"), dash.no_update, True

    try:
        conf = _build_conf_from_values(*values)
        payload, payload_path = persist_airflow_request(conf)

        # Only dataset_id, mode, and (if explicitly pinned) prep_version go to Airflow.
        # The workstation owns all other defaults and auto-increments prep_version.
        pinned_prep_version = (values[7] or "").strip() or None
        minimal_conf = build_minimal_trigger_conf(
            dataset_id=dataset_id,
            mode=values[6] or "train",
            prep_version=pinned_prep_version,
            run_id=payload["dag_run_id"],
        )

        if not AIRFLOW_BASE_URL:
            return (
                dbc.Alert(
                    [
                        html.Strong("Payload saved, Airflow API not configured. "),
                        "Set AIRFLOW_BASE_URL or AIRFLOW_API_BASE_URL plus AIRFLOW_USERNAME and AIRFLOW_PASSWORD.",
                        html.Br(),
                        html.Code(payload_path),
                    ],
                    color="warning",
                ),
                {
                    "dag_id": conf["dag_id"],
                    "dag_run_id": payload["dag_run_id"],
                    "state": "not_configured",
                    "b2_silver_prefix": _normalize_prefix(conf["script_args"].get("b2_silver_prefix")),
                    "prep_version": conf.get("prep_version", ""),
                },
                True,
            )

        result = trigger_airflow_preprocessing_dag(conf["dag_id"], minimal_conf)
        return (
            dbc.Alert([html.Strong("Airflow DAG triggered. "), "DAG run id: ", html.Code(result["dag_run_id"])], color="success"),
            {
                "dag_id": result["dag_id"],
                "dag_run_id": result["dag_run_id"],
                "state": result.get("state", "queued"),
                # Carry the exact paths used at trigger time so verify callbacks
                # don't silently use stale UI state if the user changes fields mid-run.
                "b2_silver_prefix": _normalize_prefix(conf["script_args"].get("b2_silver_prefix")),
                "prep_version": conf.get("prep_version", ""),
            },
            False,
        )
    except Exception as exc:
        return dbc.Alert(f"Preprocessing trigger failed: {exc}", color="danger"), dash.no_update, True


@callback(
    Output("preproc-airflow-status-store", "data"),
    Output("preproc-airflow-status-refresh", "disabled", allow_duplicate=True),
    Input("preproc-airflow-status-refresh", "n_intervals"),
    Input("preproc-dag-run-store", "data"),
    prevent_initial_call=True,
)
def poll_airflow_status(_ticks, dag_run):
    if not dag_run or not dag_run.get("dag_run_id") or dag_run.get("state") == "not_configured":
        status = dag_run or {}
        return status, True

    dag_id = dag_run.get("dag_id") or AIRFLOW_PREPROCESSING_DAG_ID or AIRFLOW_DAG_ID
    status = build_airflow_status_snapshot(dag_id, dag_run["dag_run_id"])
    terminal = status.get("state") in {"success", "failed"}
    return status, terminal


@callback(
    Output("preproc-stepper", "children"),
    Input("preproc-dataset-id", "value"),
    Input("preproc-airflow-status-store", "data"),
    Input("preproc-dag-run-store", "data"),
    Input("preproc-silver-status-store", "data"),
)
def update_stepper(dataset_id, airflow_status, dag_run, silver_status):
    return _preproc_stepper(dataset_id, airflow_status, dag_run, silver_status)


@callback(
    Output("preproc-trigger-airflow-button", "disabled"),
    Output("preproc-trigger-airflow-button", "children"),
    Input("preproc-airflow-status-store", "data"),
    Input("preproc-dataset-id", "value"),
    Input("preproc-segment-validation", "className"),
    Input("preproc-b2-output-prefix", "value"),
)
def update_trigger_button(status, dataset_id, seg_class, b2_prefix):
    if not dataset_id:
        return True, "Select Dataset First"
    state = (status or {}).get("state")
    if state in {"queued", "running", "scheduled"}:
        return True, "Preprocessing Running"
    if "ops-validation-badge-danger" in (seg_class or ""):
        return True, "Fix Segment Split"
    if not _normalize_prefix(b2_prefix):
        return True, "Set B2 Prefix"
    return False, "Start Preprocessing"


@callback(
    Output("preproc-quick-start-button", "disabled"),
    Output("preproc-quick-start-button", "children"),
    Output("preproc-quick-start-hint", "children"),
    Input("preproc-dataset-id", "value"),
    Input("preproc-airflow-status-store", "data"),
)
def update_quick_start_button(dataset_id, status):
    if not dataset_id:
        return True, "Start Preprocessing", "Select a dataset to enable quick-start with default parameters"
    state = (status or {}).get("state")
    if state in {"queued", "running", "scheduled"}:
        return True, "Preprocessing Running", f"DAG is {state} — wait for it to finish before starting another run"
    return False, "Start Preprocessing", f"Trigger lidar_preprocessing_pipeline for dataset: {dataset_id}"


@callback(
    Output("preproc-silver-status-store", "data"),
    Output("preproc-silver-verification-panel", "children"),
    Input("preproc-verify-silver-button", "n_clicks"),
    Input("preproc-airflow-status-store", "data"),
    State("preproc-dataset-id", "value"),
    State("preproc-version", "value"),
    State("preproc-b2-output-prefix", "value"),
    State("preproc-dag-run-store", "data"),
    prevent_initial_call=True,
)
def verify_silver_outputs(manual_clicks, airflow_status, dataset_id, prep_version, b2_prefix, dag_run):
    triggered = dash.ctx.triggered_id
    if not dataset_id:
        raise PreventUpdate
    if triggered == "preproc-airflow-status-store" and (airflow_status or {}).get("state") != "success":
        raise PreventUpdate

    # On auto-verify (DAG just succeeded), use the exact prefix stored at trigger
    # time rather than live UI state — guards against mid-run UI edits.
    if triggered == "preproc-airflow-status-store" and dag_run:
        stored_prefix = (dag_run or {}).get("b2_silver_prefix", "")
        prefix = stored_prefix or _normalize_prefix(b2_prefix) or _standard_silver_prefix(dataset_id, prep_version)
    else:
        prefix = _normalize_prefix(b2_prefix) or _standard_silver_prefix(dataset_id, prep_version)

    verification = verify_b2_silver_outputs(dataset_id, prefix)
    return verification, _verification_panel(verification)


@callback(
    Output("preproc-silver-layer-container", "children"),
    Output("preproc-gold-layer-container", "children"),
    Input("preproc-silver-status-store", "data"),
    State("preproc-dataset-id", "value"),
    State("preproc-version", "value"),
    State("preproc-b2-output-prefix", "value"),
    State("preproc-dag-run-store", "data"),
)
def render_output_layers(silver_status, dataset_id, prep_version, b2_prefix, dag_run):
    if not silver_status:
        return (
            empty_state("Silver Layer analytics", "Verify Silver outputs to load real metadata and charts."),
            empty_state("Gold output contract", "Gold preview appears after the Silver readiness check has run."),
        )

    # Prefer paths stored at trigger time over current UI state.
    stored_prefix = (dag_run or {}).get("b2_silver_prefix", "")
    stored_prep_version = (dag_run or {}).get("prep_version", "")
    prefix = stored_prefix or _normalize_prefix(b2_prefix) or _standard_silver_prefix(dataset_id, prep_version)
    effective_prep_version = stored_prep_version or prep_version or "prep_v001"

    silver_payload = load_local_or_b2_silver_metadata(dataset_id, prefix)
    readiness = compute_silver_readiness(silver_status, silver_payload)
    silver = build_silver_layer_section(dataset_id, effective_prep_version, prefix, silver_status, silver_payload)
    gold_payload = load_gold_metadata_if_available(dataset_id, effective_prep_version)
    gold = build_gold_layer_section(dataset_id, effective_prep_version, readiness, gold_payload=gold_payload)
    return silver, gold
