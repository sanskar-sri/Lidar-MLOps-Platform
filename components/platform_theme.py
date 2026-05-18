import re

from dash import dcc, html


COLORS = {
    "bg": "#05070d",
    "panel": "rgba(15, 22, 34, 0.92)",
    "border": "rgba(125, 180, 255, 0.18)",
    "text": "#eef6ff",
    "muted": "#9aa9bd",
    "cyan": "#61b8ff",
    "green": "#55e2a7",
    "purple": "#a98cff",
    "amber": "#f0bd55",
    "red": "#ff6f7d",
}

CARD_STYLE = {
    "background": COLORS["panel"],
    "border": f"1px solid {COLORS['border']}",
    "borderRadius": "10px",
}

HERO_STYLE = {
    "background": COLORS["bg"],
}


NAV_ITEMS = [
    ("Home", "/"),
    ("Data Explorer", "/data-explorer"),
    ("Preprocessing", "/preprocessing"),
    ("Training", "/training"),
    ("Postprocessing", "/postprocessing"),
    ("Control", "/control-panel"),
]


def ops_nav(active):
    return html.Nav(
        [
            dcc.Link(
                label,
                href=href,
                className="ops-nav-link ops-nav-link-active" if label == active else "ops-nav-link",
            )
            for label, href in NAV_ITEMS
        ],
        className="ops-nav",
    )


def status_badge(label, tone="active"):
    return html.Div(label, className=f"ops-live-pill ops-live-pill-{tone}")


def ops_brand(subtitle):
    return html.Div(
        [
            html.Div(className="ops-brand-mark"),
            html.Div(
                [
                    html.Div("LiDAR Platform", className="ops-brand-title"),
                    html.Div(subtitle, className="ops-brand-subtitle"),
                ]
            ),
        ],
        className="ops-brand",
    )


def ops_topbar(active, subtitle, status_label):
    return html.Header(
        [
            ops_brand(subtitle),
            ops_nav(active),
            status_badge(status_label),
        ],
        className="ops-topbar",
    )


def hero_metric(label, value):
    return html.Div(
        [
            html.Div(value, className="ops-hero-metric-value"),
            html.Div(label, className="ops-hero-metric-label"),
        ],
        className="ops-hero-metric",
    )


def platform_hero(canvas_id, eyebrow, title, accent, description, metrics, class_name=""):
    return html.Section(
        [
            html.Canvas(id=canvas_id, className="ops-hero-canvas"),
            html.Div(className="ops-hero-shade"),
            html.Div(
                [
                    html.Div(eyebrow, className="ops-eyebrow"),
                    html.H1([title, html.Br(), html.Em(accent)]),
                    html.P(description),
                    html.Div(
                        [hero_metric(label, value) for label, value in metrics],
                        className="ops-hero-metrics",
                    ),
                ],
                className="ops-hero-copy",
            ),
        ],
        className=f"ops-hero {class_name}".strip(),
        style=HERO_STYLE,
    )


def step_item(number, title, detail, tone="blue", state=None):
    state_class = f" ops-step-item-{state}" if state else ""
    return html.Div(
        [
            html.Div(number, className=f"ops-step-index ops-step-index-{tone}"),
            html.Div(
                [
                    html.Div(title, className="ops-step-name"),
                    html.Div(detail, className="ops-step-detail"),
                ],
                className="ops-step-copy",
            ),
        ],
        className=f"ops-step-item ops-step-item-{tone}{state_class}",
    )


def section_head(kicker, title, description):
    return html.Div(
        [
            html.Div(kicker, className="ops-section-kicker"),
            html.H2(title),
            html.P(description),
        ],
        className="ops-section-head",
    )


def status_tone(status):
    normalized = str(status or "").lower()
    if normalized in {"passed", "success", "verified", "generated", "connected", "running"}:
        return "ok"
    if normalized in {"partial", "planned", "queued", "checking", "unknown"}:
        return "warn"
    if normalized in {"failed", "missing", "error", "offline"}:
        return "danger"
    return "info"


def small_status(label, status):
    tone = status_tone(status)
    return html.Span(f"{label}: {status}", className=f"ops-small-status ops-small-status-{tone}")


def ops_table_style():
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
            "fontFamily": "'DM Sans', Arial, sans-serif",
            "fontSize": "12px",
            "whiteSpace": "normal",
            "height": "auto",
            "backgroundColor": "#0b111b",
            "color": "#eef6ff",
            "border": "1px solid rgba(125, 180, 255, 0.14)",
        },
        "style_header": {
            "fontWeight": "bold",
            "backgroundColor": "#111827",
            "color": "#eef6ff",
            "border": "1px solid rgba(125, 180, 255, 0.18)",
        },
        "style_data_conditional": [
            {"if": {"row_index": "odd"}, "backgroundColor": "#0f1724"},
        ],
    }


def _control_tone(tone):
    normalized = str(tone or "").strip().lower()
    if normalized in {"connected", "online", "ok", "success", "healthy", "passed", "running"}:
        return "connected"
    if normalized in {"offline", "failed", "error", "danger"}:
        return "offline"
    return "warning"


def _extract_percent(*values):
    for value in values:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value or ""))
        if match:
            return max(0.0, min(100.0, float(match.group(1))))
    return 0.0


def _metric_band(percent):
    if percent >= 90:
        return "offline"
    if percent >= 75:
        return "warning"
    return "connected"


def _control_metric_tile(metric):
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


def _uri_cell(label, value, detail, link=False):
    value = value or "n/a"
    href = value if link and str(value).startswith(("http://", "https://")) else None
    value_node = html.A(value, href=href, target="_blank", rel="noreferrer") if href else html.Span(value)
    return html.Div(
        [
            html.Div(label, className="control-uri-label"),
            html.Div(value_node, className="control-uri-value"),
            html.Div(detail, className="control-uri-detail"),
        ],
        className="control-uri-cell",
    )


def _ops_node_card(item):
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


def _control_node_card(item):
    tone = _control_tone(item.get("tone"))
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
        [_control_metric_tile(metric) for metric in metrics]
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


def _control_service_card(item):
    tone = _control_tone(item.get("tone") or item.get("status"))
    service = item.get("service") or item.get("name") or "Service"
    status = item.get("status") or item.get("state") or "Unknown"
    detail = item.get("detail") or ""
    uri_cells = []
    if service.lower().startswith("mlflow") or item.get("tracking_uri") or item.get("public_url"):
        uri_cells = [
            _uri_cell("Mac Tracking URI", item.get("tracking_uri"), "controller and preprocessing default", link=True),
            _uri_cell("Training URI", item.get("training_tracking_uri"), "Windows training default", link=True),
            _uri_cell("Browser URL", item.get("public_url") or item.get("url"), "open service UI", link=True),
        ]

    return html.Article(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(className=f"control-dot control-dot-{tone}"),
                            html.Div(
                                [
                                    html.H3(service if not service.lower().startswith("mlflow") else "MLflow Tracking Server"),
                                    html.Div(detail, className="control-node-queue"),
                                ],
                                className="control-title-stack",
                            ),
                        ],
                        className="control-card-head",
                    ),
                    html.Div(status, className=f"control-state control-state-{tone}"),
                ],
                className="control-card-top",
            ),
            html.Div(detail, className="control-detail") if not uri_cells else None,
            html.Div(uri_cells, className="control-uri-grid") if uri_cells else None,
        ],
        className=f"control-card control-card-tracking control-card-{tone}",
    )


def ops_service_health_card(item, variant="node"):
    if variant == "control-node":
        return _control_node_card(item)
    if variant in {"control-service", "service"}:
        return _control_service_card(item)
    return _ops_node_card(item)


def empty_state(title, detail):
    return html.Div(
        [
            html.Div(title, className="ops-empty-title"),
            html.Div(detail, className="ops-empty-detail"),
        ],
        className="ops-empty-state",
    )
