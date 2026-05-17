import re
from datetime import datetime, timezone

import dash
from dash import Input, Output, callback, dcc, html

from services.compute_nodes_service import COMPUTE_HEALTH_POLL_MS, check_compute_nodes
from services.mlflow_service import check_mlflow_service


dash.register_page(__name__, path="/control-panel", name="Control Panel")


def _tone_slug(tone):
    normalized = str(tone or "").strip().lower()
    if normalized in {"connected", "online", "ok", "success"}:
        return "connected"
    if normalized in {"offline", "failed", "error"}:
        return "offline"
    return "warning"


def _extract_percent(*values):
    for value in values:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value or ""))
        if match:
            return max(0.0, min(100.0, float(match.group(1))))
    return 0.0


def _sparkline(percent):
    seed = max(6, min(92, percent or 34))
    heights = [
        max(8, min(94, seed + delta))
        for delta in [-14, -4, -9, 6, -2, 11, 4, -7, 9, 0]
    ]
    return html.Div(
        [html.Span(style={"height": f"{height:.0f}%"}) for height in heights],
        className="control-sparkline",
    )


def _metric_band(percent):
    if percent >= 90:
        return "offline"
    if percent >= 75:
        return "warning"
    return "connected"


def _metric_tile(metric):
    label = metric.get("label", "")
    value = metric.get("value", "")
    detail = metric.get("detail", "")
    percent = _extract_percent(value, detail)
    band = _metric_band(percent)

    return html.Div(
        [
            html.Div(
                [
                    html.Div(label, className="control-metric-label"),
                    html.Div(value, className="control-metric-value"),
                ],
                className="control-metric-head",
            ),
            html.Div(
                html.Span(style={"width": f"{percent:.1f}%"}),
                className=f"control-gauge control-gauge-{band}",
            ),
            html.Div(detail, className="control-metric-detail"),
        ],
        className=f"control-metric control-metric-{band}",
        style={"--metric-pct": f"{percent:.1f}%"},
    )


def _kv(label, value, highlight=False):
    return html.Div(
        [
            html.Span(label, className="control-kv-label"),
            html.Span(value or "n/a", className="control-kv-value control-kv-highlight" if highlight else "control-kv-value"),
        ],
        className="control-kv",
    )


def _node_card(item):
    tone = _tone_slug(item.get("tone"))
    payload = item.get("payload") or {}
    platform = payload.get("platform") or {}
    checked_at = payload.get("checked_at") or payload.get("timestamp") or "not reported"
    roles = ", ".join(item.get("roles") or [])
    metrics = item.get("metrics") or []
    platform_text = " ".join(
        str(part)
        for part in [
            platform.get("system"),
            platform.get("release"),
            platform.get("machine"),
        ]
        if part
    )

    metric_content = (
        [_metric_tile(metric) for metric in metrics]
        if metrics
        else [html.Div("Waiting for CPU, RAM, GPU, and VRAM telemetry.", className="control-empty-line")]
    )

    return html.Article(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(className=f"control-dot control-dot-{tone}"),
                            html.Div(
                                [
                                    html.H3(item.get("name") or item.get("id") or "Compute Node"),
                                    html.Div(
                                        f"queue: {item.get('airflow_queue') or item.get('id') or 'n/a'}",
                                        className="control-node-queue",
                                    ),
                                ],
                                className="control-title-stack",
                            ),
                        ],
                        className="control-card-head",
                    ),
                    html.Div(item.get("state", "Unknown"), className=f"control-state control-state-{tone}"),
                ],
                className="control-card-top",
            ),
            html.Div(item.get("detail", ""), className="control-detail"),
            html.Div(metric_content, className="control-metrics"),
            html.Div(
                [
                    _kv("Roles", roles, highlight=True),
                    _kv("Health URL", item.get("health_url")),
                    _kv("Platform", platform_text),
                    _kv("Python", platform.get("python")),
                    _kv("Checked", checked_at),
                ],
                className="control-kv-grid",
            ),
        ],
        className=f"control-card control-card-{tone}",
    )


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


def _uri_cell(label, value, detail, link=False):
    value = value or "n/a"
    href = value if link and str(value).startswith(("http://", "https://")) else None
    value_node = (
        html.A(value, href=href, target="_blank", rel="noreferrer")
        if href
        else html.Span(value)
    )
    return html.Div(
        [
            html.Div(label, className="control-uri-label"),
            html.Div(value_node, className="control-uri-value"),
            html.Div(detail, className="control-uri-detail"),
        ],
        className="control-uri-cell",
    )


def _mlflow_card(item):
    tone = _tone_slug(item.get("tone"))
    return html.Article(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(className=f"control-dot control-dot-{tone}"),
                            html.Div(
                                [
                                    html.H3("MLflow Tracking Server"),
                                    html.Div(item.get("detail", ""), className="control-node-queue"),
                                ],
                                className="control-title-stack",
                            ),
                        ],
                        className="control-card-head",
                    ),
                    html.Div(item.get("status", "Unknown"), className=f"control-state control-state-{tone}"),
                ],
                className="control-card-top",
            ),
            html.Div(
                [
                    _uri_cell("Mac Tracking URI", item.get("tracking_uri"), "controller and preprocessing default", link=True),
                    _uri_cell("Training URI", item.get("training_tracking_uri"), "Windows training default", link=True),
                    _uri_cell("Browser URL", item.get("public_url") or item.get("url"), "open MLflow UI", link=True),
                ],
                className="control-uri-grid",
            ),
        ],
        className=f"control-card control-card-tracking control-card-{tone}",
    )


layout = html.Div(
    className="control-page ops-page",
    children=[
        dcc.Interval(
            id="control-panel-refresh",
            interval=COMPUTE_HEALTH_POLL_MS,
            n_intervals=0,
        ),
        dcc.Interval(id="control-panel-age-tick", interval=1000, n_intervals=0),
        dcc.Store(id="control-panel-last-refresh-store"),

        html.Header(
            [
                html.Div(
                    [
                        html.Div(className="ops-brand-mark"),
                        html.Div(
                            [
                                html.Div("LiDAR Platform", className="ops-brand-title"),
                                html.Div("Remote workers, routing, and observability", className="ops-brand-subtitle"),
                            ]
                        ),
                    ],
                    className="ops-brand",
                ),
                _ops_nav("Control"),
                html.Div("Operations Console", className="ops-live-pill"),
            ],
            className="ops-topbar",
        ),

        html.Div(
            [
                html.Canvas(id="control-cv", className="ops-hero-canvas"),
                html.Div(className="ops-hero-shade"),
                html.Div(
                    [
                        html.Div("Live Operations", className="ops-eyebrow"),
                        html.H1(["Compute", html.Br(), html.Em("Control Panel")]),
                        html.P("Remote workstation health, routing readiness, and tracking service status for MLS runs."),
                    ],
                    className="control-hero-copy",
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
        [_node_card(item) for item in nodes],
        [_mlflow_card(mlflow)],
        f"{online_nodes}/{total_nodes} online",
        refreshed_at,
    )


@callback(
    Output("control-panel-refreshed-at", "children"),
    Input("control-panel-last-refresh-store", "data"),
    Input("control-panel-age-tick", "n_intervals"),
)
def update_refresh_age(last_refresh, _ticks):
    return _relative_age(last_refresh)
