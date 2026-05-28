import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from components.ops_page_shell import page_shell, section
from components.platform_theme import ops_table_style
from services.metadata_service import list_registered_datasets


dash.register_page(
    __name__,
    path="/gis-exports",
    name="GIS Exports",
    title="GIS Exports - LiDAR Platform",
)


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


def _summary_card(label, value):
    return html.Div(
        [
            html.Div(label, className="control-summary-label"),
            html.Div(str(value), className="control-summary-value"),
        ],
        className="control-summary-card",
    )


_EXPORT_FORM = html.Div(
    [
        html.Div(
            [
                html.Div(
                    [
                        dbc.Label("Dataset"),
                        dcc.Dropdown(
                            id="dataset-gis-dropdown",
                            options=_dataset_options(),
                            placeholder="Select a registered dataset",
                        ),
                    ],
                    className="ops-field",
                ),
                html.Div(
                    [
                        dbc.Label("Preprocessing Version"),
                        dcc.Dropdown(
                            id="prepversion-gis-dropdown",
                            options=[
                                {"label": "prep_v001", "value": "prep_v001"},
                                {"label": "prep_v002", "value": "prep_v002"},
                            ],
                            value="prep_v001",
                            clearable=False,
                        ),
                    ],
                    className="ops-field",
                ),
                html.Div(
                    [
                        dbc.Label("Model"),
                        dcc.Dropdown(
                            id="model-gis-dropdown",
                            options=[
                                {"label": "PointNet++ SSG", "value": "pointnet2"},
                                {"label": "PointNet++ MSG", "value": "pointnet2_msg"},
                                {"label": "RandLA-Net", "value": "randlanet"},
                                {"label": "Point Transformer v3", "value": "ptv3"},
                            ],
                            value="pointnet2",
                            clearable=False,
                        ),
                    ],
                    className="ops-field",
                ),
            ],
            className="ops-field-grid",
        ),
        html.Div(
            [
                html.Div(
                    [
                        dbc.Label("Run ID"),
                        dbc.Input(
                            id="run-id-gis-input",
                            placeholder="e.g. paris-lille-id-1_prep_v001_pointnet2_run1",
                        ),
                    ],
                    className="ops-field ops-field-wide",
                ),
                html.Div(
                    [
                        dbc.Label("Prediction PLY path (local or B2 path)"),
                        dbc.Input(
                            id="ply-path-gis-input",
                            placeholder=(
                                "data/local_staging/segmentation_outputs/"
                                ".../test_full_prediction.ply"
                            ),
                        ),
                    ],
                    className="ops-field ops-field-wide",
                ),
            ],
            className="ops-field-grid",
        ),
        html.Div(
            dbc.Switch(
                id="upload-b2-gis-toggle",
                label="Upload exports to B2 after generation",
                value=True,
            ),
            className="mt-3 mb-3",
        ),
        dbc.Button(
            "Generate GeoJSON + GeoParquet",
            id="run-gis-export-btn",
            className="ops-btn ops-btn-primary",
            n_clicks=0,
        ),
    ]
)

_STATUS_CONTENT = html.Div(
    [
        html.Div(id="gis-export-summary-cards", className="control-summary-grid"),
        html.Div(id="gis-export-b2-table", className="mt-3"),
        html.Div(
            [
                dbc.Button(
                    "Download GeoJSON",
                    id="download-geojson-btn",
                    disabled=True,
                    outline=True,
                    color="primary",
                    className="me-2",
                    n_clicks=0,
                ),
                dbc.Button(
                    "Download GeoParquet",
                    id="download-geoparquet-btn",
                    disabled=True,
                    outline=True,
                    color="primary",
                    n_clicks=0,
                ),
                dcc.Download(id="download-geojson"),
                dcc.Download(id="download-geoparquet"),
            ],
            className="mt-3",
        ),
    ]
)

_INFO_CONTENT = html.Div(
    [
        html.Div(
            [
                html.Div(
                    [
                        html.H4("GeoJSON"),
                        html.P(
                            "Building footprint polygons in WGS84 (EPSG:4326). "
                            "Open directly in QGIS, Google Maps, Kepler.gl, or any web map."
                        ),
                    ],
                    className="ops-mini-card",
                ),
                html.Div(
                    [
                        html.H4("GeoParquet 1.1"),
                        html.P(
                            "Columnar spatial analytics with WKB geometry encoding. "
                            "Query with DuckDB, GeoPandas, Snowflake, or BigQuery."
                        ),
                    ],
                    className="ops-mini-card",
                ),
            ],
            className="ops-card-grid",
        )
    ]
)

layout = page_shell(
    active="Exports",
    subtitle="GeoJSON · GeoParquet · building footprint export",
    status="GIS Export Engine",
    canvas_id="gis-exports-cv",
    eyebrow="GeoAI Products",
    title="GIS",
    accent="Exports",
    description=(
        "Generate GeoJSON and GeoParquet building footprint exports "
        "from segmentation prediction outputs."
    ),
    metrics=[
        ("Output Format", "GeoJSON + GeoParquet"),
        ("CRS Output", "WGS84 EPSG:4326"),
        ("Clustering", "DBSCAN"),
        ("B2 Upload", "Optional"),
    ],
    page_class="exports-page",
    children=[
        dcc.Store(id="gis-export-result-store"),
        dcc.Interval(
            id="gis-datasets-refresh",
            interval=60_000,
            max_intervals=-1,
            n_intervals=0,
        ),
        section(
            "GIS Export",
            "Export Configuration",
            "Select a dataset, model, and prediction PLY path to generate building footprint exports.",
            _EXPORT_FORM,
            "ops-panel-primary",
        ),
        html.Div(id="gis-export-run-alert", className="mt-2"),
        html.Div(
            id="gis-export-status-section",
            style={"display": "none"},
            className="ops-panel mt-3",
            children=[
                html.Div("Export Status", className="ops-section-kicker"),
                html.H2("Export Results"),
                html.P("Summary of the completed GIS export run."),
                _STATUS_CONTENT,
            ],
        ),
        section(
            "Format Guide",
            "What These Files Contain",
            "Use GeoJSON for instant web-map inspection; GeoParquet for lakehouse analytics at scale.",
            _INFO_CONTENT,
        ),
    ],
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("dataset-gis-dropdown", "options"),
    Input("gis-datasets-refresh", "n_intervals"),
)
def refresh_gis_datasets(_):
    return _dataset_options()


@callback(
    Output("gis-export-result-store", "data"),
    Output("gis-export-status-section", "style"),
    Output("gis-export-run-alert", "children"),
    Input("run-gis-export-btn", "n_clicks"),
    State("dataset-gis-dropdown", "value"),
    State("prepversion-gis-dropdown", "value"),
    State("model-gis-dropdown", "value"),
    State("run-id-gis-input", "value"),
    State("ply-path-gis-input", "value"),
    State("upload-b2-gis-toggle", "value"),
    prevent_initial_call=True,
)
def run_gis_export(n_clicks, dataset_id, prep_version, model, run_id, ply_path, upload_b2):
    if not n_clicks:
        raise PreventUpdate

    _show = {"display": "block"}

    if not dataset_id:
        return dash.no_update, _show, dbc.Alert("Select a dataset.", color="warning")
    if not run_id:
        return dash.no_update, _show, dbc.Alert("Enter a Run ID.", color="warning")
    if not ply_path:
        return dash.no_update, _show, dbc.Alert("Enter the prediction PLY path.", color="warning")

    from services.gis_export_service import run_gis_export_pipeline

    output_dir = f"data/local_staging/gis_exports/{dataset_id}/{run_id}"
    result = run_gis_export_pipeline(
        dataset_id=dataset_id,
        prep_version=prep_version or "prep_v001",
        model=model or "pointnet2",
        run_id=run_id,
        prediction_ply_path=ply_path,
        output_dir=output_dir,
        upload_to_b2=bool(upload_b2),
    )

    if not result.get("ok"):
        alert = dbc.Alert(
            f"Export failed: {result.get('error')}", color="danger", dismissable=True
        )
    else:
        alert = dbc.Alert(
            f"Export complete — {result['buildings_detected']:,} buildings detected.",
            color="success",
            dismissable=True,
        )

    return result, _show, alert


@callback(
    Output("gis-export-summary-cards", "children"),
    Output("gis-export-b2-table", "children"),
    Output("download-geojson-btn", "disabled"),
    Output("download-geoparquet-btn", "disabled"),
    Input("gis-export-result-store", "data"),
)
def populate_export_results(result):
    if not result:
        return [], html.Div(), True, True

    cards = [
        _summary_card("Buildings Detected", f"{result.get('buildings_detected', 0):,}"),
        _summary_card("Noise Points", f"{result.get('noise_points', 0):,}"),
        _summary_card("GeoJSON Path", result.get("geojson_path") or "n/a"),
        _summary_card("GeoParquet Path", result.get("geoparquet_path") or "n/a"),
    ]

    b2_uploads = result.get("b2_uploads") or []
    if b2_uploads:
        b2_rows = [
            {
                "File": u["local"].split("/")[-1],
                "B2 Path": u["b2_path"],
                "Status": "OK" if u["ok"] else "Failed",
            }
            for u in b2_uploads
        ]
        b2_table = dash_table.DataTable(
            columns=[
                {"name": "File", "id": "File"},
                {"name": "B2 Path", "id": "B2 Path"},
                {"name": "Status", "id": "Status"},
            ],
            data=b2_rows,
            **ops_table_style(),
        )
    else:
        b2_table = html.Div()

    ok = bool(result.get("ok"))
    return cards, b2_table, not ok, not ok


@callback(
    Output("download-geojson", "data"),
    Input("download-geojson-btn", "n_clicks"),
    State("gis-export-result-store", "data"),
    prevent_initial_call=True,
)
def download_geojson(n_clicks, result):
    if not n_clicks or not result or not result.get("geojson_path"):
        raise PreventUpdate
    return dcc.send_file(result["geojson_path"])


@callback(
    Output("download-geoparquet", "data"),
    Input("download-geoparquet-btn", "n_clicks"),
    State("gis-export-result-store", "data"),
    prevent_initial_call=True,
)
def download_geoparquet(n_clicks, result):
    if not n_clicks or not result or not result.get("geoparquet_path"):
        raise PreventUpdate
    return dcc.send_file(result["geoparquet_path"])
