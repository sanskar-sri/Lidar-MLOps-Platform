import json

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html

from services.metadata_service import list_registered_datasets
from services.preprocessing_service import (
    AIRFLOW_API_BASE_URL,
    AIRFLOW_DAG_ID,
    build_airflow_conf,
    build_remote_command,
    build_storage_contract,
    get_preprocessing_script_info,
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


layout = html.Div(
    className="prep-page",
    children=[
        dcc.Interval(id="preproc-dataset-refresh", interval=60000, n_intervals=0),

        html.Div(
            [
                html.Div("Airflow Remote Execution", className="prep-eyebrow"),
                html.H2("Preprocessing Control"),
                html.P(
                    "Trigger MLS preprocessing from this dashboard while the heavy v8 script runs on the configured Airflow machine and writes model-ready outputs back to B2."
                ),
            ],
            className="prep-page-head",
        ),

        dbc.Row(
            [
                _metric("Controller", "Dash only", "No local preprocessing on this system"),
                _metric("Orchestrator", AIRFLOW_DAG_ID, "Airflow DAG activated by API payload"),
                _metric("Source bucket", "Building-Identification-MLS", "Backblaze B2 storage contract"),
                _metric("Training output", "gold_model_ready_data", "Final folder used by model training"),
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
                                    "Remote v8 Script",
                                    "Run preprocessing on the high-CPU/GPU workstation.",
                                    "yellow",
                                ),
                                _flow_step(
                                    "Gold Output",
                                    "Upload PointNet++, RandLA-Net, and PTv3-ready data to gold_model_ready_data.",
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

        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        [
                            dbc.CardHeader(html.H4("2. Dataset and Run")),
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
                            dbc.CardHeader(html.H4("3. Preprocessing Parameters")),
                            dbc.CardBody(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col([dbc.Label("Voxel Size (m)"), dbc.Input(id="preproc-voxel-size", type="number", value=0.05, step=0.01)], md=4),
                                            dbc.Col([dbc.Label("Block Size (m)"), dbc.Input(id="preproc-block-size", type="number", value=2.0, step=0.5)], md=4),
                                            dbc.Col([dbc.Label("Points / Block"), dbc.Input(id="preproc-n-points", type="number", value=8192, step=1024)], md=4),
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
                                            {"label": "Save PLY previews", "value": "save_ply"},
                                            {"label": "Compress output", "value": "compress_output"},
                                        ],
                                        value=["compute_normals", "include_density", "save_ply"],
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
                            dbc.CardHeader(html.H4("4. Bucket Contract")),
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
                                        page_size=8,
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
                            dbc.CardHeader(html.H4("5. Script Source")),
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
                                        page_size=8,
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
                dbc.CardHeader(html.H4("6. Airflow Payload Preview")),
                dbc.CardBody(
                    [
                        dbc.Alert(
                            [
                                html.Strong("Execution rule: "),
                                "this page only creates and sends the Airflow trigger. The v8 script must run on the remote high-configuration system and upload results to the gold bucket prefix.",
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
    Input("preproc-dataset-dropdown", "value"),
    prevent_initial_call=True,
)
def apply_selected_dataset(dataset_id):
    if not dataset_id:
        return dash.no_update, dash.no_update

    try:
        datasets = list_registered_datasets()
    except Exception:
        datasets = []

    for item in datasets:
        if item.get("dataset_id") == dataset_id:
            return dataset_id, item.get("dataset_name") or dataset_id

    return dataset_id, dataset_id


def _build_conf_from_values(
    dataset_id,
    dataset_name,
    mode,
    prep_version,
    output_mode,
    voxel_size,
    block_size,
    n_points,
    max_blocks_train,
    stride_val_test,
    split_gap_m,
    min_bldg_ratio,
    randla_overlap,
    ptv3_scene_length,
    num_workers,
    feature_flags,
):
    flags = set(feature_flags or [])
    return build_airflow_conf(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        mode=mode,
        prep_version=prep_version,
        output_mode=output_mode,
        voxel_size=voxel_size or 0.05,
        block_size=block_size or 2.0,
        n_points=n_points or 8192,
        max_blocks_train=max_blocks_train or 8000,
        stride_val_test=stride_val_test or 1.5,
        split_gap_m=split_gap_m or 2.0,
        min_bldg_ratio=min_bldg_ratio or 0.01,
        randla_overlap=randla_overlap or 0.0,
        ptv3_scene_length=ptv3_scene_length or 50.0,
        num_workers=num_workers or 24,
        compute_normals="compute_normals" in flags,
        include_density="include_density" in flags,
        save_ply="save_ply" in flags,
        compress_output="compress_output" in flags,
    )


@callback(
    Output("preproc-storage-table", "data"),
    Output("preproc-script-table", "data"),
    Output("preproc-command-preview", "children"),
    Output("preproc-payload-preview", "children"),
    Input("preproc-dataset-id", "value"),
    Input("preproc-dataset-name", "value"),
    Input("preproc-mode", "value"),
    Input("preproc-version", "value"),
    Input("preproc-output-mode", "value"),
    Input("preproc-voxel-size", "value"),
    Input("preproc-block-size", "value"),
    Input("preproc-n-points", "value"),
    Input("preproc-max-blocks", "value"),
    Input("preproc-stride", "value"),
    Input("preproc-split-gap", "value"),
    Input("preproc-min-bldg", "value"),
    Input("preproc-randla-overlap", "value"),
    Input("preproc-ptv3-length", "value"),
    Input("preproc-workers", "value"),
    Input("preproc-feature-flags", "value"),
)
def update_preprocessing_preview(*values):
    dataset_id = values[0] or "<dataset_id>"
    prep_version = values[3] or "prep_v001"
    storage = build_storage_contract(dataset_id, prep_version)
    script = get_preprocessing_script_info()

    storage_rows = [
        {"role": key.replace("_", " ").title(), "path": value}
        for key, value in storage.items()
    ]
    script_rows = [
        {"field": "Script", "value": script["name"]},
        {"field": "What it does", "value": script["purpose"]},
        {"field": "Local reference", "value": script["local_reference_path"]},
        {"field": "Remote Airflow path", "value": script["remote_execution_path"]},
        {"field": "Exists on controller", "value": str(script["exists_on_controller"])},
        {"field": "Last modified", "value": script["last_modified"]},
    ]

    conf = _build_conf_from_values(*values)
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
    State("preproc-mode", "value"),
    State("preproc-version", "value"),
    State("preproc-output-mode", "value"),
    State("preproc-voxel-size", "value"),
    State("preproc-block-size", "value"),
    State("preproc-n-points", "value"),
    State("preproc-max-blocks", "value"),
    State("preproc-stride", "value"),
    State("preproc-split-gap", "value"),
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

    conf = _build_conf_from_values(*values)
    button_id = dash.ctx.triggered_id

    try:
        if button_id == "preproc-trigger-airflow-button":
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
