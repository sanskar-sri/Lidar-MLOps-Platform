import json
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from components.ops_page_shell import page_shell, section
from components.platform_theme import ops_table_style
from services.metadata_service import list_registered_datasets


dash.register_page(
    __name__,
    path="/risk-exposure",
    name="Risk & Exposure",
    title="Risk & Exposure - LiDAR Platform",
)

_HEIGHT_CATS = ["Single storey", "Low rise", "Mid rise", "High rise"]
_HEIGHT_COLORS = ["#61b8ff", "#55e2a7", "#f0bd55", "#ff6f7d"]
_HEIGHT_FLOOR_RANGES = {
    "Single storey": "1 floor",
    "Low rise": "2–4 floors",
    "Mid rise": "4–8 floors",
    "High rise": "9+ floors",
}

_CONF_CATS = [
    "High confidence",
    "Moderate confidence",
    "Field check required",
    "No confidence data",
]
_CONF_COLORS = ["#55e2a7", "#f0bd55", "#ff6f7d", "#9aa9bd"]

_CHART_LAYOUT = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"color": "#eef6ff", "family": "'DM Sans', Arial, sans-serif", "size": 12},
    "margin": {"l": 10, "r": 10, "t": 30, "b": 10},
}


def _dataset_options():
    try:
        datasets = list_registered_datasets()
    except Exception:
        datasets = []
    return [
        {
            "label": f"{d.get('dataset_id', '')} — {d.get('dataset_name', '')}",
            "value": d.get("dataset_id", ""),
        }
        for d in datasets
        if d.get("dataset_id")
    ]


def _kpi_card(label, value, tone=""):
    cls = f"control-summary-card{f' control-summary-card-{tone}' if tone else ''}"
    val_cls = f"control-summary-value{f' control-summary-value-{tone}' if tone else ''}"
    return html.Div(
        [
            html.Div(label, className="control-summary-label"),
            html.Div(str(value), className=val_cls),
        ],
        className=cls,
    )


def _empty_fig(message="Load data to view chart"):
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        x=0.5, y=0.5, xref="paper", yref="paper",
        showarrow=False, font={"color": "#9aa9bd", "size": 13},
    )
    fig.update_layout(**_CHART_LAYOUT, xaxis_visible=False, yaxis_visible=False)
    return fig


layout = page_shell(
    active="Risk",
    subtitle="Building inventory and flood exposure intelligence",
    status="Risk Engine",
    canvas_id="risk-cv",
    eyebrow="GeoAI Products",
    title="Risk &",
    accent="Exposure",
    description=(
        "Load a GIS export and run building height, flood depth, and "
        "detection confidence analysis over the full building inventory."
    ),
    metrics=[
        ("Input", "GeoJSON Export"),
        ("Analysis", "Height · Flood · Confidence"),
        ("Flood Model", "LiDAR Z-elevation"),
        ("RGB Proxy", "RGB-enabled exports"),
    ],
    page_class="risk-page",
    children=[
        dcc.Store(id="risk-gdf-store", storage_type="session"),
        dcc.Store(id="risk-summary-store"),
        dcc.Download(id="download-field-check"),
        dcc.Interval(
            id="risk-datasets-refresh",
            interval=60_000,
            max_intervals=-1,
            n_intervals=0,
        ),

        # ── Section 1: Load Export ─────────────────────────────────────────
        section(
            "Load",
            "Load Building Data",
            "Select a dataset and run to load an existing GIS export for risk analysis.",
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    dbc.Label("Dataset"),
                                    dcc.Dropdown(
                                        id="risk-dataset-dropdown",
                                        options=_dataset_options(),
                                        placeholder="Select a registered dataset",
                                    ),
                                ],
                                className="ops-field",
                            ),
                            html.Div(
                                [
                                    dbc.Label("Export Run"),
                                    dcc.Dropdown(
                                        id="risk-run-dropdown",
                                        placeholder="Select run after choosing dataset",
                                    ),
                                ],
                                className="ops-field",
                            ),
                        ],
                        className="ops-field-grid",
                    ),
                    dbc.Button(
                        "Load Building Data",
                        id="load-risk-btn",
                        className="ops-btn ops-btn-primary mt-3",
                        n_clicks=0,
                    ),
                    html.Div(id="risk-load-alert", className="mt-2"),
                ]
            ),
            "ops-panel-primary",
        ),

        # ── Section 2: KPI Strip ───────────────────────────────────────────
        html.Div(
            id="risk-kpi-section",
            style={"display": "none"},
            className="ops-panel mt-3",
            children=[
                html.Div("Summary", className="ops-section-kicker"),
                html.H2("Building Inventory KPIs"),
                html.Div(id="risk-kpi-cards", className="control-summary-grid mt-3"),
            ],
        ),

        # ── Section 3: Height Classification ──────────────────────────────
        html.Div(
            id="risk-height-section",
            style={"display": "none"},
            className="ops-panel mt-3",
            children=[
                html.Div("Height Analysis", className="ops-section-kicker"),
                html.H2("Building Height Classification"),
                html.P(
                    "Height categories derived from LiDAR Z-range (z_max − z_min) per cluster."
                ),
                dcc.Graph(
                    id="risk-height-chart",
                    figure=_empty_fig(),
                    config={"displayModeBar": False},
                ),
                html.Div(id="risk-height-table", className="mt-3"),
            ],
        ),

        # ── Section 4: Flood Exposure ──────────────────────────────────────
        html.Div(
            id="risk-flood-section",
            style={"display": "none"},
            className="ops-panel mt-3",
            children=[
                html.Div("Flood Simulation", className="ops-section-kicker"),
                html.H2("Flood Depth Exposure"),
                dcc.Slider(
                    id="flood-depth-slider",
                    min=0.5,
                    max=2.0,
                    step=0.5,
                    value=1.0,
                    marks={0.5: "0.5 m", 1.0: "1.0 m", 2.0: "2.0 m"},
                    className="mt-3 mb-4",
                ),
                html.Div(id="risk-flood-kpi-cards", className="control-summary-grid"),
                html.P(
                    "Flood simulation derived from LiDAR Z-elevation. "
                    "Terrain level estimated from the 10th percentile of z_min across all buildings.",
                    className="mt-3",
                    style={"color": "#9aa9bd", "fontSize": "12px"},
                ),
            ],
        ),

        # ── Section 5: Detection Confidence ───────────────────────────────
        html.Div(
            id="risk-confidence-section",
            style={"display": "none"},
            className="ops-panel mt-3",
            children=[
                html.Div("Model Confidence", className="ops-section-kicker"),
                html.H2("Detection Confidence"),
                dcc.Graph(
                    id="risk-confidence-chart",
                    figure=_empty_fig(),
                    config={"displayModeBar": False},
                ),
                html.Div(
                    [
                        html.H4("Field Check Required Buildings", className="mt-3"),
                        html.Div(id="risk-field-check-table"),
                        dbc.Button(
                            "Download Field Check List (CSV)",
                            id="download-field-check-btn",
                            outline=True,
                            color="warning",
                            className="mt-2",
                            n_clicks=0,
                        ),
                    ]
                ),
            ],
        ),

        # ── Section 6: RGB Proxy ──────────────────────────────────────────
        html.Div(
            id="risk-rgb-section",
            style={"display": "none"},
            className="ops-panel mt-3",
            children=[
                html.Div("RGB Analysis", className="ops-section-kicker"),
                html.H2("RGB Construction Proxy"),
                html.P("Mean brightness distribution across detected building clusters when RGB attributes are present."),
                dcc.Graph(
                    id="risk-rgb-chart",
                    figure=_empty_fig(),
                    config={"displayModeBar": False},
                ),
                html.Div(id="risk-rgb-breakdown", className="control-summary-grid mt-3"),
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("risk-dataset-dropdown", "options"),
    Input("risk-datasets-refresh", "n_intervals"),
)
def refresh_risk_datasets(_):
    return _dataset_options()


@callback(
    Output("risk-run-dropdown", "options"),
    Output("risk-run-dropdown", "value"),
    Input("risk-dataset-dropdown", "value"),
)
def populate_risk_runs(dataset_id):
    if not dataset_id:
        return [], None
    base = Path(f"data/local_staging/gis_exports/{dataset_id}")
    if not base.exists():
        return [], None
    runs = sorted(
        [d.name for d in base.iterdir() if d.is_dir()],
        reverse=True,
    )
    options = [{"label": r, "value": r} for r in runs]
    return options, (runs[0] if runs else None)


@callback(
    Output("risk-gdf-store", "data"),
    Output("risk-summary-store", "data"),
    Output("risk-kpi-section", "style"),
    Output("risk-height-section", "style"),
    Output("risk-flood-section", "style"),
    Output("risk-confidence-section", "style"),
    Output("risk-load-alert", "children"),
    Input("load-risk-btn", "n_clicks"),
    State("risk-dataset-dropdown", "value"),
    State("risk-run-dropdown", "value"),
    prevent_initial_call=True,
)
def load_risk_data(n_clicks, dataset_id, run_id):
    if not n_clicks:
        raise PreventUpdate

    _show = {"display": "block"}
    _no_data = [dash.no_update, dash.no_update] + [{"display": "none"}] * 4

    if not dataset_id:
        return *_no_data, dbc.Alert("Select a dataset.", color="warning")
    if not run_id:
        return *_no_data, dbc.Alert("Select an export run.", color="warning")

    geojson_path = Path(
        f"data/local_staging/gis_exports/{dataset_id}/{run_id}/buildings.geojson"
    )
    if not geojson_path.exists():
        return *_no_data, dbc.Alert(
            f"GeoJSON not found at {geojson_path}. Run GIS Export first.", color="danger"
        )

    try:
        import geopandas as gpd
        from services.risk_service import run_risk_assessment

        gdf = gpd.read_file(str(geojson_path))

        cv_mean = None
        density_path = Path(
            f"data/local_staging/gold_outputs/{dataset_id}/"
            f"prep_v001/artifacts/eval/density_report.json"
        )
        if density_path.exists():
            try:
                dr = json.loads(density_path.read_text(encoding="utf-8"))
                cv_mean = dr.get("cv_mean")
            except Exception:
                pass

        result = run_risk_assessment(gdf)
        enriched_gdf = result["gdf"]
        summary = result["summary"]
        summary["scan_coverage_cv_mean"] = cv_mean

        gdf_json = enriched_gdf.to_json()

        alert = dbc.Alert(
            f"Loaded {summary['total_buildings']:,} buildings from {run_id}.",
            color="success",
            dismissable=True,
        )
        return gdf_json, summary, _show, _show, _show, _show, alert

    except Exception as exc:
        return *_no_data, dbc.Alert(f"Load failed: {exc}", color="danger")


@callback(
    Output("risk-kpi-cards", "children"),
    Input("risk-summary-store", "data"),
)
def populate_kpi_strip(summary):
    if not summary:
        return []

    total = summary.get("total_buildings", 0)
    conf = summary.get("confidence_distribution", {})
    high_conf = conf.get("High confidence", 0)
    field_check = conf.get("Field check required", 0)

    height_dist = summary.get("height_distribution", {})
    top_cat = max(height_dist, key=lambda k: height_dist[k]) if height_dist else "n/a"

    cv_mean = summary.get("scan_coverage_cv_mean")
    cv_label = f"{cv_mean:.3f}" if cv_mean is not None else "n/a"

    return [
        _kpi_card("Total Buildings", f"{total:,}"),
        _kpi_card("High Confidence", f"{high_conf:,}", "connected"),
        _kpi_card("Field Check Required", f"{field_check:,}", "warning"),
        _kpi_card("Predominant Height Category", top_cat),
        _kpi_card("Scan Coverage Quality (cv_mean)", cv_label),
    ]


@callback(
    Output("risk-height-chart", "figure"),
    Output("risk-height-table", "children"),
    Input("risk-summary-store", "data"),
)
def populate_height_section(summary):
    if not summary:
        return _empty_fig(), html.Div()

    height_dist = summary.get("height_distribution", {})
    total = summary.get("total_buildings", 1) or 1

    counts = [height_dist.get(cat, 0) for cat in _HEIGHT_CATS]
    fig = go.Figure(
        go.Bar(
            x=_HEIGHT_CATS,
            y=counts,
            marker_color=_HEIGHT_COLORS,
            showlegend=False,
        )
    )
    fig.update_layout(
        **_CHART_LAYOUT,
        xaxis={"title": None, "gridcolor": "rgba(125,180,255,0.1)"},
        yaxis={"title": "Count", "gridcolor": "rgba(125,180,255,0.1)"},
    )

    table_rows = [
        {
            "Category": cat,
            "Count": f"{height_dist.get(cat, 0):,}",
            "Estimated Floor Range": _HEIGHT_FLOOR_RANGES.get(cat, "—"),
            "% of Total": f"{height_dist.get(cat, 0) / total * 100:.1f}%",
        }
        for cat in _HEIGHT_CATS
    ]
    table = dash_table.DataTable(
        columns=[
            {"name": "Category", "id": "Category"},
            {"name": "Count", "id": "Count"},
            {"name": "Estimated Floor Range", "id": "Estimated Floor Range"},
            {"name": "% of Total", "id": "% of Total"},
        ],
        data=table_rows,
        **ops_table_style(),
    )
    return fig, table


@callback(
    Output("risk-flood-kpi-cards", "children"),
    Input("flood-depth-slider", "value"),
    Input("risk-summary-store", "data"),
)
def update_flood_cards(depth, summary):
    if not summary:
        return []

    key = f"{str(depth).replace('.', '_')}m"
    total = summary.get("total_buildings", 0)
    flood_data = summary.get("flood_exposure", {}).get(key, {})
    exposed = flood_data.get("exposed", 0)
    pct = flood_data.get("pct", 0.0)
    non_exposed = total - exposed
    terrain_z = summary.get("terrain_z_used", 0.0)

    return [
        _kpi_card("Exposed Buildings", f"{exposed:,} ({pct:.1f}%)", "warning"),
        _kpi_card("Non-Exposed Buildings", f"{non_exposed:,}"),
        _kpi_card("Terrain Z Used", f"{terrain_z:.2f} m"),
    ]


@callback(
    Output("risk-confidence-chart", "figure"),
    Output("risk-field-check-table", "children"),
    Input("risk-summary-store", "data"),
)
def populate_confidence_section(summary):
    if not summary:
        return _empty_fig(), html.Div()

    conf_dist = summary.get("confidence_distribution", {})
    counts = [conf_dist.get(cat, 0) for cat in _CONF_CATS]

    fig = go.Figure(
        go.Bar(
            x=counts,
            y=_CONF_CATS,
            orientation="h",
            marker_color=_CONF_COLORS,
            showlegend=False,
        )
    )
    fig.update_layout(
        **_CHART_LAYOUT,
        xaxis={"title": "Count", "gridcolor": "rgba(125,180,255,0.1)"},
        yaxis={"gridcolor": "rgba(125,180,255,0.1)", "automargin": True},
        height=220,
    )

    field_check_n = conf_dist.get("Field check required", 0)
    note = html.P(
        f"{field_check_n:,} building(s) flagged for field verification.",
        style={"color": "#9aa9bd", "fontSize": "12px"},
    )
    return fig, note


@callback(
    Output("download-field-check", "data"),
    Input("download-field-check-btn", "n_clicks"),
    State("risk-gdf-store", "data"),
    prevent_initial_call=True,
)
def download_field_check(n_clicks, gdf_json):
    if not n_clicks or not gdf_json:
        raise PreventUpdate
    try:
        import io
        import geopandas as gpd

        gdf = gpd.read_file(io.StringIO(gdf_json))
        subset = gdf[gdf.get("verification_status", "") == "Field check required"]
        cols = [
            "cluster_id", "point_count", "footprint_area_m2",
            "height_range_m", "confidence_mean",
            "centroid_x_utm", "centroid_y_utm",
        ]
        available = [c for c in cols if c in subset.columns]
        return dcc.send_data_frame(
            subset[available].to_csv, "field_check_list.csv", index=False
        )
    except Exception as exc:
        raise PreventUpdate from exc


@callback(
    Output("risk-rgb-section", "style"),
    Input("risk-summary-store", "data"),
)
def toggle_rgb_section(summary):
    if summary and summary.get("has_rgb"):
        return {"display": "block"}
    return {"display": "none"}


@callback(
    Output("risk-rgb-chart", "figure"),
    Output("risk-rgb-breakdown", "children"),
    Input("risk-gdf-store", "data"),
)
def populate_rgb_section(gdf_json):
    if not gdf_json:
        return _empty_fig(), []
    try:
        import io
        import geopandas as gpd

        gdf = gpd.read_file(io.StringIO(gdf_json))

        if "mean_brightness" not in gdf.columns or gdf["mean_brightness"].isna().all():
            return _empty_fig("No RGB data available"), []

        brightness = gdf["mean_brightness"].dropna()
        fig = go.Figure(
            go.Histogram(
                x=brightness,
                nbinsx=30,
                marker_color="#61b8ff",
                opacity=0.85,
                showlegend=False,
            )
        )
        fig.update_layout(
            **_CHART_LAYOUT,
            xaxis={"title": "Mean Brightness (0–255)", "gridcolor": "rgba(125,180,255,0.1)"},
            yaxis={"title": "Building Count", "gridcolor": "rgba(125,180,255,0.1)"},
        )

        era_counts = {}
        if "construction_era_proxy" in gdf.columns:
            for era in ["Older / darker material", "Mixed", "Modern / lighter material"]:
                era_counts[era] = int((gdf["construction_era_proxy"] == era).sum())

        breakdown_cards = [
            html.Div(
                [
                    html.Div(label, className="control-summary-label"),
                    html.Div(f"{count:,}", className="control-summary-value"),
                ],
                className="control-summary-card",
            )
            for label, count in era_counts.items()
        ]
        return fig, breakdown_cards

    except Exception:
        return _empty_fig("Could not render RGB chart"), []
