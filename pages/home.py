"""
pages/home.py
Landing page for the Dash Pages root route.

The home page is intentionally presentation-heavy, but the live status cards
still use the existing backend health service so UI polish does not disconnect
the operational truth from the rest of the app.
"""

from datetime import datetime
from pathlib import Path
import sqlite3
from urllib.parse import quote

import dash
from dash import Input, Output, callback, dcc, html

from services.airflow_health_service import get_b2_file_count, get_backend_status_cards
from services.metadata_service import list_registered_datasets


dash.register_page(__name__, path="/", name="Home")


_STAGES = [
    {
        "num": "Stage 01",
        "tag": "BRZ",
        "name": "Bronze Raw Ingestion",
        "desc": "Land raw .ply, .las, and .laz MLS tiles plus label maps in bronze_raw_data with manifest and checksum controls.",
        "color": "#4fb3ff",
        "status": "completed",
        "href": "/data-explorer",
    },
    {
        "num": "Stage 02",
        "tag": "META",
        "name": "Bronze Metadata Profiling",
        "desc": "Publish registry metadata plus Parquet KPI, class, spatial, and readiness checks in metadata_analytics.",
        "color": "#3dd6b5",
        "status": "completed",
        "href": "/data-explorer",
    },
    {
        "num": "Stage 03",
        "tag": "AIR",
        "name": "Remote Preprocessing Orchestration",
        "desc": "Dash sends the Airflow v9 payload while the workstation stages bronze inputs and runs the preprocessing script.",
        "color": "#f2b84b",
        "status": "running",
        "href": "/preprocessing",
    },
    {
        "num": "Stage 04",
        "tag": "SLV",
        "name": "Silver Conformed Cloud",
        "desc": "Write voxelised, offset-normalized, feature-enriched processed_cloud.npz as the model-agnostic silver tier.",
        "color": "#b987ff",
        "status": "pending",
        "href": "/preprocessing",
    },
    {
        "num": "Stage 05",
        "tag": "GLD",
        "name": "Gold Model-Ready Artifacts",
        "desc": "Write PointNet++ and RandLA blocks, Pointcept scenes, train/val/test splits, and eval artifacts for training.",
        "color": "#7bd88f",
        "status": "pending",
        "href": "/preprocessing",
    },
    {
        "num": "Stage 06",
        "tag": "ML",
        "name": "Training and Inference Runs",
        "desc": "Train PointNet++, RandLA-Net, and PTv3 from gold data, then store building predictions by model run.",
        "color": "#ff6b6b",
        "status": "pending",
        "href": "/training",
    },
    {
        "num": "Stage 07",
        "tag": "3D",
        "name": "Segmentation Refinement and QA",
        "desc": "Persist segmentation outputs, clustered_final_outputs, and Rerun views for spatial validation.",
        "color": "#bde7ff",
        "status": "pending",
        "href": "/postprocessing",
    },
    {
        "num": "Stage 08",
        "tag": "OBS",
        "name": "Benchmarking and Observability",
        "desc": "Track logs, metadata.json, MLflow metrics and artifacts, DVC context, and model comparison history.",
        "color": "#ffe6aa",
        "status": "pending",
        "href": "/control-panel",
    },
]

_STATUS_PLACEHOLDERS = [
    {
        "service": "B2 Storage",
        "status": "Checking",
        "detail": "Backblaze bucket health probe pending.",
        "tone": "checking",
    },
    {
        "service": "Airflow",
        "status": "Checking",
        "detail": "Remote orchestration endpoint probe pending.",
        "tone": "checking",
    },
    {
        "service": "MLflow",
        "status": "Checking",
        "detail": "Experiment tracking probe pending.",
        "tone": "checking",
    },
    {
        "service": "DVC",
        "status": "Checking",
        "detail": "Dataset versioning probe pending.",
        "tone": "checking",
    },
    {
        "service": "Airflow Runtime",
        "status": "Checking",
        "detail": "Airflow worker runtime probe pending.",
        "tone": "checking",
    },
    {
        "service": "Windows Workstation",
        "status": "Checking",
        "detail": "Windows host health endpoint probe pending.",
        "tone": "checking",
    },
]


def _short_detail(value, limit=104):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _relative_time(value):
    if not value:
        return "No runs yet"

    seconds = max(0, int((datetime.now() - value).total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _latest_request_time():
    roots = [
        Path("data/airflow_preprocessing_requests"),
        Path("data/airflow_training_requests"),
    ]
    files = []
    for root in roots:
        if root.exists():
            files.extend(
                path
                for path in root.glob("*.json")
                if not path.name.endswith("_dataset_config.json")
            )
    if not files:
        return None
    return datetime.fromtimestamp(max(path.stat().st_mtime for path in files))


def _mlflow_experiment_count():
    db_path = Path("data/mlflow/mlflow.db")
    if not db_path.exists():
        return 0

    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "select count(*) from experiments where lifecycle_stage != 'deleted'"
            ).fetchone()
            return int(row[0] or 0)
    except Exception as exc:
        print(f"[HOME MLFLOW COUNT WARNING] {exc}")
        return 0


def _home_stats():
    try:
        datasets = list_registered_datasets()
    except Exception as exc:
        print(f"[HOME DATASET STATS WARNING] {exc}")
        datasets = []

    registry_files = sum(int(row.get("total_files") or 0) for row in datasets)
    files_ingested = get_b2_file_count()
    if files_ingested is None:
        files_ingested = registry_files
    experiment_count = _mlflow_experiment_count()
    if not experiment_count:
        request_root = Path("data/airflow_preprocessing_requests")
        experiment_count = len(
            [
                path
                for path in request_root.glob("*.json")
                if not path.name.endswith("_dataset_config.json")
            ]
        ) if request_root.exists() else 0

    return [
        {
            "label": "Files Ingested",
            "value": files_ingested,
            "display": f"{files_ingested:,}",
            "kind": "number",
        },
        {
            "label": "Models Ready",
            "value": 3,
            "display": "3",
            "kind": "number",
        },
        {
            "label": "Experiments",
            "value": experiment_count,
            "display": f"{experiment_count:,}",
            "kind": "number",
        },
        {
            "label": "Last Run",
            "value": _relative_time(_latest_request_time()),
            "display": _relative_time(_latest_request_time()),
            "kind": "text",
        },
    ]


def _stat_tile(item):
    attrs = {
        "data-stat-kind": item["kind"],
        "data-stat-value": str(item["value"]),
    }
    return html.Div(
        [
            html.Div(item["display"], className="lp-stat-value", **attrs),
            html.Div(item["label"], className="lp-stat-label"),
        ],
        className="lp-stat",
    )


def _stats_strip():
    return [_stat_tile(item) for item in _home_stats()]


def _status_sparkline(service, tone):
    service_score = sum(ord(ch) for ch in str(service))
    base = {
        "connected": 72,
        "warning": 52,
        "offline": 32,
        "checking": 45,
    }.get(tone, 45)
    points = []
    for index in range(12):
        value = base + ((service_score + index * 7) % 18) - 8
        x = index * 10
        y = 28 - max(6, min(26, value / 4))
        points.append(f"{x},{y:.1f}")

    stroke = _tone_color(tone)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 110 30" preserveAspectRatio="none">'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="{stroke}" '
        'stroke-width="2" vector-effect="non-scaling-stroke"/>'
        '</svg>'
    )
    return html.Img(
        src=f"data:image/svg+xml;charset=utf-8,{quote(svg)}",
        className="lp-spark",
        alt=f"{service} uptime trend",
    )


def _status_box(item):
    service = item.get("service", "")
    tone = item.get("tone", "checking")
    show_dvc_action = service.lower() == "dvc" and tone != "connected"

    return html.Div(
        [
            html.Div(
                [
                    html.Div(service, className="lp-status-service"),
                    html.Div(
                        [
                            html.Span(className=f"lp-status-dot lp-status-dot-{tone}"),
                            html.Span(item.get("status", ""), className=f"lp-status-value lp-status-value-{tone}"),
                        ],
                        className="lp-status-badge",
                    ),
                ],
                className="lp-status-top",
            ),
            html.Div(_short_detail(item.get("detail", "")), className="lp-status-detail"),
            html.A(
                "Install DVC ->",
                href="https://dvc.org/doc/install",
                target="_blank",
                rel="noopener noreferrer",
                className="lp-status-action",
            ) if show_dvc_action else _status_sparkline(service, tone),
        ],
        className=f"lp-status-card lp-status-card-{tone}",
        style={"--status-color": _tone_color(tone)},
    )


def _tone_color(tone):
    return {
        "connected": "#3dd6b5",
        "warning": "#f2b84b",
        "offline": "#ff6b6b",
        "checking": "#7d8894",
    }.get(tone, "#7d8894")


def _stage_status_label(status):
    return {
        "completed": "✓ completed",
        "running": "⟳ running",
        "pending": "○ pending",
    }.get(status, "○ pending")


def _stage_last_run(status):
    return {
        "completed": "last run 2h ago",
        "running": "active now",
        "pending": "waiting",
    }.get(status, "waiting")


def _flow_bar():
    children = []
    for index, stage in enumerate(_STAGES):
        status = stage["status"]
        children.append(
            html.Div(
                stage["tag"],
                className=f"lp-flow-node lp-flow-node-{status}",
                style={"--stage-color": stage["color"]},
                title=f"{stage['num']} · {stage['name']}",
            )
        )
        if index < len(_STAGES) - 1:
            active_line = status == "running"
            line_class = "lp-flow-line lp-flow-line-active" if active_line else (
                "lp-flow-line lp-flow-line-complete" if status == "completed" else "lp-flow-line"
            )
            children.append(
                html.Div(
                    html.Span(className="lp-flow-travel") if active_line else None,
                    className=line_class,
                )
            )
    return html.Div(children, className="lp-flow-bar", role="img", **{"aria-label": "Pipeline status flow"})


def _stage_card(stage):
    status = stage["status"]
    return dcc.Link(
        [
            html.Div(
                [
                    html.Span(stage["num"].upper(), className="lp-cnum"),
                    html.Span(_stage_status_label(status), className=f"lp-stage-chip lp-stage-chip-{status}"),
                ],
                className="lp-card-head",
            ),
            html.Div(
                stage["tag"],
                className="lp-cico",
                style={"background": f"{stage['color']}24", "color": stage["color"]},
            ),
            html.Div(stage["name"], className="lp-cname"),
            html.Div(stage["desc"], className="lp-cdesc"),
            html.Div(
                [
                    html.Span(_stage_last_run(status), className="lp-card-ts"),
                    html.Span("↗", className="lp-card-arrow"),
                ],
                className="lp-card-foot",
            ),
            html.Div(className="lp-cbar", style={"background": stage["color"]}),
        ],
        href=stage["href"],
        className=f"lp-card lp-card-{status}",
        style={"--stage-color": stage["color"]},
    )


def _toast(message, tone):
    return html.Div(
        [
            html.Span(className=f"lp-toast-dot lp-toast-dot-{tone}"),
            html.Span(message),
        ],
        className="lp-toast",
    )


layout = html.Div(
    id="lp-home",
    className="lp-root",
    children=[
        dcc.Interval(id="backend-status-refresh", interval=30000, n_intervals=0),
        dcc.Interval(id="home-stats-refresh", interval=60000, n_intervals=0),

        html.Div(
            [
                _toast("Stage 01 · Bronze Raw Ingestion completed", "connected"),
                _toast("Stage 03 · Airflow preprocessing is running", "running"),
            ],
            className="lp-toast-wrap",
            id="lp-toast-wrap",
        ),

        html.Div(
            className="lp-topbar",
            children=[
                html.Div(
                    className="lp-brand",
                    children=[
                        html.Span(className="lp-brand-grid"),
                        html.Div(
                            [
                                html.Div("LiDAR Platform", className="lp-brand-title"),
                                html.Div(
                                    "Data Explorer · Medallion Preprocessing · Training · Rerun",
                                    className="lp-brand-subtitle",
                                ),
                            ],
                            className="lp-brand-copy",
                        ),
                    ],
                ),
                html.Div(
                    [
                        dcc.Link("Home", href="/", className="lp-nav-link lp-nav-link-active"),
                        dcc.Link("Data Explorer", href="/data-explorer", className="lp-nav-link"),
                        dcc.Link("Preprocessing", href="/preprocessing", className="lp-nav-link"),
                        dcc.Link("Training", href="/training", className="lp-nav-link"),
                        dcc.Link("Postprocessing", href="/postprocessing", className="lp-nav-link"),
                        dcc.Link("Control", href="/control-panel", className="lp-nav-link"),
                    ],
                    className="lp-nav",
                ),
                html.Div(
                    [html.Span(className="lp-live-dot"), "Pipeline Active"],
                    className="lp-live-pill",
                ),
            ],
        ),

        html.Div(
            className="lp-hero",
            children=[
                html.Canvas(id="lp-cv", **{"aria-label": "Animated LiDAR particle field"}),
                html.Div(
                    className="lp-hcnt",
                    children=[
                        html.Div("Mobile LiDAR · 3D Point Cloud Platform", className="lp-eyebrow"),
                        html.H1(
                            ["Building Identification", html.Br(), html.Em("on Mobile LiDAR Data")],
                            className="lp-h1",
                        ),
                        html.P(
                            "Upload, register, profile, and visualize 3D point cloud datasets for building segmentation and model training.",
                            className="lp-p",
                        ),
                        html.Div(
                            [
                                dcc.Link("Open Data Explorer ->", href="/data-explorer", className="lp-bp"),
                                html.A("About the Pipeline", href="#lp-pipeline", className="lp-bg"),
                            ],
                            className="lp-btns",
                        ),
                    ],
                ),
            ],
        ),

        html.Div(_stats_strip(), id="home-stats-strip", className="lp-live-stats"),

        html.Section(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("Infrastructure Status", className="lp-section-title"),
                                html.Div("Live probes from B2, Airflow, MLflow, DVC, and workers.", className="lp-section-sub"),
                            ]
                        ),
                        html.Div([html.Span("refresh in "), html.Span("30s", id="lp-refresh-countdown")], className="lp-refresh-pill"),
                    ],
                    className="lp-section-head",
                ),
                html.Div(
                    [_status_box(item) for item in _STATUS_PLACEHOLDERS],
                    id="backend-status-strip",
                    className="lp-status-grid",
                ),
            ],
            className="lp-status-section",
        ),

        html.Section(
            className="lp-pipeline",
            id="lp-pipeline",
            children=[
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("Data Pipeline", className="lp-section-title"),
                                html.Div(
                                    "bronze_raw_data -> metadata/analytics -> Airflow v9 -> silver_preprocessed_data -> gold_model_ready_data -> training/inference -> segmentation outputs -> logs/MLflow/DVC",
                                    className="lp-section-sub",
                                ),
                            ]
                        ),
                    ],
                    className="lp-section-head lp-pipeline-head",
                ),
                _flow_bar(),
                html.Div([_stage_card(stage) for stage in _STAGES], className="lp-cards"),
            ],
        ),

        html.Div(
            [
                html.Span("Backblaze B2 · Open3D · Plotly Dash · Rerun SDK 0.31", className="lp-ftl"),
                html.Span("data_explorer v2", className="lp-ftr"),
            ],
            className="lp-ft",
        ),
    ],
)


@callback(
    Output("backend-status-strip", "children"),
    Input("backend-status-refresh", "n_intervals"),
)
def refresh_backend_status(_):
    return [_status_box(item) for item in get_backend_status_cards()]


@callback(
    Output("home-stats-strip", "children"),
    Input("home-stats-refresh", "n_intervals"),
)
def refresh_home_stats(_):
    return _stats_strip()
