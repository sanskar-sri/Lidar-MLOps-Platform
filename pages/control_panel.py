from datetime import datetime, timezone

import dash
from dash import Input, Output, callback, dcc, html

from components.platform_theme import ops_service_health_card, ops_topbar
from services.airflow_health_service import get_backend_status_cards
from services.compute_nodes_service import COMPUTE_HEALTH_POLL_MS, check_compute_nodes
from services.mlflow_service import check_mlflow_service


dash.register_page(__name__, path="/control-panel", name="Control Panel", title="Control Panel - LiDAR Platform")


def _tone_slug(tone):
    normalized = str(tone or "").strip().lower()
    if normalized in {"connected", "online", "ok", "success"}:
        return "connected"
    if normalized in {"offline", "failed", "error"}:
        return "offline"
    return "warning"


def _summary_card(label, value, detail, tone="connected"):
    tone = _tone_slug(tone)
    return html.Div(
        [
            html.Div(label, className="control-summary-label"),
            html.Div(value, className=f"control-summary-value control-summary-value-{tone}"),
            html.Div(detail, className="control-summary-detail"),
        ],
        className=f"control-summary-card control-summary-card-{tone}",
    )


def _summary_cards(nodes, mlflow):
    total_nodes = len(nodes)
    online_nodes = sum(1 for node in nodes if _tone_slug(node.get("tone")) == "connected")
    routing_ready = online_nodes > 0
    mlflow_tone = _tone_slug(mlflow.get("tone"))

    return [
        _summary_card(
            "Refresh Cadence",
            f"{COMPUTE_HEALTH_POLL_MS / 1000:g}s",
            "COMPUTE_HEALTH_POLL_MS",
        ),
        _summary_card(
            "Active Compute",
            f"{online_nodes}/{total_nodes} online",
            "Windows Airflow workstation",
            "connected" if online_nodes else "warning",
        ),
        _summary_card(
            "Routing Gate",
            "Ready" if routing_ready else "Blocked",
            "health preflight before remote jobs",
            "connected" if routing_ready else "offline",
        ),
        _summary_card(
            "Tracking",
            mlflow.get("status") or "Unknown",
            "MLflow service visibility",
            mlflow_tone,
        ),
    ]


def _relative_age(iso_value):
    if not iso_value:
        return "waiting for first refresh"
    try:
        checked_at = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
    except ValueError:
        return "refreshed just now"
    seconds = max(0, int((datetime.now(timezone.utc) - checked_at).total_seconds()))
    if seconds < 5:
        return "refreshed just now"
    if seconds < 60:
        return f"refreshed {seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"refreshed {minutes}m ago"
    return f"refreshed {minutes // 60}h ago"


def _b2_status_item():
    try:
        cards = get_backend_status_cards()
    except Exception as exc:
        return {
            "service": "B2 Storage",
            "status": "Unknown",
            "detail": f"Could not read backend health cards: {exc}",
            "tone": "warning",
        }
    return next(
        (card for card in cards if "b2" in str(card.get("service", "")).lower()),
        {
            "service": "B2 Storage",
            "status": "Unknown",
            "detail": "No B2 health card was returned by the backend health service.",
            "tone": "warning",
        },
    )


layout = html.Div(
    className="control-page ops-page",
    children=[
        dcc.Interval(
            id="control-panel-refresh",
            interval=COMPUTE_HEALTH_POLL_MS,
            n_intervals=0,
        ),
        dcc.Interval(id="control-panel-b2-refresh", interval=30000, n_intervals=0, max_intervals=120),
        dcc.Interval(id="control-panel-age-tick", interval=1000, n_intervals=0),
        dcc.Store(id="control-panel-last-refresh-store"),

        ops_topbar("Control", "Remote workers, routing, and observability", "Operations Console"),

        html.Div(
            [
                html.Canvas(id="control-cv", className="ops-hero-canvas"),
                html.Div(className="ops-hero-shade"),
                html.Div(
                    [
                        html.Div("Live Operations", className="ops-eyebrow"),
                        html.H1(["Compute", html.Br(), html.Em("Control Panel")]),
                        html.P("Remote workstation health, routing readiness, and tracking service status for MLS runs."),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Div("...", id="ctrl-metric-nodes-value", className="ops-hero-metric-value"),
                                        html.Div("Active Nodes", className="ops-hero-metric-label"),
                                    ],
                                    className="ops-hero-metric",
                                ),
                                html.Div(
                                    [
                                        html.Div("...", id="ctrl-metric-refresh-value", className="ops-hero-metric-value"),
                                        html.Div("Refresh Cadence", className="ops-hero-metric-label"),
                                    ],
                                    className="ops-hero-metric",
                                ),
                                html.Div(
                                    [
                                        html.Div("...", id="ctrl-metric-mlflow-value", className="ops-hero-metric-value"),
                                        html.Div("MLflow Status", className="ops-hero-metric-label"),
                                    ],
                                    className="ops-hero-metric",
                                ),
                            ],
                            id="control-hero-metrics",
                            className="ops-hero-metrics",
                        ),
                    ],
                    className="ops-hero-copy",
                ),
                html.Div(
                    [
                        html.Div(id="control-panel-refreshed-at", className="control-refreshed"),
                        html.Button(
                            "Refresh now",
                            id="control-panel-refresh-button",
                            n_clicks=0,
                            className="control-refresh-button",
                        ),
                    ],
                    className="control-hero-actions",
                ),
            ],
            className="control-hero ops-hero ops-hero-control",
        ),
        html.Div(id="control-panel-summary-grid", className="control-summary-grid"),
        html.Div(
            [
                html.Section(
                    [
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H3("Remote Compute"),
                                        html.Span(id="control-panel-node-badge", className="control-section-badge"),
                                    ],
                                    className="control-section-title",
                                ),
                                html.Div("CPU, RAM, GPU, VRAM, Docker state, queue, and endpoint details.", className="control-section-sub"),
                            ],
                            className="control-section-head",
                        ),
                        html.Div(id="control-panel-node-grid", className="control-grid control-grid-nodes"),
                    ],
                    className="control-section",
                ),
                html.Section(
                    [
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H3("Tracking Service"),
                                        html.Span("MLflow", className="control-section-badge"),
                                    ],
                                    className="control-section-title",
                                ),
                                html.Div("Controller, preprocessing, and training tracking destinations.", className="control-section-sub"),
                            ],
                            className="control-section-head",
                        ),
                        html.Div(id="control-panel-mlflow", className="control-grid control-grid-single"),
                    ],
                    className="control-section",
                ),
                html.Section(
                    [
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H3("Cloud Storage"),
                                        html.Span("Backblaze B2", className="control-section-badge"),
                                    ],
                                    className="control-section-title",
                                ),
                                html.Div("Bucket connectivity, file count, and last write timestamp.", className="control-section-sub"),
                            ],
                            className="control-section-head",
                        ),
                        html.Div(id="control-panel-b2", className="control-grid control-grid-single"),
                    ],
                    className="control-section",
                ),
            ],
            className="control-sections",
        ),
    ],
)


@callback(
    Output("control-panel-summary-grid", "children"),
    Output("control-panel-node-grid", "children"),
    Output("control-panel-mlflow", "children"),
    Output("control-panel-node-badge", "children"),
    Output("control-panel-last-refresh-store", "data"),
    Output("ctrl-metric-nodes-value", "children"),
    Output("ctrl-metric-refresh-value", "children"),
    Output("ctrl-metric-mlflow-value", "children"),
    Input("control-panel-refresh", "n_intervals"),
    Input("control-panel-refresh-button", "n_clicks"),
)
def refresh_control_panel(_interval_ticks, _manual_clicks):
    nodes = check_compute_nodes()
    mlflow = check_mlflow_service()
    refreshed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_nodes = len(nodes)
    online_nodes = sum(1 for node in nodes if _tone_slug(node.get("tone")) == "connected")

    return (
        _summary_cards(nodes, mlflow),
        [ops_service_health_card(item, variant="control-node") for item in nodes],
        [ops_service_health_card(mlflow, variant="control-service")],
        f"{online_nodes}/{total_nodes} online",
        refreshed_at,
        f"{online_nodes}/{total_nodes}",
        f"{COMPUTE_HEALTH_POLL_MS / 1000:g}s",
        mlflow.get("status") or "Unknown",
    )


@callback(
    Output("control-panel-b2", "children"),
    Input("control-panel-b2-refresh", "n_intervals"),
    Input("control-panel-refresh-button", "n_clicks"),
)
def refresh_b2_panel(_interval_ticks, _manual_clicks):
    return [ops_service_health_card(_b2_status_item(), variant="control-service")]


@callback(
    Output("control-panel-refreshed-at", "children"),
    Input("control-panel-last-refresh-store", "data"),
    Input("control-panel-age-tick", "n_intervals"),
)
def update_refresh_age(last_refresh, _ticks):
    return _relative_age(last_refresh)
