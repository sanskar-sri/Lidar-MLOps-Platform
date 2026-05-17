import json
from datetime import datetime, timezone

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from components.gold_layer_section import build_gold_layer_section
from components.platform_theme import (
    empty_state,
    ops_topbar,
    platform_hero,
    section_head,
    small_status,
    step_item,
)
from components.silver_layer_section import build_silver_layer_section
from services.compute_nodes_service import (
    COMPUTE_HEALTH_POLL_MS,
    build_compute_target_options,
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
    build_remote_command,
    build_storage_contract,
    persist_airflow_request,
)


dash.register_page(__name__, path="/preprocessing", name="Preprocessing")


# Shared operations UI helpers live in components.platform_theme. Other pages can
# import COLORS, CARD_STYLE, HERO_STYLE, ops_topbar, status_badge, and platform_hero
# from that module when they are ready to remove their local duplicates.


POLL_MS = 5000
DEFAULT_PIPELINE_VERSION = "v9_airflow_compat"


def _table_style():
    return {
        "style_table": {
            "overflowX": "auto",
            "width": "100%",
            "border": "1px solid rgba(125, 180, 255, 0.18)",
            "borderRadius": "8px",
        },
        "style_cell": {
            "textAlign": "left",
            "padding": "9px",
            "fontFamily": "Arial",
            "fontSize": "12px",
            "whiteSpace": "normal",
            "height": "auto",
            "backgroundColor": "#0b111b",
            "color": "#edf2f7",
            "border": "1px solid rgba(125, 180, 255, 0.14)",
        },
        "style_header": {
            "fontWeight": "bold",
            "backgroundColor": "#111827",
            "color": "#edf2f7",
            "border": "1px solid rgba(125, 180, 255, 0.18)",
        },
        "style_data_conditional": [
            {"if": {"row_index": "odd"}, "backgroundColor": "#0f1724"},
        ],
    }


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


def _compute_node_card(item):
    tone = item.get("tone", "warning")
    roles = ", ".join(item.get("roles") or [])
    metrics = item.get("metrics") or []
    return html.Div(
        [
            html.Div(
                [
                    html.Span(className=f"prep-node-dot prep-node-dot-{tone}"),
                    html.Div(item.get("name", ""), className="prep-node-name"),
                ],
                className="prep-node-head",
            ),
            html.Div(item.get("state", ""), className=f"prep-node-state prep-node-state-{tone}"),
            html.Div(item.get("detail", ""), className="prep-node-detail"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(metric.get("label", ""), className="prep-node-metric-label"),
                            html.Div(metric.get("value", ""), className="prep-node-metric-value"),
                            html.Div(metric.get("detail", ""), className="prep-node-metric-detail"),
                        ],
                        className="prep-node-metric",
                    )
                    for metric in metrics
                ],
                className="prep-node-metrics",
            )
            if metrics
            else None,
            html.Div(
                [
                    html.Span(f"Queue: {item.get('airflow_queue') or item.get('id')}", className="prep-node-chip"),
                    html.Span(roles or "roles pending", className="prep-node-chip"),
                ],
                className="prep-node-chips",
            ),
        ],
        className=f"prep-node-card prep-node-card-{tone}",
    )


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

    return html.Div(
        [
            html.Div(
                [
                    small_status("DAG state", state),
                    html.Div(f"{progress}%", className="airflow-progress-label"),
                ],
                className="airflow-status-head",
            ),
            dbc.Progress(value=min(max(float(progress or 0), 0), 100), striped=state in {"queued", "running"}, animated=state == "running", className="airflow-progress"),
            dash_table.DataTable(
                columns=[{"name": "Field", "id": "field"}, {"name": "Value", "id": "value"}],
                data=rows,
                page_size=8,
                **_table_style(),
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
                **_table_style(),
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


def _compute_target_blocker(execution_target):
    statuses = check_compute_nodes()
    if execution_target == "any_gpu_worker":
        if any(item.get("tone") == "connected" for item in statuses):
            return None
        return "No compute node is online. Start the health agent on the Windows workstation before triggering Airflow."

    for item in statuses:
        if item.get("id") == execution_target:
            if item.get("tone") == "connected":
                return None
            return f"{item.get('name')} is {item.get('state')}: {item.get('detail')}"

    return f"Execution target {execution_target} is not configured in this dashboard."


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


layout = html.Div(
    className="prep-page preprocessing-page ops-page",
    children=[
        dcc.Store(id="preproc-conf-store"),
        dcc.Store(id="preproc-dag-run-store"),
        dcc.Store(id="preproc-airflow-status-store"),
        dcc.Store(id="preproc-silver-status-store"),
        dcc.Interval(id="preproc-dataset-refresh", interval=60000, n_intervals=0),
        dcc.Interval(id="preproc-compute-health-refresh", interval=COMPUTE_HEALTH_POLL_MS, n_intervals=0),
        dcc.Interval(id="preproc-airflow-status-refresh", interval=POLL_MS, n_intervals=0, disabled=True),
        ops_topbar("Preprocessing", "Bronze to Silver to Gold preprocessing", "Pipeline Active"),
        platform_hero(
            "preprocessing-cv",
            "Remote Airflow Orchestration",
            "Preprocessing",
            "Workbench",
            "Route one registered MLS dataset through the v9 medallion workflow, monitor Airflow live, verify Silver outputs in B2, and prepare Gold blocks for training.",
            [
                ("Flow", "Bronze -> Silver -> Gold"),
                ("DAG", AIRFLOW_PREPROCESSING_DAG_ID or AIRFLOW_DAG_ID),
                ("Controller", "Dash only"),
            ],
            "ops-hero-preprocessing",
        ),
        html.Main(
            [
                html.Div(
                    [
                        step_item("01", "Dataset", "Select source, labels, and routing", "blue"),
                        step_item("02", "Parameters", "Configure voxel, features, splits", "green"),
                        step_item("03", "Execution", "Trigger and monitor Airflow", "purple"),
                        step_item("04", "Outputs", "Verify Silver and preview Gold", "amber"),
                    ],
                    className="ops-stepper",
                ),
                html.Div(
                    [
                        html.Section(
                            [
                                section_head("Step 01", "Dataset and Routing", "Choose the registry record, inspect source metadata, and route the job to the Windows Airflow workstation."),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dbc.Label("Registered Dataset"),
                                                dcc.Dropdown(id="preproc-dataset-dropdown", options=_dataset_options(), placeholder="Select a dataset from the registry", clearable=True),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                        html.Div([dbc.Label("Dataset ID"), dbc.Input(id="preproc-dataset-id", placeholder="Example: toronto-1", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Dataset Name"), dbc.Input(id="preproc-dataset-name", placeholder="Example: Toronto MLS", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Label Field"), dbc.Input(id="preproc-label-field", value="class", placeholder="class or scalar_Label", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Building Labels"), dbc.Input(id="preproc-building-labels", value="2", placeholder="Example: 2 or 4", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Non-Building Labels"), dbc.Input(id="preproc-non-building-labels", value="1, 3, 4, 5, 6, 7, 8, 9", placeholder="Comma-separated raw class IDs", persistence=True, persistence_type="session")], className="ops-field ops-field-wide"),
                                        html.Div([dbc.Label("Ignore Labels"), dbc.Input(id="preproc-ignore-labels", value="0", placeholder="Comma-separated raw class IDs", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div(
                                            [
                                                dbc.Label("Mode"),
                                                dcc.Dropdown(
                                                    id="preproc-mode",
                                                    options=[
                                                        {"label": "Training - labels required", "value": "train"},
                                                        {"label": "Inference - labels optional", "value": "inference"},
                                                    ],
                                                    value="train",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Execution Target"),
                                                dcc.Dropdown(id="preproc-execution-target", options=build_compute_target_options(), value="any_gpu_worker", clearable=False, persistence=True, persistence_type="session"),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                    ],
                                    className="ops-field-grid",
                                ),
                                html.Div(
                                    [
                                        html.Div([html.H3("Dataset Identity"), html.Div(id="preproc-dataset-summary")], className="ops-review-card"),
                                        html.Div(
                                            [
                                                html.H3("Compute Health"),
                                                html.Div(id="preproc-compute-health-grid", className="prep-node-grid ops-node-grid"),
                                            ],
                                            className="ops-review-card",
                                        ),
                                    ],
                                    className="ops-review-grid",
                                ),
                            ],
                            className="ops-panel ops-step-panel ops-panel-primary",
                        ),
                        html.Section(
                            [
                                section_head("Step 02", "Parameter Configuration", "Every value below is reflected in the JSON conf sent to Airflow; no hidden sample settings are injected by Dash."),
                                html.Div(
                                    [
                                        html.Div([dbc.Label("Preprocessing Version"), dbc.Input(id="preproc-version", value="prep_v001", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Pipeline Version"), dbc.Input(id="preproc-pipeline-version", value=DEFAULT_PIPELINE_VERSION, persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div(
                                            [
                                                dbc.Label("Output Tier"),
                                                dcc.Dropdown(
                                                    id="preproc-output-tier",
                                                    options=[
                                                        {"label": "Silver and Gold", "value": "silver_and_gold"},
                                                        {"label": "Silver only", "value": "silver_only"},
                                                    ],
                                                    value="silver_and_gold",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Output Mode"),
                                                dcc.Dropdown(
                                                    id="preproc-output-mode",
                                                    options=[
                                                        {"label": "All model formats", "value": "all"},
                                                        {"label": "Traditional blocks only", "value": "traditional"},
                                                        {"label": "PTv3 / Pointcept only", "value": "ptv3"},
                                                    ],
                                                    value="all",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div([dbc.Label("B2 Silver Output Prefix"), dbc.Input(id="preproc-b2-output-prefix", placeholder="silver_preprocessed_data/<dataset>/<prep_version>", persistence=True, persistence_type="session")], className="ops-field ops-field-wide"),
                                        html.Div(
                                            [
                                                dbc.Label("Input Features"),
                                                dbc.Checklist(
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
                                                ),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Label Mapping Mode"),
                                                dcc.Dropdown(
                                                    id="preproc-label-mode",
                                                    options=[
                                                        {"label": "Registry or raw numeric IDs", "value": "registry_or_raw_ids"},
                                                        {"label": "Raw numeric IDs only", "value": "raw_numeric_ids"},
                                                        {"label": "External XML/JSON map required", "value": "mapping_file_required"},
                                                    ],
                                                    value="registry_or_raw_ids",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Coordinate Normalization"),
                                                dcc.Dropdown(
                                                    id="preproc-coordinate-normalization",
                                                    options=[
                                                        {"label": "Subtract coordinate offset", "value": "offset_subtract"},
                                                        {"label": "Keep original coordinates", "value": "none"},
                                                    ],
                                                    value="offset_subtract",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Split Strategy"),
                                                dcc.Dropdown(
                                                    id="preproc-split-strategy",
                                                    options=[
                                                        {"label": "Spatial segment split", "value": "segment_spatial"},
                                                        {"label": "Tile-level split", "value": "tile_split"},
                                                        {"label": "Existing split metadata", "value": "existing_split_metadata"},
                                                    ],
                                                    value="segment_spatial",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                    ],
                                    className="ops-field-grid",
                                ),
                                html.Div(
                                    [
                                        html.Div([dbc.Label("Voxel Size (m)"), dbc.Input(id="preproc-voxel-size", type="number", value=0.02, step=0.01, min=0)], className="ops-field"),
                                        html.Div(
                                            [
                                                dbc.Label("Voxel Keep Strategy"),
                                                dcc.Dropdown(
                                                    id="preproc-voxel-strategy",
                                                    options=[
                                                        {"label": "Representative point", "value": "representative"},
                                                        {"label": "Centroid mean", "value": "centroid"},
                                                    ],
                                                    value="representative",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div([dbc.Label("Block Size (m)"), dbc.Input(id="preproc-block-size", type="number", value=2.0, step=0.5)], className="ops-field"),
                                        html.Div([dbc.Label("Points / Block"), dbc.Input(id="preproc-n-points", type="number", value=8192, step=1024)], className="ops-field"),
                                        html.Div([dbc.Label("Max Train Blocks"), dbc.Input(id="preproc-max-blocks", type="number", value=8000, step=500)], className="ops-field"),
                                        html.Div([dbc.Label("Val/Test Stride"), dbc.Input(id="preproc-stride", type="number", value=1.5, step=0.25)], className="ops-field"),
                                        html.Div([dbc.Label("Split Gap (m)"), dbc.Input(id="preproc-split-gap", type="number", value=2.0, step=0.5)], className="ops-field"),
                                        html.Div([dbc.Label("Min Building Ratio"), dbc.Input(id="preproc-min-bldg", type="number", value=0.01, step=0.005)], className="ops-field"),
                                    ],
                                    className="ops-field-grid ops-field-grid-four",
                                ),
                                html.Div(
                                    [
                                        html.Div([dbc.Label("Total Segments"), dbc.Input(id="preproc-num-segments", type="number", value=DEFAULT_NUM_SEGMENTS, min=1, step=1)], className="ops-field"),
                                        html.Div([dbc.Label("Train Segments"), dbc.Input(id="preproc-train-segments", type="number", value=DEFAULT_TRAIN_SEGMENTS, min=0, step=1)], className="ops-field"),
                                        html.Div([dbc.Label("Val Segments"), dbc.Input(id="preproc-val-segments", type="number", value=DEFAULT_VAL_SEGMENTS, min=0, step=1)], className="ops-field"),
                                        html.Div([dbc.Label("Test Segments"), dbc.Input(id="preproc-test-segments", type="number", value=DEFAULT_TEST_SEGMENTS, min=0, step=1)], className="ops-field"),
                                        html.Div(id="preproc-segment-validation", className="ops-validation-badge"),
                                    ],
                                    className="ops-field-grid ops-segment-grid",
                                ),
                                html.Div(
                                    [
                                        html.Div([dbc.Label("RandLA Overlap"), dbc.Input(id="preproc-randla-overlap", type="number", value=0.0, step=0.05, min=0, max=0.95)], className="ops-field"),
                                        html.Div([dbc.Label("PTv3 Scene Length"), dbc.Input(id="preproc-ptv3-length", type="number", value=50.0, step=5)], className="ops-field"),
                                        html.Div([dbc.Label("Workers"), dbc.Input(id="preproc-workers", type="number", min=1, max=64, value=24)], className="ops-field"),
                                        html.Div(
                                            [
                                                dbc.Label("Generated Features"),
                                                dbc.Checklist(
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
                                                ),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                    ],
                                    className="ops-field-grid",
                                ),
                            ],
                            className="ops-panel ops-step-panel",
                        ),
                        html.Section(
                            [
                                section_head("Step 03", "MLOps and Airflow Execution", "Set tracking destinations, review the actual DAG conf, and keep live status polling outside the layout."),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dbc.Label("MLflow Tracking URI"),
                                                dbc.Input(id="preproc-mlflow-tracking-uri", value=DEFAULT_MLFLOW_TRACKING_URI, placeholder="./mlruns or http://mlflow-host:5000", persistence=True, persistence_type="session"),
                                                html.A(dbc.Button("Open MLflow", id="preproc-open-mlflow-button", color="info", outline=True, size="sm", className="mt-2 w-100"), id="preproc-open-mlflow-link", href=mlflow_browser_url(DEFAULT_MLFLOW_TRACKING_URI), target="_blank", rel="noopener noreferrer"),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div([dbc.Label("MLflow Experiment"), dbc.Input(id="preproc-mlflow-experiment", value="mls-preprocessing", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("DVC Remote"), dbc.Input(id="preproc-dvc-remote", value="b2remote", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("MLflow Run Name"), dbc.Input(id="preproc-mlflow-run-name", placeholder="Optional; defaults to dataset + run id", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div(
                                            [
                                                dbc.Label("Tracking Options"),
                                                dbc.Checklist(
                                                    id="preproc-mlops-flags",
                                                    options=[
                                                        {"label": "Log small MLflow artifacts", "value": "log_artifacts"},
                                                        {"label": "Disable MLflow", "value": "disable_mlflow"},
                                                    ],
                                                    value=["log_artifacts"],
                                                    inline=True,
                                                    switch=True,
                                                ),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                    ],
                                    className="ops-field-grid",
                                ),
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
                                                    **_table_style(),
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
                                                    **_table_style(),
                                                ),
                                            ],
                                            className="ops-review-card",
                                        ),
                                    ],
                                    className="ops-review-grid",
                                ),
                                html.Div(
                                    [
                                        html.Div([html.H3("Remote Command"), html.Pre(id="preproc-command-preview", className="lineage-box ops-code-box")], className="ops-review-card"),
                                        html.Div([html.H3("Airflow DAG Run Conf"), html.Pre(id="preproc-payload-preview", className="lineage-box ops-code-box")], className="ops-review-card"),
                                    ],
                                    className="ops-review-grid",
                                ),
                                html.Div(
                                    [
                                        html.Div("Start Preprocessing sends this JSON to the remote Windows Airflow DAG. Dash only stores state and polls status.", className="ops-execution-note"),
                                        dbc.ButtonGroup(
                                            [
                                                dbc.Button("Save Trigger Payload", id="preproc-save-payload-button", color="secondary", outline=True),
                                                dbc.Button("Start Preprocessing", id="preproc-trigger-airflow-button", color="success"),
                                            ],
                                            className="ops-action-group",
                                        ),
                                    ],
                                    className="ops-trigger-row",
                                ),
                                html.Div(id="preproc-action-message", className="mt-3"),
                                html.Div(
                                    [
                                        html.H3("Live Airflow Status"),
                                        html.Div(id="preproc-airflow-status-panel"),
                                    ],
                                    className="ops-review-card airflow-live-card",
                                ),
                            ],
                            className="ops-panel ops-step-panel ops-panel-review",
                        ),
                        html.Section(
                            [
                                section_head("Step 04", "B2 Verification and Layer Readiness", "Verify real Silver output files before analytics, then preview Gold as a planned contract unless B2 already contains the generated outputs."),
                                html.Div(
                                    [
                                        dbc.Button("Verify Silver Outputs", id="preproc-verify-silver-button", color="info", outline=True),
                                        html.Div(id="preproc-silver-verification-panel", className="mt-3"),
                                    ],
                                    className="ops-review-card",
                                ),
                                html.Div(id="preproc-silver-layer-container"),
                                html.Div(id="preproc-gold-layer-container"),
                            ],
                            className="ops-stack",
                        ),
                    ],
                    className="ops-stack",
                ),
            ],
            className="ops-workspace",
        ),
    ],
)


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
    Output("preproc-execution-target", "options"),
    Input("preproc-compute-health-refresh", "n_intervals"),
)
def refresh_compute_health(_):
    statuses = check_compute_nodes()
    return [_compute_node_card(item) for item in statuses], build_compute_target_options()


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
    Output("preproc-command-preview", "children"),
    Output("preproc-payload-preview", "children"),
    Output("preproc-conf-store", "data"),
    *FORM_INPUTS,
)
def update_preprocessing_preview(*values):
    try:
        conf = _build_conf_from_values(*values)
    except Exception as exc:
        return [], [{"parameter": "configuration_error", "value": str(exc)}], "", "", None

    command = " \\\n  ".join(build_remote_command(conf))
    payload = json.dumps(conf, indent=2)
    return _storage_rows(conf["storage"]), _parameter_summary(conf), command, payload, conf


@callback(
    Output("preproc-action-message", "children"),
    Output("preproc-dag-run-store", "data"),
    Output("preproc-airflow-status-refresh", "disabled"),
    Input("preproc-save-payload-button", "n_clicks"),
    Input("preproc-trigger-airflow-button", "n_clicks"),
    *_form_state_specs(),
    prevent_initial_call=True,
)
def handle_preprocessing_action(save_clicks, trigger_clicks, *values):
    dataset_id = values[0]
    if not dataset_id:
        return dbc.Alert("Select or enter a dataset ID before creating an Airflow run.", color="warning"), dash.no_update, True

    button_id = dash.ctx.triggered_id
    if button_id not in {"preproc-save-payload-button", "preproc-trigger-airflow-button"}:
        raise PreventUpdate

    split_blocker = _segment_split_blocker(values[6], values[24], values[25], values[26], values[27])
    if split_blocker:
        return dbc.Alert(split_blocker, color="warning"), dash.no_update, True

    try:
        conf = _build_conf_from_values(*values)
        if button_id == "preproc-save-payload-button":
            payload, payload_path = persist_airflow_request(conf)
            return (
                dbc.Alert([html.Strong("Trigger payload saved. "), html.Code(payload_path), html.Br(), "DAG run id: ", html.Code(payload["dag_run_id"])], color="success"),
                dash.no_update,
                True,
            )

        health_blocker = _compute_target_blocker(values[8])
        if health_blocker:
            return (
                dbc.Alert([html.Strong("Compute target is not ready. "), health_blocker], color="warning"),
                dash.no_update,
                True,
            )

        payload, payload_path = persist_airflow_request(conf)
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
                {"dag_id": conf["dag_id"], "dag_run_id": payload["dag_run_id"], "state": "not_configured"},
                True,
            )

        result = trigger_airflow_preprocessing_dag(conf["dag_id"], conf)
        return (
            dbc.Alert([html.Strong("Airflow DAG triggered. "), "DAG run id: ", html.Code(result["dag_run_id"])], color="success"),
            {"dag_id": result["dag_id"], "dag_run_id": result["dag_run_id"], "state": result.get("state", "queued")},
            False,
        )
    except Exception as exc:
        return dbc.Alert(f"Preprocessing trigger failed: {exc}", color="danger"), dash.no_update, True


@callback(
    Output("preproc-airflow-status-store", "data"),
    Output("preproc-airflow-status-panel", "children"),
    Output("preproc-airflow-status-refresh", "disabled", allow_duplicate=True),
    Input("preproc-airflow-status-refresh", "n_intervals"),
    Input("preproc-dag-run-store", "data"),
    prevent_initial_call=True,
)
def poll_airflow_status(_ticks, dag_run):
    if not dag_run or not dag_run.get("dag_run_id") or dag_run.get("state") == "not_configured":
        status = dag_run or {}
        return status, _status_panel(status), True

    dag_id = dag_run.get("dag_id") or AIRFLOW_PREPROCESSING_DAG_ID or AIRFLOW_DAG_ID
    status = build_airflow_status_snapshot(dag_id, dag_run["dag_run_id"])
    terminal = status.get("state") in {"success", "failed"}
    return status, _status_panel(status), terminal


@callback(
    Output("preproc-trigger-airflow-button", "disabled"),
    Output("preproc-trigger-airflow-button", "children"),
    Input("preproc-airflow-status-store", "data"),
    Input("preproc-dataset-id", "value"),
)
def update_trigger_button(status, dataset_id):
    if not dataset_id:
        return True, "Select Dataset First"
    state = (status or {}).get("state")
    if state in {"queued", "running", "scheduled"}:
        return True, "Preprocessing Running"
    return False, "Start Preprocessing"


@callback(
    Output("preproc-silver-status-store", "data"),
    Output("preproc-silver-verification-panel", "children"),
    Input("preproc-verify-silver-button", "n_clicks"),
    Input("preproc-airflow-status-store", "data"),
    State("preproc-dataset-id", "value"),
    State("preproc-version", "value"),
    State("preproc-b2-output-prefix", "value"),
    prevent_initial_call=True,
)
def verify_silver_outputs(manual_clicks, airflow_status, dataset_id, prep_version, b2_prefix):
    triggered = dash.ctx.triggered_id
    if not dataset_id:
        raise PreventUpdate
    if triggered == "preproc-airflow-status-store" and (airflow_status or {}).get("state") != "success":
        raise PreventUpdate

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
)
def render_output_layers(silver_status, dataset_id, prep_version, b2_prefix):
    if not silver_status:
        return (
            empty_state("Silver Layer analytics", "Verify Silver outputs to load real metadata and charts."),
            empty_state("Gold output contract", "Gold preview appears after the Silver readiness check has run."),
        )

    prefix = _normalize_prefix(b2_prefix) or _standard_silver_prefix(dataset_id, prep_version)
    silver_payload = load_local_or_b2_silver_metadata(dataset_id, prefix)
    readiness = compute_silver_readiness(silver_status, silver_payload)
    silver = build_silver_layer_section(dataset_id, prep_version, prefix, silver_status, silver_payload)
    gold = build_gold_layer_section(dataset_id, prep_version, readiness)
    return silver, gold
