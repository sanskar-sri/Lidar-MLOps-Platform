import json

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html

from services.compute_nodes_service import (
    build_compute_target_options,
    check_compute_nodes,
    resolve_airflow_queue,
)
from services.metadata_service import list_registered_datasets
from services.preprocessing_service import (
    AIRFLOW_API_BASE_URL,
    AIRFLOW_DAG_ID,
    DEFAULT_NUM_SEGMENTS,
    DEFAULT_TEST_SEGMENTS,
    DEFAULT_TRAIN_SEGMENTS,
    DEFAULT_VAL_SEGMENTS,
    build_airflow_conf,
    build_dataset_config,
    build_remote_command,
    build_storage_contract,
    persist_airflow_request,
    trigger_airflow_dag,
)


dash.register_page(__name__, path="/preprocessing", name="Preprocessing")


def _table_style():
    return {
        "style_table": {
            "overflowX": "auto",
            "width": "100%",
            "border": "1px solid #303943",
            "borderRadius": "8px",
        },
        "style_cell": {
            "textAlign": "left",
            "padding": "9px",
            "fontFamily": "Arial",
            "fontSize": "13px",
            "whiteSpace": "normal",
            "height": "auto",
            "backgroundColor": "#15191d",
            "color": "#edf2f7",
            "border": "1px solid #303943",
        },
        "style_header": {
            "fontWeight": "bold",
            "backgroundColor": "#1b2127",
            "color": "#edf2f7",
            "border": "1px solid #303943",
        },
        "style_data_conditional": [
            {
                "if": {"row_index": "odd"},
                "backgroundColor": "#171d23",
            }
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


def _flow_step(title, body, tone):
    return html.Div(
        [
            html.Div(title, className="prep-step-title"),
            html.Div(body, className="prep-step-body"),
        ],
        className=f"prep-step prep-step-{tone}",
    )


def _metric(label, value, sub):
    return dbc.Col(
        html.Div(
            [
                html.Div(label, className="prep-metric-label"),
                html.Div(value, className="prep-metric-value"),
                html.Div(sub, className="prep-metric-sub"),
            ],
            className="prep-metric",
        ),
        xs=12,
        md=6,
        lg=3,
        className="mb-3",
    )


def _label_list_text(values):
    return ", ".join(str(value) for value in (values or []))


def _compute_node_card(item):
    tone = item.get("tone", "warning")
    roles = ", ".join(item.get("roles") or [])
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
                    html.Span(f"Queue: {item.get('airflow_queue') or item.get('id')}", className="prep-node-chip"),
                    html.Span(roles or "roles pending", className="prep-node-chip"),
                ],
                className="prep-node-chips",
            ),
        ],
        className=f"prep-node-card prep-node-card-{tone}",
    )


layout = html.Div(
    className="prep-page",
    children=[
        dcc.Interval(id="preproc-dataset-refresh", interval=60000, n_intervals=0),
        dcc.Interval(id="preproc-compute-health-refresh", interval=30000, n_intervals=0),

        html.Div(
            [
                html.Div("Airflow Remote Execution", className="prep-eyebrow"),
                html.H2("Preprocessing Control"),
                html.P(
                    "Trigger MLS preprocessing from this dashboard while the v9 Airflow package runs on the high-configuration workstation and writes tiered outputs back to B2."
                ),
            ],
            className="prep-page-head",
        ),

        dbc.Row(
            [
                _metric("Controller", "Dash only", "No local preprocessing on this system"),
                _metric("Orchestrator", AIRFLOW_DAG_ID, "Airflow DAG activated by API payload"),
                _metric("Bronze source", "raw tiles", "bronze_raw_data/<dataset>/source_files/tiles"),
                _metric("Silver + Gold", "v9 data lake", "Clean cloud plus model-ready scenes and blocks"),
            ],
            className="mb-2",
        ),

        dbc.Card(
            [
                dbc.CardHeader(html.H4("1. Execution Flow")),
                dbc.CardBody(
                    [
                        html.Div(
                            [
                                _flow_step(
                                    "Bronze Input",
                                    "Read raw tiles and label maps from the selected dataset prefix.",
                                    "blue",
                                ),
                                _flow_step(
                                    "Airflow Trigger",
                                    "Send config to the remote DAG. This web app does not run the script locally.",
                                    "green",
                                ),
                                _flow_step(
                                    "Remote v9 Script",
                                    "Run preprocessing on the high-CPU/GPU workstation.",
                                    "yellow",
                                ),
                                _flow_step(
                                    "Tiered Output",
                                    "Upload silver cloud, gold model data, logs, and metadata catalog entries.",
                                    "purple",
                                ),
                            ],
                            className="prep-flow",
                        )
                    ]
                ),
            ],
            className="mb-4",
        ),

        dbc.Card(
            [
                dbc.CardHeader(html.H4("2. Compute Health")),
                dbc.CardBody(
                    [
                        html.P(
                            "Both remote systems should expose a health endpoint before Dash routes preprocessing or training work to them.",
                            className="text-muted",
                        ),
                        html.Div(id="preproc-compute-health-grid", className="prep-node-grid"),
                    ]
                ),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        [
                            dbc.CardHeader(html.H4("3. Dataset and Run")),
                            dbc.CardBody(
                                [
                                    dbc.Label("Registered Dataset"),
                                    dcc.Dropdown(
                                        id="preproc-dataset-dropdown",
                                        options=_dataset_options(),
                                        placeholder="Select a dataset from the registry",
                                        clearable=True,
                                    ),
                                    html.Br(),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Dataset ID"),
                                                    dbc.Input(
                                                        id="preproc-dataset-id",
                                                        placeholder="Example: id-2",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Dataset Name"),
                                                    dbc.Input(
                                                        id="preproc-dataset-name",
                                                        placeholder="Example: Toronto MLS",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Label Field"),
                                                    dbc.Input(
                                                        id="preproc-label-field",
                                                        value="class",
                                                        placeholder="class or scalar_Label",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Building Labels"),
                                                    dbc.Input(
                                                        id="preproc-building-labels",
                                                        value="2",
                                                        placeholder="Example: 2 or 4",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Non-Building Labels"),
                                                    dbc.Input(
                                                        id="preproc-non-building-labels",
                                                        value="1, 3, 4, 5, 6, 7, 8, 9",
                                                        placeholder="Comma-separated raw class IDs",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=8,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Ignore Labels"),
                                                    dbc.Input(
                                                        id="preproc-ignore-labels",
                                                        value="0",
                                                        placeholder="Comma-separated raw class IDs",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=4,
                                            ),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
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
                                                md=6,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Prep Version"),
                                                    dbc.Input(
                                                        id="preproc-version",
                                                        value="prep_v001",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Execution Target"),
                                                    dcc.Dropdown(
                                                        id="preproc-execution-target",
                                                        options=build_compute_target_options(),
                                                        value="any_gpu_worker",
                                                        clearable=False,
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=12,
                                            ),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col(
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
                                                md=6,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Workers"),
                                                    dbc.Input(
                                                        id="preproc-workers",
                                                        type="number",
                                                        min=1,
                                                        max=64,
                                                        value=24,
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                        ],
                                        className="mb-3",
                                    ),
                                ]
                            ),
                        ],
                        className="h-100",
                    ),
                    lg=5,
                    className="mb-4",
                ),
                dbc.Col(
                    dbc.Card(
                        [
                            dbc.CardHeader(html.H4("4. Preprocessing Parameters")),
                            dbc.CardBody(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col([dbc.Label("Voxel Size (m)"), dbc.Input(id="preproc-voxel-size", type="number", value=0.02, step=0.01, min=0)], md=3),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Voxel Strategy"),
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
                                                md=3,
                                            ),
                                            dbc.Col([dbc.Label("Block Size (m)"), dbc.Input(id="preproc-block-size", type="number", value=2.0, step=0.5)], md=3),
                                            dbc.Col([dbc.Label("Points / Block"), dbc.Input(id="preproc-n-points", type="number", value=8192, step=1024)], md=3),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col([dbc.Label("Max Train Blocks"), dbc.Input(id="preproc-max-blocks", type="number", value=8000, step=500)], md=4),
                                            dbc.Col([dbc.Label("Val/Test Stride"), dbc.Input(id="preproc-stride", type="number", value=1.5, step=0.25)], md=4),
                                            dbc.Col([dbc.Label("Split Gap (m)"), dbc.Input(id="preproc-split-gap", type="number", value=2.0, step=0.5)], md=4),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col([dbc.Label("Total Segments"), dbc.Input(id="preproc-num-segments", type="number", value=DEFAULT_NUM_SEGMENTS, min=1, step=1)], md=3),
                                            dbc.Col([dbc.Label("Train Segments"), dbc.Input(id="preproc-train-segments", type="number", value=DEFAULT_TRAIN_SEGMENTS, min=0, step=1)], md=3),
                                            dbc.Col([dbc.Label("Val Segments"), dbc.Input(id="preproc-val-segments", type="number", value=DEFAULT_VAL_SEGMENTS, min=0, step=1)], md=3),
                                            dbc.Col([dbc.Label("Test Segments"), dbc.Input(id="preproc-test-segments", type="number", value=DEFAULT_TEST_SEGMENTS, min=0, step=1)], md=3),
                                        ],
                                        className="mb-3",
                                    ),
                                    dbc.Row(
                                        [
                                            dbc.Col([dbc.Label("Min Building Ratio"), dbc.Input(id="preproc-min-bldg", type="number", value=0.01, step=0.005)], md=4),
                                            dbc.Col([dbc.Label("RandLA Overlap"), dbc.Input(id="preproc-randla-overlap", type="number", value=0.0, step=0.05, min=0, max=0.95)], md=4),
                                            dbc.Col([dbc.Label("PTv3 Scene Length"), dbc.Input(id="preproc-ptv3-length", type="number", value=50.0, step=5)], md=4),
                                        ],
                                        className="mb-3",
                                    ),
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
                                ]
                            ),
                        ],
                        className="h-100",
                    ),
                    lg=7,
                    className="mb-4",
                ),
            ]
        ),

        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        [
                            dbc.CardHeader(html.H4("5. Bucket Contract")),
                            dbc.CardBody(
                                [
                                    html.P(
                                        "These are the exact B2 folders the Airflow job should read from and write to.",
                                        className="text-muted",
                                    ),
                                    dash_table.DataTable(
                                        id="preproc-storage-table",
                                        columns=[
                                            {"name": "Role", "id": "role"},
                                            {"name": "B2 Path", "id": "path"},
                                        ],
                                        data=[],
                                        page_size=10,
                                        **_table_style(),
                                    ),
                                ]
                            ),
                        ],
                        className="h-100",
                    ),
                    lg=6,
                    className="mb-4",
                ),
                dbc.Col(
                    dbc.Card(
                        [
                            dbc.CardHeader(html.H4("6. Script Source")),
                            dbc.CardBody(
                                [
                                    html.P(
                                        "The dashboard references this script, while Airflow runs the remote deployed copy.",
                                        className="text-muted",
                                    ),
                                    dash_table.DataTable(
                                        id="preproc-script-table",
                                        columns=[
                                            {"name": "Field", "id": "field"},
                                            {"name": "Value", "id": "value"},
                                        ],
                                        data=[],
                                        page_size=10,
                                        **_table_style(),
                                    ),
                                ]
                            ),
                        ],
                        className="h-100",
                    ),
                    lg=6,
                    className="mb-4",
                ),
            ]
        ),

        dbc.Card(
            [
                dbc.CardHeader(html.H4("7. Airflow Payload Preview")),
                dbc.CardBody(
                    [
                        dbc.Alert(
                            [
                                html.Strong("Execution rule: "),
                                "this page only creates and sends the Airflow trigger. The v9 package must run on the remote high-configuration system and upload results to the silver, gold, logs, and metadata prefixes.",
                            ],
                            color="info",
                        ),
                        html.H5("Remote Command"),
                        html.Pre(id="preproc-command-preview", className="lineage-box"),
                        html.H5("Trigger Config"),
                        html.Pre(id="preproc-payload-preview", className="lineage-box"),
                        dbc.ButtonGroup(
                            [
                                dbc.Button(
                                    "Save Trigger Payload",
                                    id="preproc-save-payload-button",
                                    color="secondary",
                                    outline=True,
                                ),
                                dbc.Button(
                                    "Trigger Airflow DAG",
                                    id="preproc-trigger-airflow-button",
                                    color="success",
                                ),
                            ]
                        ),
                        html.Div(id="preproc-action-message", className="mt-3"),
                    ]
                ),
            ],
            className="mb-4",
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
        return (
            dash.no_update,
            dash.no_update,
            dash.no_update,
            dash.no_update,
            dash.no_update,
            dash.no_update,
        )

    try:
        datasets = list_registered_datasets()
    except Exception:
        datasets = []

    for item in datasets:
        if item.get("dataset_id") == dataset_id:
            dataset_name = item.get("dataset_name") or dataset_id
            config = build_dataset_config(dataset_id, dataset_name)
            return (
                dataset_id,
                dataset_name,
                config["label_field"],
                _label_list_text(config["building_labels"]),
                _label_list_text(config["non_building_labels"]),
                _label_list_text(config["ignore_labels"]),
            )

    config = build_dataset_config(dataset_id, dataset_id)
    return (
        dataset_id,
        dataset_id,
        config["label_field"],
        _label_list_text(config["building_labels"]),
        _label_list_text(config["non_building_labels"]),
        _label_list_text(config["ignore_labels"]),
    )


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
):
    flags = set(feature_flags or [])
    airflow_queue = resolve_airflow_queue(execution_target)
    return build_airflow_conf(
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
    )


@callback(
    Output("preproc-compute-health-grid", "children"),
    Output("preproc-execution-target", "options"),
    Input("preproc-compute-health-refresh", "n_intervals"),
)
def refresh_compute_health(_):
    statuses = check_compute_nodes()
    return [_compute_node_card(item) for item in statuses], build_compute_target_options()


def _compute_target_blocker(execution_target):
    statuses = check_compute_nodes()
    if execution_target == "any_gpu_worker":
        if any(item.get("tone") == "connected" for item in statuses):
            return None
        return "No compute node is online. Start the health agent on System 1 or System 2 before triggering Airflow."

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

    return (
        f"Train + val + test segments must equal total segments "
        f"({train} + {val} + {test} != {total})."
    )


@callback(
    Output("preproc-storage-table", "data"),
    Output("preproc-script-table", "data"),
    Output("preproc-command-preview", "children"),
    Output("preproc-payload-preview", "children"),
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
)
def update_preprocessing_preview(*values):
    dataset_id = values[0] or "<dataset_id>"
    conf = _build_conf_from_values(*values)
    storage = conf["storage"] if values[0] else build_storage_contract(dataset_id, values[7] or "prep_v001")
    script = conf["script"]

    storage_rows = [
        {"role": key.replace("_", " ").title(), "path": value}
        for key, value in storage.items()
    ]
    script_rows = [
        {"field": "DAG", "value": conf["dag_id"]},
        {"field": "Run ID", "value": conf["run_id"]},
        {"field": "Script", "value": script["name"]},
        {"field": "What it does", "value": script["purpose"]},
        {"field": "Local reference", "value": script["local_reference_path"]},
        {"field": "Remote Airflow path", "value": script["remote_execution_path"]},
        {"field": "Dataset config", "value": conf["script_args"]["custom_dataset"]},
        {"field": "Exists on controller", "value": str(script["exists_on_controller"])},
        {"field": "Last modified", "value": script["last_modified"]},
    ]

    command = " \\\n  ".join(build_remote_command(conf))

    return (
        storage_rows,
        script_rows,
        command,
        json.dumps(conf, indent=2),
    )


@callback(
    Output("preproc-action-message", "children"),
    Input("preproc-save-payload-button", "n_clicks"),
    Input("preproc-trigger-airflow-button", "n_clicks"),
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
    prevent_initial_call=True,
)
def handle_preprocessing_action(save_clicks, trigger_clicks, *values):
    dataset_id = values[0]
    if not dataset_id:
        return dbc.Alert("Select or enter a dataset ID before creating an Airflow run.", color="warning")

    split_blocker = _segment_split_blocker(values[6], values[17], values[18], values[19], values[20])
    if split_blocker:
        return dbc.Alert(split_blocker, color="warning")

    conf = _build_conf_from_values(*values)
    button_id = dash.ctx.triggered_id

    try:
        if button_id == "preproc-trigger-airflow-button":
            health_blocker = _compute_target_blocker(values[8])
            if health_blocker:
                return dbc.Alert(
                    [
                        html.Strong("Compute target is not ready. "),
                        health_blocker,
                        html.Br(),
                        "Use Save Trigger Payload until the remote systems are connected.",
                    ],
                    color="warning",
                )

            if not AIRFLOW_API_BASE_URL:
                payload, payload_path = persist_airflow_request(conf)
                return dbc.Alert(
                    [
                        html.Strong("Payload saved, Airflow API not configured. "),
                        "Set AIRFLOW_API_BASE_URL, AIRFLOW_USERNAME, and AIRFLOW_PASSWORD to trigger directly. ",
                        html.Br(),
                        "Saved request: ",
                        html.Code(payload_path),
                        html.Br(),
                        "DAG run id: ",
                        html.Code(payload["dag_run_id"]),
                    ],
                    color="warning",
                )

            result = trigger_airflow_dag(conf)
            return dbc.Alert(
                [
                    html.Strong("Airflow DAG triggered. "),
                    "Run request was sent to ",
                    html.Code(result["airflow_url"]),
                    html.Br(),
                    "Payload archive: ",
                    html.Code(result["payload_path"]),
                ],
                color="success",
            )

        payload, payload_path = persist_airflow_request(conf)
        return dbc.Alert(
            [
                html.Strong("Trigger payload saved. "),
                "Use this JSON for Airflow or configure the API URL to trigger from the page.",
                html.Br(),
                html.Code(payload_path),
                html.Br(),
                "DAG run id: ",
                html.Code(payload["dag_run_id"]),
            ],
            color="success",
        )

    except Exception as exc:
        return dbc.Alert(f"Preprocessing trigger failed: {exc}", color="danger")
