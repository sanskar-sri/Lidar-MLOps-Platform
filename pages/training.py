import json
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from services.compute_nodes_service import (
    COMPUTE_HEALTH_POLL_MS,
    build_compute_target_options,
    check_compute_nodes,
    resolve_airflow_queue,
)
from services.metadata_service import list_registered_datasets
from services.mlflow_service import mlflow_browser_url
from services.training_service import (
    AIRFLOW_API_BASE_URL,
    AIRFLOW_TRAINING_DAG_ID,
    DEFAULT_TRAINING_MLFLOW_TRACKING_URI,
    TRAINING_RUN_REQUEST_DIR,
    build_training_command,
    build_training_conf,
    persist_training_request,
    trigger_training_dag,
)


dash.register_page(__name__, path="/training", name="Training")


MODEL_OPTIONS = [
    {"label": "PointNet++ SSG  |  stable baseline", "value": "pointnet2"},
    {"label": "PointNet++ MSG  |  multi-scale geometry", "value": "pointnet2_msg"},
    {"label": "RandLA-Net  |  efficient large scenes", "value": "randlanet"},
]


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
    }


def _dataset_options():
    try:
        datasets = list_registered_datasets()
    except Exception as exc:
        print(f"[TRAINING DATASET LIST ERROR] {exc}")
        datasets = []

    return [
        {
            "label": f"{item.get('dataset_id', '')} - {item.get('dataset_name', '')}",
            "value": item.get("dataset_id", ""),
        }
        for item in datasets
        if item.get("dataset_id")
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
            ) if metrics else None,
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


def _ops_nav(active):
    links = [
        ("Home", "/"),
        ("Data Explorer", "/data-explorer"),
        ("Preprocessing", "/preprocessing"),
        ("Training", "/training"),
        ("Postprocessing", "/postprocessing"),
        ("Control", "/control-panel"),
    ]
    return html.Nav(
        [
            dcc.Link(
                label,
                href=href,
                className="ops-nav-link ops-nav-link-active" if label == active else "ops-nav-link",
            )
            for label, href in links
        ],
        className="ops-nav",
    )


def _hero_metric(label, value):
    return html.Div(
        [html.Div(value, className="ops-hero-metric-value"), html.Div(label, className="ops-hero-metric-label")],
        className="ops-hero-metric",
    )


def _training_history_rows(limit=8):
    root = Path(TRAINING_RUN_REQUEST_DIR)
    if not root.exists():
        return []

    rows = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        conf = payload.get("conf") or {}
        rows.append(
            {
                "created_at": payload.get("created_at") or "n/a",
                "run_id": payload.get("dag_run_id") or conf.get("run_id") or path.stem,
                "dataset": conf.get("dataset_id") or "n/a",
                "model": conf.get("model_type") or "n/a",
                "queue": conf.get("airflow_queue") or "n/a",
                "epochs": (conf.get("script_args") or {}).get("num_epochs", "n/a"),
            }
        )
    return rows


_legacy_layout = html.Div(
    className="prep-page",
    children=[
        dcc.Interval(id="training-dataset-refresh", interval=60000, n_intervals=0),
        dcc.Interval(id="training-compute-health-refresh", interval=COMPUTE_HEALTH_POLL_MS, n_intervals=0),

        html.Div(
            [
                html.Div("Airflow Remote Execution", className="prep-eyebrow"),
                html.H2("Training Control"),
                html.P(
                    "Submit segmentation training jobs against gold model-ready data while remote workers handle GPU execution and upload run artifacts to B2."
                ),
            ],
            className="prep-page-head",
        ),

        dbc.Row(
            [
                _metric("Controller", "Dash only", "No training process runs on this machine"),
                _metric("Orchestrator", AIRFLOW_TRAINING_DAG_ID, "Training DAG activated by API payload"),
                _metric("Input", "gold_model_ready_data", "NPZ blocks from preprocessing v9"),
                _metric("Outputs", "training + segmentation", "B2 lineage paths include dataset, prep version, model, run"),
            ],
            className="mb-2",
        ),

        dbc.Card(
            [
                dbc.CardHeader(html.H4("1. Compute Health")),
                dbc.CardBody(
                    [
                        html.P(
                            "A selected remote system must be online before Dash triggers the training DAG.",
                            className="text-muted",
                        ),
                        html.Div(id="training-compute-health-grid", className="prep-node-grid"),
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
                            dbc.CardHeader(html.H4("2. Dataset and Model")),
                            dbc.CardBody(
                                [
                                    dbc.Label("Registered Dataset"),
                                    dcc.Dropdown(
                                        id="training-dataset-dropdown",
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
                                                        id="training-dataset-id",
                                                        placeholder="paris-lille-id-1",
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
                                                        id="training-dataset-name",
                                                        placeholder="Paris-Lille-3D",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                    html.Br(),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Preprocessing Version"),
                                                    dbc.Input(
                                                        id="training-prep-version",
                                                        value="prep_v001",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Model"),
                                                    dcc.Dropdown(
                                                        id="training-model-type",
                                                        options=MODEL_OPTIONS,
                                                        value="pointnet2",
                                                        clearable=False,
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                ]
                            ),
                        ],
                        className="mb-4",
                    ),
                    lg=6,
                ),
                dbc.Col(
                    dbc.Card(
                        [
                            dbc.CardHeader(html.H4("3. Training Run")),
                            dbc.CardBody(
                                [
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Execution Target"),
                                                    dcc.Dropdown(
                                                        id="training-execution-target",
                                                        options=build_compute_target_options(),
                                                        value="any_gpu_worker",
                                                        clearable=False,
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Run ID"),
                                                    dbc.Input(
                                                        id="training-run-id",
                                                        placeholder="Leave blank for UTC run id",
                                                        persistence=True,
                                                        persistence_type="session",
                                                    ),
                                                ],
                                                md=6,
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                    html.Br(),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Epochs"),
                                                    dbc.Input(
                                                        id="training-num-epochs",
                                                        type="number",
                                                        min=1,
                                                        step=1,
                                                        value=80,
                                                    ),
                                                ],
                                                md=4,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Batch Size"),
                                                    dbc.Input(
                                                        id="training-batch-size",
                                                        type="number",
                                                        min=1,
                                                        step=1,
                                                        value=4,
                                                    ),
                                                ],
                                                md=4,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Learning Rate"),
                                                    dbc.Input(
                                                        id="training-learning-rate",
                                                        type="number",
                                                        min=0,
                                                        step=0.0001,
                                                        value=0.001,
                                                    ),
                                                ],
                                                md=4,
                                            ),
                                        ],
                                        className="g-3",
                                    ),
                                ]
                            ),
                        ],
                        className="mb-4",
                    ),
                    lg=6,
                ),
            ]
        ),

        dbc.Card(
            [
                dbc.CardHeader(html.H4("4. MLOps and Storage")),
                dbc.CardBody(
                    [
                        dbc.Row(
                            [
                                dbc.Col(
                                    [
                                        dbc.Label("MLflow Tracking URI"),
                                        dbc.Input(
                                            id="training-mlflow-uri",
                                            value=DEFAULT_TRAINING_MLFLOW_TRACKING_URI,
                                            persistence=True,
                                            persistence_type="session",
                                        ),
                                        html.A(
                                            dbc.Button(
                                                "See Experiment Tracking",
                                                id="training-open-mlflow-button",
                                                color="info",
                                                outline=True,
                                                size="sm",
                                                className="mt-2 w-100",
                                            ),
                                            id="training-open-mlflow-link",
                                            href=mlflow_browser_url(DEFAULT_TRAINING_MLFLOW_TRACKING_URI),
                                            target="_blank",
                                            rel="noopener noreferrer",
                                        ),
                                    ],
                                    md=4,
                                ),
                                dbc.Col(
                                    [
                                        dbc.Label("MLflow Experiment"),
                                        dbc.Input(
                                            id="training-mlflow-experiment",
                                            value="mls-training",
                                            persistence=True,
                                            persistence_type="session",
                                        ),
                                    ],
                                    md=4,
                                ),
                                dbc.Col(
                                    [
                                        dbc.Label("DVC Remote"),
                                        dbc.Input(
                                            id="training-dvc-remote",
                                            value="b2remote",
                                            persistence=True,
                                            persistence_type="session",
                                        ),
                                    ],
                                    md=4,
                                ),
                            ],
                            className="g-3",
                        ),
                        html.Br(),
                        dbc.Checklist(
                            id="training-storage-flags",
                            options=[
                                {"label": "Upload training and segmentation artifacts to B2", "value": "upload_to_b2"},
                            ],
                            value=["upload_to_b2"],
                            switch=True,
                        ),
                    ]
                ),
            ],
            className="mb-4",
        ),

        dbc.Card(
            [
                dbc.CardHeader(html.H4("5. Payload Preview")),
                dbc.CardBody(
                    [
                        html.Div(id="training-payload-table"),
                        html.Br(),
                        html.Pre(id="training-command-preview", className="prep-command-preview"),
                    ]
                ),
            ],
            className="mb-4",
        ),

        dbc.Button(
            "Trigger Training DAG",
            id="training-trigger-button",
            color="success",
            size="lg",
            className="mb-3",
        ),
        html.Div(id="training-trigger-result"),
    ],
)


layout = html.Div(
    className="prep-page training-page ops-page",
    children=[
        dcc.Interval(id="training-dataset-refresh", interval=60000, n_intervals=0),
        dcc.Interval(id="training-compute-health-refresh", interval=COMPUTE_HEALTH_POLL_MS, n_intervals=0),
        dcc.Interval(id="training-history-refresh", interval=60000, n_intervals=0),

        html.Header(
            [
                html.Div(
                    [
                        html.Div(className="ops-brand-mark"),
                        html.Div(
                            [
                                html.Div("LiDAR Platform", className="ops-brand-title"),
                                html.Div("Gold data to segmentation model runs", className="ops-brand-subtitle"),
                            ]
                        ),
                    ],
                    className="ops-brand",
                ),
                _ops_nav("Training"),
                html.Div("GPU Routing", className="ops-live-pill"),
            ],
            className="ops-topbar",
        ),

        html.Section(
            [
                html.Canvas(id="training-cv", className="ops-hero-canvas"),
                html.Div(className="ops-hero-shade"),
                html.Div(
                    [
                        html.Div("Remote Training Execution", className="ops-eyebrow"),
                        html.H1(["Training", html.Br(), html.Em("Control Room")]),
                        html.P(
                            "Pick a model like an operator, route the GPU job to the right worker, and keep a visible record of every training payload created by Dash."
                        ),
                        html.Div(
                            [
                                _hero_metric("Models", "3"),
                                _hero_metric("DAG", AIRFLOW_TRAINING_DAG_ID),
                                _hero_metric("Input", "Gold blocks"),
                            ],
                            className="ops-hero-metrics",
                        ),
                    ],
                    className="ops-hero-copy",
                ),
            ],
            className="ops-hero ops-hero-training",
        ),

        html.Main(
            [
                html.Div(
                    [
                        html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div("Model Selection", className="ops-section-kicker"),
                                        html.H2("Dataset and Architecture"),
                                        html.P("The model is now a visual selector, so the choice reads like an operational decision rather than a form value."),
                                    ],
                                    className="ops-section-head",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dbc.Label("Registered Dataset"),
                                                dcc.Dropdown(
                                                    id="training-dataset-dropdown",
                                                    options=_dataset_options(),
                                                    placeholder="Select a dataset from the registry",
                                                    clearable=True,
                                                ),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                        html.Div([dbc.Label("Dataset ID"), dbc.Input(id="training-dataset-id", placeholder="paris-lille-id-1", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Dataset Name"), dbc.Input(id="training-dataset-name", placeholder="Paris-Lille-3D", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Preprocessing Version"), dbc.Input(id="training-prep-version", value="prep_v001", persistence=True, persistence_type="session")], className="ops-field"),
                                    ],
                                    className="ops-field-grid",
                                ),
                                html.Div(
                                    [
                                        dbc.Label("Model"),
                                        dcc.RadioItems(
                                            id="training-model-type",
                                            options=MODEL_OPTIONS,
                                            value="pointnet2",
                                            className="training-model-selector",
                                            inputClassName="training-model-radio",
                                            labelClassName="training-model-option",
                                            persistence=True,
                                            persistence_type="session",
                                        ),
                                    ],
                                    className="ops-model-block",
                                ),
                            ],
                            className="ops-panel ops-panel-primary",
                        ),

                        html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div("Worker Preflight", className="ops-section-kicker"),
                                        html.H2("Compute Health"),
                                        html.P("Training remains blocked unless the selected remote worker is online."),
                                    ],
                                    className="ops-section-head",
                                ),
                                html.Div(id="training-compute-health-grid", className="prep-node-grid ops-node-grid"),
                            ],
                            className="ops-panel",
                        ),

                        html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div("Run Parameters", className="ops-section-kicker"),
                                        html.H2("Training Run"),
                                        html.P("GPU routing, run identity, and learning parameters stay compact and scannable."),
                                    ],
                                    className="ops-section-head",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dbc.Label("Execution Target"),
                                                dcc.Dropdown(
                                                    id="training-execution-target",
                                                    options=build_compute_target_options(),
                                                    value="any_gpu_worker",
                                                    clearable=False,
                                                    persistence=True,
                                                    persistence_type="session",
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div([dbc.Label("Run ID"), dbc.Input(id="training-run-id", placeholder="Leave blank for UTC run id", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("Epochs"), dbc.Input(id="training-num-epochs", type="number", min=1, step=1, value=80)], className="ops-field"),
                                        html.Div([dbc.Label("Batch Size"), dbc.Input(id="training-batch-size", type="number", min=1, step=1, value=4)], className="ops-field"),
                                        html.Div([dbc.Label("Learning Rate"), dbc.Input(id="training-learning-rate", type="number", min=0, step=0.0001, value=0.001)], className="ops-field"),
                                    ],
                                    className="ops-field-grid ops-field-grid-five",
                                ),
                            ],
                            className="ops-panel",
                        ),

                        html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div("Tracking", className="ops-section-kicker"),
                                        html.H2("MLOps and Storage"),
                                        html.P("The outbound run carries MLflow, DVC, B2, and segmentation-output destinations together."),
                                    ],
                                    className="ops-section-head",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dbc.Label("MLflow Tracking URI"),
                                                dbc.Input(id="training-mlflow-uri", value=DEFAULT_TRAINING_MLFLOW_TRACKING_URI, persistence=True, persistence_type="session"),
                                                html.A(
                                                    dbc.Button("Open MLflow", id="training-open-mlflow-button", color="info", outline=True, size="sm", className="mt-2 w-100"),
                                                    id="training-open-mlflow-link",
                                                    href=mlflow_browser_url(DEFAULT_TRAINING_MLFLOW_TRACKING_URI),
                                                    target="_blank",
                                                    rel="noopener noreferrer",
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div([dbc.Label("MLflow Experiment"), dbc.Input(id="training-mlflow-experiment", value="mls-training", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div([dbc.Label("DVC Remote"), dbc.Input(id="training-dvc-remote", value="b2remote", persistence=True, persistence_type="session")], className="ops-field"),
                                        html.Div(
                                            [
                                                dbc.Label("Storage Options"),
                                                dbc.Checklist(
                                                    id="training-storage-flags",
                                                    options=[{"label": "Upload training and segmentation artifacts to B2", "value": "upload_to_b2"}],
                                                    value=["upload_to_b2"],
                                                    switch=True,
                                                ),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                    ],
                                    className="ops-field-grid",
                                ),
                            ],
                            className="ops-panel",
                        ),

                        html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div("Review", className="ops-section-kicker"),
                                        html.H2("Payload and Job History"),
                                        html.P("Preview the command, trigger the DAG, then keep the most recent payloads visible below the action."),
                                    ],
                                    className="ops-section-head",
                                ),
                                html.Div(
                                    [
                                        html.Div(id="training-payload-table", className="ops-review-card"),
                                        html.Div(
                                            [
                                                html.H3("Remote Command"),
                                                html.Pre(id="training-command-preview", className="prep-command-preview ops-code-box"),
                                            ],
                                            className="ops-review-card",
                                        ),
                                    ],
                                    className="ops-review-grid",
                                ),
                                html.Div(
                                    [
                                        dbc.Button("Trigger Training DAG", id="training-trigger-button", color="success", size="lg", className="ops-primary-action"),
                                        html.Div(id="training-trigger-result", className="ops-trigger-result"),
                                    ],
                                    className="ops-trigger-row",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.H3("Recent Training Jobs"),
                                                html.Span("local payload ledger", className="ops-chip"),
                                            ],
                                            className="ops-inline-title",
                                        ),
                                        html.Div(id="training-job-history"),
                                    ],
                                    className="ops-subpanel ops-history-panel",
                                ),
                            ],
                            className="ops-panel ops-panel-review",
                        ),
                    ],
                    className="ops-stack",
                ),
            ],
            className="ops-workspace",
        ),
    ],
)


def _build_conf_from_values(values):
    airflow_queue = resolve_airflow_queue(values["execution_target"])
    return build_training_conf(
        dataset_id=values["dataset_id"],
        dataset_name=values["dataset_name"],
        prep_version=values["prep_version"],
        model_type=values["model_type"],
        execution_target=values["execution_target"],
        airflow_queue=airflow_queue,
        run_id=values["run_id"],
        num_epochs=values["num_epochs"] or 80,
        batch_size=values["batch_size"] or 4,
        learning_rate=values["learning_rate"] or 0.001,
        mlflow_tracking_uri=values["mlflow_uri"],
        mlflow_experiment=values["mlflow_experiment"],
        dvc_remote=values["dvc_remote"],
        upload_to_b2="upload_to_b2" in (values["storage_flags"] or []),
    )


def _compute_target_blocker(execution_target):
    if execution_target == "any_gpu_worker":
        return None
    for item in check_compute_nodes():
        if item.get("id") == execution_target:
            if item.get("state") == "Online":
                return None
            return f"{item.get('name') or execution_target} is not online: {item.get('detail')}"
    return f"Execution target {execution_target} is not configured in this dashboard."


@callback(
    Output("training-dataset-dropdown", "options"),
    Input("training-dataset-refresh", "n_intervals"),
)
def refresh_training_datasets(_n):
    return _dataset_options()


@callback(
    Output("training-dataset-id", "value"),
    Output("training-dataset-name", "value"),
    Input("training-dataset-dropdown", "value"),
    prevent_initial_call=True,
)
def apply_selected_dataset(dataset_id):
    if not dataset_id:
        raise PreventUpdate
    for item in list_registered_datasets():
        if item.get("dataset_id") == dataset_id:
            return dataset_id, item.get("dataset_name") or dataset_id
    return dataset_id, dataset_id


@callback(
    Output("training-compute-health-grid", "children"),
    Input("training-compute-health-refresh", "n_intervals"),
)
def refresh_training_compute_health(_n):
    return [_compute_node_card(item) for item in check_compute_nodes()]


@callback(
    Output("training-open-mlflow-link", "href"),
    Output("training-open-mlflow-button", "disabled"),
    Input("training-mlflow-uri", "value"),
)
def update_training_mlflow_link(uri):
    href = mlflow_browser_url(uri)
    return href, href == "#"


@callback(
    Output("training-job-history", "children"),
    Input("training-history-refresh", "n_intervals"),
    Input("training-trigger-result", "children"),
)
def refresh_training_history(_n, _latest_result):
    rows = _training_history_rows()
    if not rows:
        return html.Div("No training payloads have been saved yet.", className="ops-empty-state")
    return dash_table.DataTable(
        data=rows,
        columns=[
            {"name": "Created", "id": "created_at"},
            {"name": "Run ID", "id": "run_id"},
            {"name": "Dataset", "id": "dataset"},
            {"name": "Model", "id": "model"},
            {"name": "Queue", "id": "queue"},
            {"name": "Epochs", "id": "epochs"},
        ],
        page_size=8,
        **_table_style(),
    )


@callback(
    Output("training-payload-table", "children"),
    Output("training-command-preview", "children"),
    Input("training-dataset-id", "value"),
    Input("training-dataset-name", "value"),
    Input("training-prep-version", "value"),
    Input("training-model-type", "value"),
    Input("training-execution-target", "value"),
    Input("training-run-id", "value"),
    Input("training-num-epochs", "value"),
    Input("training-batch-size", "value"),
    Input("training-learning-rate", "value"),
    Input("training-mlflow-uri", "value"),
    Input("training-mlflow-experiment", "value"),
    Input("training-dvc-remote", "value"),
    Input("training-storage-flags", "value"),
)
def preview_training_payload(*raw_values):
    keys = [
        "dataset_id",
        "dataset_name",
        "prep_version",
        "model_type",
        "execution_target",
        "run_id",
        "num_epochs",
        "batch_size",
        "learning_rate",
        "mlflow_uri",
        "mlflow_experiment",
        "dvc_remote",
        "storage_flags",
    ]
    values = dict(zip(keys, raw_values))
    dataset_id = values["dataset_id"] or "<dataset_id>"
    values["dataset_id"] = dataset_id
    values["dataset_name"] = values["dataset_name"] or dataset_id
    values["prep_version"] = values["prep_version"] or "prep_v001"
    values["model_type"] = values["model_type"] or "pointnet2"
    values["execution_target"] = values["execution_target"] or "any_gpu_worker"

    conf = _build_conf_from_values(values)
    rows = [
        {"field": "DAG", "value": conf["dag_id"]},
        {"field": "Dataset", "value": conf["dataset_id"]},
        {"field": "Prep Version", "value": conf["prep_version"]},
        {"field": "Model", "value": conf["model_type"]},
        {"field": "Run ID", "value": conf["run_id"]},
        {"field": "Airflow Queue", "value": conf["airflow_queue"]},
        {"field": "Gold Input", "value": conf["storage"]["gold_input"]},
        {"field": "Training Output", "value": conf["storage"]["training_output"]},
        {"field": "Segmentation Output", "value": conf["storage"]["segmentation_output"]},
    ]
    table = dash_table.DataTable(
        data=rows,
        columns=[{"name": "Field", "id": "field"}, {"name": "Value", "id": "value"}],
        **_table_style(),
    )
    return table, " ".join(build_training_command(conf))


@callback(
    Output("training-trigger-result", "children"),
    Input("training-trigger-button", "n_clicks"),
    State("training-dataset-id", "value"),
    State("training-dataset-name", "value"),
    State("training-prep-version", "value"),
    State("training-model-type", "value"),
    State("training-execution-target", "value"),
    State("training-run-id", "value"),
    State("training-num-epochs", "value"),
    State("training-batch-size", "value"),
    State("training-learning-rate", "value"),
    State("training-mlflow-uri", "value"),
    State("training-mlflow-experiment", "value"),
    State("training-dvc-remote", "value"),
    State("training-storage-flags", "value"),
    prevent_initial_call=True,
)
def trigger_training(n_clicks, *raw_values):
    if not n_clicks:
        raise PreventUpdate

    keys = [
        "dataset_id",
        "dataset_name",
        "prep_version",
        "model_type",
        "execution_target",
        "run_id",
        "num_epochs",
        "batch_size",
        "learning_rate",
        "mlflow_uri",
        "mlflow_experiment",
        "dvc_remote",
        "storage_flags",
    ]
    values = dict(zip(keys, raw_values))
    if not values["dataset_id"]:
        return dbc.Alert("Select or enter a dataset ID before triggering training.", color="warning")

    values["dataset_name"] = values["dataset_name"] or values["dataset_id"]
    values["prep_version"] = values["prep_version"] or "prep_v001"
    values["model_type"] = values["model_type"] or "pointnet2"
    values["execution_target"] = values["execution_target"] or "any_gpu_worker"

    blocker = _compute_target_blocker(values["execution_target"])
    if blocker:
        return dbc.Alert(blocker, color="danger")

    conf = _build_conf_from_values(values)

    try:
        if not AIRFLOW_API_BASE_URL:
            payload, payload_path = persist_training_request(conf)
            return dbc.Alert(
                [
                    html.Strong("Training payload saved locally. "),
                    "Set AIRFLOW_API_BASE_URL, AIRFLOW_USERNAME, and AIRFLOW_PASSWORD to trigger directly. ",
                    html.Br(),
                    "Payload: ",
                    html.Code(payload_path),
                    html.Br(),
                    "DAG run id: ",
                    html.Code(payload["dag_run_id"]),
                ],
                color="warning",
            )
        result = trigger_training_dag(conf)
        return dbc.Alert(
            [
                html.Strong("Training DAG triggered. "),
                "DAG run id: ",
                html.Code(conf["run_id"]),
                html.Br(),
                "Payload: ",
                html.Code(result["payload_path"]),
            ],
            color="success",
        )
    except Exception as exc:
        return dbc.Alert(f"Training trigger failed: {exc}", color="danger")
