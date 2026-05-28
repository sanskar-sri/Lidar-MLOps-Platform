import dash_bootstrap_components as dbc
from dash import dash_table, html

from components.platform_theme import empty_state, ops_table_style, ops_topbar, platform_hero


def metric_strip(metrics):
    return html.Div(
        [
            html.Div(
                [
                    html.Div(str(value), className="ops-hero-metric-value"),
                    html.Div(label, className="ops-hero-metric-label"),
                ],
                className="ops-hero-metric",
            )
            for label, value in metrics
        ],
        className="ops-hero-metrics",
    )


def info_card(title, detail, kicker=None, tone="info"):
    return html.Div(
        [
            html.Div(kicker, className="ops-section-kicker") if kicker else None,
            html.H3(title),
            html.P(detail),
        ],
        className=f"ops-mini-card ops-mini-card-{tone}",
    )


def kv_grid(rows):
    return html.Div(
        [
            html.Div(
                [
                    html.Span(label, className="ops-kv-label"),
                    html.Span(str(value), className="ops-kv-value"),
                ],
                className="ops-kv-row",
            )
            for label, value in rows
        ],
        className="ops-kv-grid",
    )


def data_table(rows, columns=None, empty_title="No records", empty_detail="No records are available yet."):
    rows = rows or []
    if not rows:
        return empty_state(empty_title, empty_detail)
    if columns is None:
        keys = list(rows[0].keys())
        columns = [{"name": key.replace("_", " ").title(), "id": key} for key in keys]
    return dash_table.DataTable(
        columns=columns,
        data=rows,
        page_size=8,
        **ops_table_style(),
    )


def section(kicker, title, description, children, class_name=""):
    return html.Section(
        [
            html.Div(
                [
                    html.Div(kicker, className="ops-section-kicker"),
                    html.H2(title),
                    html.P(description),
                ],
                className="ops-section-head",
            ),
            children,
        ],
        className=f"ops-panel {class_name}".strip(),
    )


def page_shell(
    *,
    active,
    subtitle,
    status,
    canvas_id,
    eyebrow,
    title,
    accent,
    description,
    metrics,
    children,
    page_class,
):
    return html.Div(
        className=f"{page_class} ops-page",
        children=[
            ops_topbar(active, subtitle, status),
            platform_hero(
                canvas_id=canvas_id,
                eyebrow=eyebrow,
                title=title,
                accent=accent,
                description=description,
                metrics=metrics,
                class_name=f"ops-hero-{page_class}",
            ),
            html.Main(children, className="ops-workspace"),
        ],
    )


def placeholder_button(label):
    return dbc.Button(label, color="success", disabled=True, className="ops-primary-action")

