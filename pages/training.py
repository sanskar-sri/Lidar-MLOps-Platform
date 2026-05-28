import json
from datetime import datetime, timezone
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from components.lidar_particle_background import lidar_particle_background
from components.platform_theme import (
    empty_state,
    ops_service_health_card,
    ops_table_style,
    small_status,
)
from components.platform_header import platform_header
from services.compute_nodes_service import (
    COMPUTE_HEALTH_POLL_MS,
    build_compute_target_options,
    check_compute_nodes,
    resolve_airflow_queue,
)
from services.dataset_selection import resolve_selected_dataset_id
from services.metadata_service import list_registered_datasets
from services.mlflow_service import mlflow_browser_url
from services.preprocessing_runtime_service import build_airflow_status_snapshot
from services.silver_gold_outputs_service import get_gold_readiness
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


dash.register_page(__name__, path="/training", name="Training", title="Training - LiDAR Platform")


MODEL_OPTIONS = [
    {
        "label": html.Div(
            [
                html.Div("PointNet++ SSG", id="model-card-pointnet2", className="training-model-title"),
                html.Div("Stable baseline | ~4 GB VRAM | ~2 h / 80 ep", className="training-model-meta"),
                html.Span("Recommended", className="ops-chip training-model-chip"),
            ]
        ),
        "value": "pointnet2",
    },
    {
        "label": html.Div(
            [
                html.Div("PointNet++ MSG", id="model-card-pointnet2-msg", className="training-model-title"),
                html.Div("Multi-scale geometry | ~6 GB VRAM | ~2.5 h / 80 ep", className="training-model-meta"),
            ]
        ),
        "value": "pointnet2_msg",
    },
    {
        "label": html.Div(
            [
                html.Div("RandLA-Net", id="model-card-randlanet", className="training-model-title"),
                html.Div("Efficient large scenes | ~8 GB VRAM | graph tensors", className="training-model-meta"),
            ]
        ),
        "value": "randlanet",
    },
]


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


def _dataset_name_for(dataset_id):
    dataset_id = str(dataset_id or "").strip()
    if not dataset_id:
        return ""
    for item in list_registered_datasets():
        if item.get("dataset_id") == dataset_id:
            return item.get("dataset_name") or dataset_id
    return dataset_id


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


def _training_status_panel(status):
    if not status:
        return empty_state("Live Training Status", "Trigger a training DAG to begin polling Airflow.")

    state = status.get("state", "unknown")
    progress = status.get("progress_pct", 0)
    completed = int(status.get("completed_tasks") or 0)
    total = int(status.get("total_tasks") or 0)
    rows = [
        {"field": "DAG ID", "value": status.get("dag_id") or AIRFLOW_TRAINING_DAG_ID},
        {"field": "DAG run ID", "value": status.get("dag_run_id") or "n/a"},
        {"field": "State", "value": state},
        {"field": "Current task", "value": status.get("current_task") or "n/a"},
        {"field": "Completed tasks", "value": f"{completed} / {total}"},
        {"field": "Start time", "value": status.get("start_time") or "n/a"},
        {"field": "End time", "value": status.get("end_time") or "n/a"},
        {"field": "Duration", "value": _duration_label(status.get("start_time"), status.get("end_time"))},
        {"field": "Checked at", "value": status.get("checked_at") or "n/a"},
    ]
    task_chips = (
        html.Div(
            [
                small_status(
                    f"Task {index + 1}",
                    "success"
                    if index < completed
                    else (
                        "running"
                        if state in {"queued", "running", "scheduled"} and index == completed
                        else "pending"
                    ),
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
                            html.Span(
                                f"elapsed {_duration_label(status.get('start_time'), status.get('end_time'))}"
                            ),
                        ],
                        className="airflow-progress-label-group",
                    ),
                ],
                className="airflow-status-head",
            ),
            dbc.Progress(
                value=min(max(float(progress or 0), 0), 100),
                striped=state in {"queued", "running"},
                animated=state == "running",
                className="airflow-progress",
            ),
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
                    html.Pre(
                        status.get("latest_error") or "No Airflow log excerpt was available.",
                        className="ops-code-box",
                    ),
                ],
                color="danger",
                className="mt-3",
                is_open=state == "failed",
            ),
        ],
        className="airflow-status-panel",
    )


def _training_stat_tile(label, display):
    return html.Div(
        [
            html.Div(display, className="de-stat-value"),
            html.Div(label, className="de-stat-label"),
        ],
        className="de-stat",
    )


def _training_stats_strip():
    try:
        history = _training_history_rows(limit=100)
    except Exception:
        history = []
    dag_raw = AIRFLOW_TRAINING_DAG_ID or "not set"
    dag_label = (dag_raw[:14] + "…") if len(dag_raw) > 14 else dag_raw
    return [
        _training_stat_tile("Architectures", "3"),
        _training_stat_tile("Training Runs", f"{len(history):,}"),
        _training_stat_tile("Active DAG", dag_label),
        _training_stat_tile("MLOps Stack", "MLflow + DVC"),
    ]


def _build_training_job_cards(rows):
    if not rows:
        return dbc.Alert("No training runs yet.", color="secondary", className="mb-0")
    cards = []
    for row in rows[:6]:
        run_id = str(row.get("run_id") or "")
        short_id = (run_id[:22] + "…") if len(run_id) > 22 else run_id
        cards.append(
            html.Div(
                [
                    html.Span(short_id, className="dataset-card-title"),
                    html.Span(row.get("dataset", "n/a"), className="dataset-card-id"),
                    html.Span(
                        [
                            html.Span(
                                [
                                    html.Span("Model", className="dataset-card-metric-label"),
                                    html.Span(row.get("model", "n/a"), className="dataset-card-metric-value"),
                                ],
                                className="dataset-card-metric",
                            ),
                            html.Span(
                                [
                                    html.Span("Epochs", className="dataset-card-metric-label"),
                                    html.Span(str(row.get("epochs", "n/a")), className="dataset-card-metric-value"),
                                ],
                                className="dataset-card-metric",
                            ),
                        ],
                        className="dataset-card-metrics",
                    ),
                    html.Span(
                        [
                            html.Span(row.get("queue", "n/a"), className="dataset-card-pill"),
                            html.Span(
                                str(row.get("created_at") or "")[:16],
                                className="dataset-card-status",
                            ),
                        ],
                        className="dataset-card-foot",
                    ),
                ],
                className="dataset-card",
            )
        )
    return cards


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

layout = dbc.Container(
    fluid=True,
    className="data-explorer-page training-page-de",
    children=[
        dcc.Interval(id="training-dataset-refresh", interval=60000, n_intervals=0),
        dcc.Interval(id="training-compute-health-refresh", interval=COMPUTE_HEALTH_POLL_MS, n_intervals=0),
        dcc.Interval(id="training-history-refresh", interval=60000, n_intervals=0),
        dcc.Store(id="training-dag-run-store"),
        dcc.Store(id="training-airflow-status-store"),
        dcc.Store(id="training-gold-readiness-store"),
        dcc.Interval(id="training-airflow-status-refresh", interval=10000, n_intervals=0, disabled=True),
        html.Div(id="training-toast-wrap", className="lp-toast-wrap"),

        # ── Topbar ─────────────────────────────────────────────────────────
        platform_header(
            active_path="/training",
            brand_subtitle="Training · GPU Routing · MLOps",
            status_label="GPU Routing",
            visual_context="ops",
        ),

        # ── Hero ───────────────────────────────────────────────────────────
        html.Section(
            className="de-hero",
            children=[
                lidar_particle_background("training-cv", aria_label="Animated training field"),
                html.Div(
                    [
                        html.Div(
                            "Remote Training Execution · Airflow DAG · MLflow",
                            className="de-eyebrow",
                        ),
                        html.H1(
                            ["Training", html.Br(), html.Em("Control Room")],
                            className="de-hero-title",
                        ),
                        html.P(
                            "Pick a model architecture, route the GPU job to the right remote worker, "
                            "configure training parameters, and keep a visible record of every training payload.",
                            className="de-hero-copy",
                        ),
                        html.Div(
                            [
                                html.A(
                                    "Open Workspace →",
                                    href="#training-workspace",
                                    className="de-primary-cta",
                                ),
                                dcc.Link(
                                    "View Preprocessing",
                                    href="/preprocessing",
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

        # ── Stats strip ────────────────────────────────────────────────────
        html.Div(
            _training_stats_strip(),
            id="training-live-stats",
            className="de-live-stats",
        ),

        # ── Main workspace ─────────────────────────────────────────────────
        html.Section(
            id="training-workspace",
            className="data-explorer-workspace",
            children=[
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("Training", className="data-explorer-eyebrow"),
                                html.H2("Training workspace"),
                                html.P(
                                    "Configure a model run, verify compute health, preview the Airflow payload, "
                                    "and trigger the remote training DAG.",
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
                        # ── Sidebar ─────────────────────────────────────
                        html.Aside(
                            className="data-explorer-sidebar",
                            children=[
                                html.Div(
                                    [
                                        html.Div("Training Jobs", className="data-explorer-eyebrow"),
                                        html.H4("Recent Runs"),
                                        html.P(
                                            "Local training payloads ledger. Configure a new run using the tabs.",
                                            className="mb-0",
                                        ),
                                    ],
                                    className="data-explorer-sidebar-head",
                                ),

                                # Step guide strip
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Span("01", className="training-step-num training-step-num-blue"),
                                                html.Div(
                                                    [
                                                        html.Span("Model + Dataset", className="training-step-name"),
                                                        html.Span("architecture + source", className="training-step-detail"),
                                                    ]
                                                ),
                                            ],
                                            className="training-step-row",
                                        ),
                                        html.Div(
                                            [
                                                html.Span("02", className="training-step-num training-step-num-green"),
                                                html.Div(
                                                    [
                                                        html.Span("Compute", className="training-step-name"),
                                                        html.Span("online GPU worker", className="training-step-detail"),
                                                    ]
                                                ),
                                            ],
                                            className="training-step-row",
                                        ),
                                        html.Div(
                                            [
                                                html.Span("03", className="training-step-num training-step-num-purple"),
                                                html.Div(
                                                    [
                                                        html.Span("Parameters", className="training-step-name"),
                                                        html.Span("epochs, batch, LR + MLOps", className="training-step-detail"),
                                                    ]
                                                ),
                                            ],
                                            className="training-step-row",
                                        ),
                                        html.Div(
                                            [
                                                html.Span("04", className="training-step-num training-step-num-amber"),
                                                html.Div(
                                                    [
                                                        html.Span("Review", className="training-step-name"),
                                                        html.Span("payload + trigger DAG", className="training-step-detail"),
                                                    ]
                                                ),
                                            ],
                                            className="training-step-row",
                                        ),
                                    ],
                                    className="training-step-guide",
                                ),

                                # Recent job cards
                                html.Div(id="training-job-card-list", className="dataset-card-list"),

                                # Primary action
                                dbc.Button(
                                    "Trigger Training DAG",
                                    id="training-trigger-button",
                                    color="success",
                                    className="w-100 mt-3",
                                    disabled=True,
                                ),
                                html.Div(id="training-trigger-result", className="mt-2"),
                            ],
                        ),

                        # ── Main tabbed content ──────────────────────────
                        html.Section(
                            dbc.Tabs(
                                [
                                    # Tab 1 — Model + Dataset
                                    dbc.Tab(
                                        label="Model + Dataset",
                                        tab_id="model-dataset",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div("Step 01", className="de-eyebrow"),
                                                    html.H3("Dataset and Architecture"),
                                                    html.P(
                                                        "Select a registered dataset and choose the model architecture for this training run."
                                                    ),
                                                ],
                                                className="training-tab-head",
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
                                                    html.Div(
                                                        [
                                                            dbc.Label("Dataset ID"),
                                                            dbc.Input(
                                                                id="training-dataset-id",
                                                                placeholder="Selected dataset ID",
                                                                persistence=True,
                                                                persistence_type="session",
                                                            ),
                                                        ],
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
                                                        [
                                                            dbc.Label("Dataset Name"),
                                                            dbc.Input(
                                                                id="training-dataset-name",
                                                                placeholder="Dataset name",
                                                                persistence=True,
                                                                persistence_type="session",
                                                            ),
                                                        ],
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
                                                        [
                                                            dbc.Label("Preprocessing Version"),
                                                            dbc.Input(
                                                                id="training-prep-version",
                                                                value="prep_v001",
                                                                persistence=True,
                                                                persistence_type="session",
                                                            ),
                                                        ],
                                                        className="ops-field",
                                                    ),
                                                ],
                                                className="ops-field-grid",
                                            ),
                                            html.Div(
                                                id="training-gold-readiness",
                                                className="mt-3",
                                            ),
                                            html.Div(
                                                [
                                                    dbc.Label("Model Architecture"),
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
                                                    dbc.Tooltip(
                                                        "PointNet++: Deep Hierarchical Feature Learning on Point Sets in a Metric Space, Qi et al. 2017.",
                                                        target="model-card-pointnet2",
                                                        placement="top",
                                                    ),
                                                    dbc.Tooltip(
                                                        "PointNet++ MSG variant uses multi-scale grouping for denser local geometry.",
                                                        target="model-card-pointnet2-msg",
                                                        placement="top",
                                                    ),
                                                    dbc.Tooltip(
                                                        "RandLA-Net: Efficient Semantic Segmentation of Large-Scale Point Clouds.",
                                                        target="model-card-randlanet",
                                                        placement="top",
                                                    ),
                                                ],
                                                className="ops-model-block",
                                            ),
                                        ],
                                    ),

                                    # Tab 2 — Compute
                                    dbc.Tab(
                                        label="Compute",
                                        tab_id="compute",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div("Step 02", className="de-eyebrow"),
                                                    html.H3("Compute Health"),
                                                    html.P(
                                                        "Training is blocked unless the selected remote worker is online. "
                                                        "Verify before triggering the DAG."
                                                    ),
                                                ],
                                                className="training-tab-head",
                                            ),
                                            html.Div(
                                                id="training-compute-health-grid",
                                                className="prep-node-grid ops-node-grid",
                                            ),
                                        ],
                                    ),

                                    # Tab 3 — Parameters + MLOps
                                    dbc.Tab(
                                        label="Parameters",
                                        tab_id="parameters",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div("Step 03", className="de-eyebrow"),
                                                    html.H3("Training Parameters"),
                                                    html.P(
                                                        "GPU routing, run identity, and learning hyperparameters."
                                                    ),
                                                ],
                                                className="training-tab-head",
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
                                                    html.Div(
                                                        [
                                                            dbc.Label("Run ID"),
                                                            dbc.Input(
                                                                id="training-run-id",
                                                                placeholder="Leave blank for UTC run id",
                                                                persistence=True,
                                                                persistence_type="session",
                                                            ),
                                                        ],
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
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
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
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
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
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
                                                        className="ops-field",
                                                    ),
                                                ],
                                                className="ops-field-grid ops-field-grid-five",
                                            ),

                                            html.Hr(className="training-tab-divider"),

                                            html.Div(
                                                [
                                                    html.Div("Tracking", className="de-eyebrow"),
                                                    html.H3("MLOps and Storage"),
                                                    html.P(
                                                        "The outbound run carries MLflow, DVC, B2, and segmentation-output destinations together."
                                                    ),
                                                ],
                                                className="training-tab-head",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
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
                                                                    "Open MLflow",
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
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
                                                        [
                                                            dbc.Label("MLflow Experiment"),
                                                            dbc.Input(
                                                                id="training-mlflow-experiment",
                                                                value="mls-training",
                                                                persistence=True,
                                                                persistence_type="session",
                                                            ),
                                                        ],
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
                                                        [
                                                            dbc.Label("DVC Remote"),
                                                            dbc.Input(
                                                                id="training-dvc-remote",
                                                                value="b2remote",
                                                                persistence=True,
                                                                persistence_type="session",
                                                            ),
                                                        ],
                                                        className="ops-field",
                                                    ),
                                                    html.Div(
                                                        [
                                                            dbc.Label("Storage Options"),
                                                            dbc.Checklist(
                                                                id="training-storage-flags",
                                                                options=[
                                                                    {
                                                                        "label": "Upload training and segmentation artifacts to B2",
                                                                        "value": "upload_to_b2",
                                                                    }
                                                                ],
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
                                    ),

                                    # Tab 4 — Review
                                    dbc.Tab(
                                        label="Review",
                                        tab_id="review",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div("Step 04", className="de-eyebrow"),
                                                    html.H3("Payload Preview and Live Status"),
                                                    html.P(
                                                        "Review the complete Airflow conf payload and the remote training command, "
                                                        "then trigger the DAG from the sidebar."
                                                    ),
                                                ],
                                                className="training-tab-head",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        id="training-payload-table",
                                                        className="ops-review-card",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Remote Command"),
                                                            html.Pre(
                                                                id="training-command-preview",
                                                                className="prep-command-preview ops-code-box",
                                                            ),
                                                        ],
                                                        className="ops-review-card",
                                                    ),
                                                ],
                                                className="ops-review-grid",
                                            ),
                                            html.Div(
                                                [
                                                    html.H4("Live Training Status"),
                                                    html.Div(
                                                        id="training-airflow-status-panel",
                                                        children=_training_status_panel(None),
                                                    ),
                                                ],
                                                className="ops-review-card airflow-live-card mt-4",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.H4("Recent Training Jobs"),
                                                            html.Span("local payload ledger", className="ops-chip"),
                                                        ],
                                                        className="ops-inline-title",
                                                    ),
                                                    html.Div(id="training-job-history"),
                                                ],
                                                className="ops-subpanel ops-history-panel mt-4",
                                            ),
                                        ],
                                    ),
                                ],
                                id="training-tabs",
                                active_tab="model-dataset",
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
        if any(item.get("tone") == "connected" for item in check_compute_nodes()):
            return None
        return "No compute node is online. Start the health agent on the Windows workstation before triggering Airflow."
    for item in check_compute_nodes():
        if item.get("id") == execution_target:
            if item.get("state") == "Online":
                return None
            return f"{item.get('name') or execution_target} is not online: {item.get('detail')}"
    return f"Execution target {execution_target} is not configured in this dashboard."


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("training-live-stats", "children"),
    Input("training-history-refresh", "n_intervals"),
    Input("training-trigger-result", "children"),
)
def refresh_training_stats(_n, _result):
    return _training_stats_strip()


@callback(
    Output("training-dataset-dropdown", "options"),
    Input("training-dataset-refresh", "n_intervals"),
)
def refresh_training_datasets(_n):
    return _dataset_options()


@callback(
    Output("training-dataset-dropdown", "value"),
    Output("training-dataset-id", "value", allow_duplicate=True),
    Output("training-dataset-name", "value", allow_duplicate=True),
    Input("url", "search"),
    Input("selected-dataset-id", "data"),
    prevent_initial_call="initial_duplicate",
)
def apply_context_dataset(search, selected_dataset_id):
    dataset_id = resolve_selected_dataset_id(search, selected_dataset_id)
    if not dataset_id:
        return None, "", ""
    return dataset_id, dataset_id, _dataset_name_for(dataset_id)


@callback(
    Output("training-dataset-id", "value"),
    Output("training-dataset-name", "value"),
    Input("training-dataset-dropdown", "value"),
    prevent_initial_call=True,
)
def apply_selected_dataset(dataset_id):
    if not dataset_id:
        raise PreventUpdate
    return dataset_id, _dataset_name_for(dataset_id)


@callback(
    Output("training-gold-readiness-store", "data"),
    Output("training-gold-readiness", "children"),
    Output("training-trigger-button", "disabled"),
    Output("training-trigger-button", "children"),
    Input("training-dataset-id", "value"),
    Input("training-prep-version", "value"),
)
def update_gold_readiness(dataset_id, prep_version):
    dataset_id = str(dataset_id or "").strip()
    prep_version = str(prep_version or "prep_v001").strip() or "prep_v001"
    if not dataset_id:
        status = {
            "ready": False,
            "dataset_id": "",
            "prep_version": prep_version,
            "message": "Please select a dataset first.",
        }
        return status, dbc.Alert("Please select a dataset first.", color="info", className="mb-0"), True, "Trigger Training DAG"

    status = get_gold_readiness(dataset_id, prep_version)
    if not status.get("ready"):
        message = "No Gold model-ready data found. Run preprocessing first."
        detail = status.get("prefix") or ""
        return (
            status,
            dbc.Alert([message, html.Br(), html.Code(detail)], color="warning", className="mb-0"),
            True,
            "Run preprocessing first",
        )

    split_availability = status.get("split_availability") or {}
    rows = [
        ("Dataset ID", dataset_id),
        ("Prep Version", status.get("prep_version") or prep_version),
        ("Gold Blocks", status.get("gold_blocks", 0)),
        ("Train Blocks", "Available" if split_availability.get("train") else "Not detected"),
        ("Val Blocks", "Available" if split_availability.get("val") else "Not detected"),
        ("Test Blocks", "Available" if split_availability.get("test") else "Not detected"),
    ]
    return (
        status,
        html.Div(
            [
                small_status("Gold model-ready data", "ready"),
                html.Div(
                    [
                        html.Div(
                            [html.Span(label), html.Strong(str(value))],
                            className="preproc-trigger-row",
                        )
                        for label, value in rows
                    ],
                    className="preproc-trigger-grid",
                ),
            ],
            className="preproc-trigger-card",
        ),
        False,
        "Trigger Training DAG",
    )


@callback(
    Output("training-compute-health-grid", "children"),
    Input("training-compute-health-refresh", "n_intervals"),
)
def refresh_training_compute_health(_n):
    return [ops_service_health_card(item) for item in check_compute_nodes()]


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
    Output("training-job-card-list", "children"),
    Input("training-history-refresh", "n_intervals"),
    Input("training-trigger-result", "children"),
    Input("training-airflow-status-store", "data"),
)
def refresh_training_history(_n, _latest_result, _status):
    rows = _training_history_rows()
    cards = _build_training_job_cards(rows)
    if not rows:
        return (
            empty_state("No training payloads", "Saved or triggered training runs will appear here."),
            cards,
        )
    table = dash_table.DataTable(
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
        **ops_table_style(),
    )
    return table, cards


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
    Input("training-gold-readiness-store", "data"),
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
    gold_status = raw_values[-1] or {}
    values = dict(zip(keys, raw_values[:-1]))
    if not values["dataset_id"]:
        return (
            empty_state("Dataset required", "Please select a dataset first."),
            "Training command waits for a selected dataset.",
        )
    if not gold_status.get("ready"):
        return (
            empty_state("Gold data required", "No Gold model-ready data found. Run preprocessing first."),
            f"Expected Gold prefix: {gold_status.get('prefix') or '02_preprocessing/gold_model_ready_data/<dataset_id>/<prep_version>/'}",
        )
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
        **ops_table_style(),
    )
    return table, " ".join(build_training_command(conf))


@callback(
    Output("training-trigger-result", "children"),
    Output("training-dag-run-store", "data"),
    Output("training-airflow-status-refresh", "disabled"),
    Input("training-trigger-button", "n_clicks"),
    State("training-gold-readiness-store", "data"),
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
def trigger_training(n_clicks, gold_status, *raw_values):
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
        return (
            dbc.Alert("Please select a dataset first.", color="warning"),
            dash.no_update,
            True,
        )
    if not (gold_status or {}).get("ready"):
        return (
            dbc.Alert("No Gold model-ready data found. Run preprocessing first.", color="warning"),
            dash.no_update,
            True,
        )

    values["dataset_name"] = values["dataset_name"] or values["dataset_id"]
    values["prep_version"] = values["prep_version"] or "prep_v001"
    values["model_type"] = values["model_type"] or "pointnet2"
    values["execution_target"] = values["execution_target"] or "any_gpu_worker"

    blocker = _compute_target_blocker(values["execution_target"])
    if blocker:
        return dbc.Alert(blocker, color="danger"), dash.no_update, True

    conf = _build_conf_from_values(values)

    try:
        if not AIRFLOW_API_BASE_URL:
            payload, payload_path = persist_training_request(conf)
            return (
                dbc.Alert(
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
                ),
                {
                    "dag_id": AIRFLOW_TRAINING_DAG_ID,
                    "dag_run_id": payload["dag_run_id"],
                    "state": "not_configured",
                },
                True,
            )
        result = trigger_training_dag(conf)
        response = result.get("response") or {}
        dag_run_id = response.get("dag_run_id") or conf["run_id"]
        return (
            dbc.Alert(
                [
                    html.Strong("Training DAG triggered. "),
                    "DAG run id: ",
                    html.Code(dag_run_id),
                    html.Br(),
                    "Payload: ",
                    html.Code(result["payload_path"]),
                ],
                color="success",
            ),
            {
                "dag_id": AIRFLOW_TRAINING_DAG_ID,
                "dag_run_id": dag_run_id,
                "state": response.get("state") or "queued",
            },
            False,
        )
    except Exception as exc:
        return dbc.Alert(f"Training trigger failed: {exc}", color="danger"), dash.no_update, True


@callback(
    Output("training-airflow-status-store", "data"),
    Output("training-airflow-status-panel", "children"),
    Output("training-airflow-status-refresh", "disabled", allow_duplicate=True),
    Input("training-airflow-status-refresh", "n_intervals"),
    Input("training-dag-run-store", "data"),
    prevent_initial_call=True,
)
def poll_training_status(_ticks, dag_run):
    if not dag_run or not dag_run.get("dag_run_id"):
        raise PreventUpdate
    if dag_run.get("state") == "not_configured":
        return dag_run, _training_status_panel(dag_run), True

    dag_id = dag_run.get("dag_id") or AIRFLOW_TRAINING_DAG_ID
    status = build_airflow_status_snapshot(dag_id, dag_run["dag_run_id"])
    terminal = status.get("state") in {"success", "failed"}
    return status, _training_status_panel(status), terminal


@callback(
    Output("training-toast-wrap", "children"),
    Input("training-airflow-status-store", "data"),
)
def update_training_toast(status):
    state = (status or {}).get("state")
    if state == "success":
        return [
            html.Div(
                [
                    html.Span(className="lp-toast-dot lp-toast-dot-connected"),
                    "Training DAG completed successfully.",
                ],
                className="lp-toast lp-toast-show",
            )
        ]
    if state == "failed":
        return [
            html.Div(
                [
                    html.Span(className="lp-toast-dot", style={"background": "var(--ops-red)"}),
                    "Training DAG failed.",
                ],
                className="lp-toast lp-toast-show",
            )
        ]
    return []
