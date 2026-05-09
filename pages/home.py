"""
pages/home.py
Landing page — served at the root path "/" so visitors no longer
see a blank 404 when they open the app without a route.

Dash picks this up automatically via use_pages=True in app.py.
The canvas point-cloud animation is driven by assets/landing.js
which Dash auto-includes on every page.
"""

import base64
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from urllib import error, request

import dash
from dash import Input, Output, callback, dcc, html

dash.register_page(__name__, path="/", name="Home")

# ── Pipeline stage card data ───────────────────────────────────────

_STAGES = [
    {
        "num": "Stage 01",
        "tag": "BRZ",
        "name": "Bronze Raw Ingestion",
        "desc": "Land raw MLS tiles and label maps in bronze_raw_data with manifests and checksum controls for governed object storage.",
        "color": "#4fb3ff",
        "bg": "rgba(79,179,255,0.12)",
    },
    {
        "num": "Stage 02",
        "tag": "QA",
        "name": "Metadata & Quality Profiling",
        "desc": "Publish dataset registry JSON in metadata and Parquet KPI, class, spatial, and readiness checks in metadata_analytics.",
        "color": "#3dd6b5",
        "bg": "rgba(61,214,181,0.12)",
    },
    {
        "num": "Stage 03",
        "tag": "AIR",
        "name": "Airflow Preprocessing Orchestration",
        "desc": "Trigger remote GPU preprocessing through Airflow, writing silver_preprocessed_data and gold_model_ready_data outputs.",
        "color": "#f2b84b",
        "bg": "rgba(242,184,75,0.12)",
    },
    {
        "num": "Stage 04",
        "tag": "ML",
        "name": "Experiment Training & Versioning",
        "desc": "Train PointNet++, RandLA-Net, and PTv3 pipelines with MLflow experiment tracking and DVC dataset versioning.",
        "color": "#b987ff",
        "bg": "rgba(185,135,255,0.12)",
    },
    {
        "num": "Stage 05",
        "tag": "INF",
        "name": "Inference & Segmentation Outputs",
        "desc": "Stage inference_ready_data artifacts and store point-wise building predictions in segmentation_outputs by model run.",
        "color": "#7bd88f",
        "bg": "rgba(123,216,143,0.12)",
    },
    {
        "num": "Stage 06",
        "tag": "GEO",
        "name": "Geometric Post-Processing",
        "desc": "Apply RANSAC clustering to prediction outputs and persist refined building objects in clustered_final_outputs.",
        "color": "#ff6b6b",
        "bg": "rgba(255,107,107,0.12)",
    },
    {
        "num": "Stage 07",
        "tag": "3D",
        "name": "3D Operational Visualization",
        "desc": "Use Rerun.io to validate raw, semantic, prediction, and cluster-level views for spatial QA and review.",
        "color": "#bde7ff",
        "bg": "rgba(189,231,255,0.12)",
    },
    {
        "num": "Stage 08",
        "tag": "OBS",
        "name": "Benchmarking & Observability",
        "desc": "Compare models with accuracy, precision, recall, F1, IoU, mIoU, inference time, and run history in logs.",
        "color": "#ffe6aa",
        "bg": "rgba(255,230,170,0.12)",
    },
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]

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
        "service": "DVC",
        "status": "Checking",
        "detail": "Dataset versioning probe pending.",
        "tone": "checking",
    },
    {
        "service": "System Runtime",
        "status": "Checking",
        "detail": "Dash process and local runtime probe pending.",
        "tone": "checking",
    },
]


def _stage_card(s):
    return html.Div(
        [
            html.Div(s["num"], className="lp-cnum"),
            html.Div(
                s["tag"],
                className="lp-cico",
                style={"background": s["bg"], "color": s["color"]},
            ),
            html.Div(s["name"], className="lp-cname"),
            html.Div(s["desc"], className="lp-cdesc"),
            html.Div(className="lp-cbar", style={"background": s["color"]}),
        ],
        className="lp-card",
    )


def _status_result(service, status, detail, tone):
    return {
        "service": service,
        "status": status,
        "detail": _short_detail(detail),
        "tone": tone,
    }


def _short_detail(value, limit=92):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _status_box(item):
    tone = item.get("tone", "checking")
    return html.Div(
        [
            html.Div(
                [
                    html.Span(className=f"lp-status-dot lp-status-dot-{tone}"),
                    html.Span(item.get("service", ""), className="lp-status-service"),
                ],
                className="lp-status-head",
            ),
            html.Div(item.get("status", ""), className=f"lp-sv lp-status-value lp-status-value-{tone}"),
            html.Div(item.get("detail", ""), className="lp-sl lp-status-detail"),
        ],
        className=f"lp-st lp-status lp-status-{tone}",
    )


def _run_probe(check_fn, timeout_seconds=4):
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(check_fn)
    try:
        return True, future.result(timeout=timeout_seconds)
    except TimeoutError:
        return False, "Connection probe timed out."
    except Exception as exc:
        return False, str(exc)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _check_b2_status():
    from services.b2_service import (
        B2_APPLICATION_KEY,
        B2_BUCKET_NAME,
        B2_KEY_ID,
        get_b2_bucket,
    )

    if not B2_KEY_ID or not B2_APPLICATION_KEY:
        return _status_result(
            "B2 Storage",
            "Not Configured",
            "B2_KEY_ID or B2_APPLICATION_KEY is missing.",
            "warning",
        )

    def connect_to_bucket():
        bucket = get_b2_bucket()
        return getattr(bucket, "name", None) or B2_BUCKET_NAME

    ok, result = _run_probe(connect_to_bucket, timeout_seconds=6)
    if ok:
        return _status_result(
            "B2 Storage",
            "Connected",
            f"Bucket reachable: {result}",
            "connected",
        )

    return _status_result("B2 Storage", "Offline", result, "offline")


def _check_airflow_status():
    from services.preprocessing_service import (
        AIRFLOW_API_BASE_URL,
        AIRFLOW_DAG_ID,
        AIRFLOW_PASSWORD,
        AIRFLOW_USERNAME,
    )

    if not AIRFLOW_API_BASE_URL:
        return _status_result(
            "Airflow",
            "Not Configured",
            "AIRFLOW_API_BASE_URL is missing; payload save mode only.",
            "warning",
        )

    def probe_airflow():
        headers = {"Accept": "application/json"}
        if AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
            token = base64.b64encode(
                f"{AIRFLOW_USERNAME}:{AIRFLOW_PASSWORD}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        health_url = f"{AIRFLOW_API_BASE_URL.rstrip('/')}/health"
        health_request = request.Request(health_url, headers=headers, method="GET")

        try:
            with request.urlopen(health_request, timeout=4) as response:
                if 200 <= response.status < 400:
                    return f"Health endpoint reachable for DAG {AIRFLOW_DAG_ID}."
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError("Airflow endpoint reachable, but authentication failed.") from exc
            if exc.code != 404:
                raise RuntimeError(f"Airflow returned HTTP {exc.code}.") from exc

        dag_url = f"{AIRFLOW_API_BASE_URL.rstrip('/')}/api/v1/dags/{AIRFLOW_DAG_ID}"
        dag_request = request.Request(dag_url, headers=headers, method="GET")
        with request.urlopen(dag_request, timeout=4) as response:
            if 200 <= response.status < 400:
                return f"DAG API reachable: {AIRFLOW_DAG_ID}."
            raise RuntimeError(f"Airflow returned HTTP {response.status}.")

    ok, result = _run_probe(probe_airflow, timeout_seconds=6)
    if ok:
        return _status_result("Airflow", "Connected", result, "connected")

    return _status_result("Airflow", "Offline", result, "offline")


def _check_dvc_status():
    dvc_bin = shutil.which("dvc")
    dvc_markers = [
        PROJECT_ROOT / ".dvc",
        PROJECT_ROOT / "dvc.yaml",
        PROJECT_ROOT / "dvc.lock",
        PROJECT_ROOT / ".dvcignore",
    ]
    has_dvc_metadata = any(path.exists() for path in dvc_markers)

    if not dvc_bin:
        return _status_result(
            "DVC",
            "Unavailable",
            "DVC CLI is not installed in this environment.",
            "warning",
        )

    if not has_dvc_metadata:
        return _status_result(
            "DVC",
            "Not Initialized",
            "No .dvc metadata or dvc.yaml found in this project.",
            "warning",
        )

    try:
        result = subprocess.run(
            [dvc_bin, "status"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _status_result("DVC", "Timeout", "dvc status timed out.", "offline")

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode == 0:
        detail = output or "DVC workspace is tracked and up to date."
        return _status_result("DVC", "Connected", detail, "connected")

    return _status_result(
        "DVC",
        "Attention",
        output or f"dvc status exited with code {result.returncode}.",
        "warning",
    )


def _check_system_status():
    try:
        import psutil

        memory_percent = psutil.virtual_memory().percent
        cpu_percent = psutil.cpu_percent(interval=None)
        detail = (
            f"Python {sys.version_info.major}.{sys.version_info.minor} on "
            f"{platform.system()} | CPU {cpu_percent:.0f}% | RAM {memory_percent:.0f}%"
        )
    except Exception:
        detail = f"Python {sys.version_info.major}.{sys.version_info.minor} on {platform.system()}"

    return _status_result("System Runtime", "Online", detail, "connected")


# ── Page layout ────────────────────────────────────────────────────

layout = html.Div(
    id="lp-home",
    className="lp-root",
    children=[
        html.Canvas(id="lp-cv"),

        html.Div(
            className="lp-topbar",
            children=[
                html.Div(
                    className="lp-brand",
                    children=[
                        html.Span(className="lp-brand-dot"),
                        html.Div(
                            [
                                html.Div(
                                    "Building Identification on Mobile LiDAR Data",
                                    className="lp-brand-title",
                                ),
                                html.Div(
                                    "Data Explorer · Preprocessing · Rerun Visualization",
                                    className="lp-brand-subtitle",
                                ),
                            ],
                            className="lp-brand-copy",
                        ),
                    ],
                ),
                dcc.Link(
                    "Data Explorer →",
                    href="/data-explorer",
                    className="lp-top-cta",
                ),
            ],
        ),

        # ── Hero section with animated canvas ─────────────────────
        html.Div(
            className="lp-hero",
            children=[
                # Horizontal scan-line sweep
                html.Div(className="lp-scan"),

                # Text overlay
                html.Div(
                    className="lp-hcnt",
                    children=[
                        # "Pipeline Active" badge
                        html.Div(
                            [html.Span(className="lp-bdot"), "Pipeline Active"],
                            className="lp-badge",
                        ),

                        html.H1(
                            ["Building Identification", html.Br(),
                             html.Em("on Mobile LiDAR Data")],
                            className="lp-h1",
                        ),

                        html.P(
                            ["Upload, register, profile, and visualize 3D point cloud datasets",
                             html.Br(),
                             "for building segmentation and model training."],
                            className="lp-p",
                        ),

                        html.Div(
                            [
                                dcc.Link(
                                    "Open Data Explorer →",
                                    href="/data-explorer",
                                    className="lp-bp",
                                ),
                                html.A(
                                    "About the Pipeline",
                                    href="#lp-pipeline",
                                    className="lp-bg",
                                ),
                            ],
                            className="lp-btns",
                        ),
                    ],
                ),
            ],
        ),

        # ── Live backend status bar ───────────────────────────────
        dcc.Interval(
            id="backend-status-refresh",
            interval=30000,
            n_intervals=0,
        ),
        html.Div(
            [_status_box(item) for item in _STATUS_PLACEHOLDERS],
            id="backend-status-strip",
            className="lp-stats",
        ),

        html.Div(
            className="lp-pipeline",
            id="lp-pipeline",
            children=[
                html.Div(
                    [
                        html.Div("Data Pipeline", className="lp-stl"),
                        html.Div(
                            "bronze_raw_data → metadata + metadata_analytics → silver_preprocessed_data → gold_model_ready_data → inference_ready_data → segmentation_outputs → clustered_final_outputs → logs",
                            className="lp-sts",
                        ),
                    ],
                    className="lp-section-head",
                ),
                html.Div(
                    [_stage_card(s) for s in _STAGES],
                    className="lp-cards",
                ),
            ],
        ),

        # ── Footer ─────────────────────────────────────────────────
        html.Div(
            [
                html.Span(
                    "Backblaze B2 · Open3D · Plotly Dash · Rerun SDK 0.31",
                    className="lp-ftl",
                ),
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
    checks = [
        _check_b2_status,
        _check_airflow_status,
        _check_dvc_status,
        _check_system_status,
    ]

    return [_status_box(check()) for check in checks]
