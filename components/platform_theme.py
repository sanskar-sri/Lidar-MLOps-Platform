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


def step_item(number, title, detail, tone="blue"):
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
        className=f"ops-step-item ops-step-item-{tone}",
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


def empty_state(title, detail):
    return html.Div(
        [
            html.Div(title, className="ops-empty-title"),
            html.Div(detail, className="ops-empty-detail"),
        ],
        className="ops-empty-state",
    )
