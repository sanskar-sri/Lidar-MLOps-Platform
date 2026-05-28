import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dcc, html

from components.lidar_particle_background import lidar_particle_background
from components.platform_header import platform_header
from services.dataset_selection import resolve_selected_dataset_id
from services.silver_gold_outputs_service import get_segmentation_output_summary


dash.register_page(__name__, path="/postprocessing", name="Postprocessing")


REFRESH_INTERVAL_MS = 60_000


def _hero_metric(label, value):
    return html.Div(
        [html.Div(value, className="ops-hero-metric-value"), html.Div(label, className="ops-hero-metric-label")],
        className="ops-hero-metric",
    )


def _stage(index, title, detail, tone):
    return html.Div(
        [
            html.Div(index, className=f"ops-step-index ops-step-index-{tone}"),
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


def _readiness_row(label, value, tone="info"):
    return html.Div(
        [
            html.Span(label, className="post-readiness-label"),
            html.Span(value, className=f"post-readiness-value post-readiness-value-{tone}"),
        ],
        className="post-readiness-row",
    )


layout = html.Div(
    className="post-page ops-page",
    children=[
        dcc.Interval(
            id="postprocessing-refresh",
            interval=REFRESH_INTERVAL_MS,
            n_intervals=0,
        ),
        platform_header(
            active_path="/postprocessing",
            brand_subtitle="Segmentation refinement and geometric QA",
            status_label="RANSAC Planned",
            visual_context="ops",
        ),
        html.Section(
            [
                lidar_particle_background("postprocessing-cv", class_name="ops-hero-canvas"),
                html.Div(className="ops-hero-shade"),
                html.Div(
                    [
                        html.Div("Future Refinement Stage", className="ops-eyebrow"),
                        html.H1(["Postprocessing", html.Br(), html.Em("Lab")]),
                        html.P(
                            "Prepare the clustering and geometric-refinement workspace that will consume trained segmentation outputs and promote building instances into QA-ready artifacts."
                        ),
                        html.Div(
                            [
                                _hero_metric("Method", "RANSAC"),
                                _hero_metric("Input", "Model masks"),
                                _hero_metric("Output", "Clusters"),
                            ],
                            className="ops-hero-metrics",
                        ),
                    ],
                    className="ops-hero-copy",
                ),
            ],
            className="ops-hero ops-hero-post",
        ),
        html.Main(
            [
                html.Div(
                    [
                        _stage("01", "Training Output", "Select segmentation masks and confidence fields", "blue"),
                        _stage("02", "Cluster Fit", "Run RANSAC planes, facades, and roof candidates", "green"),
                        _stage("03", "Spatial QA", "Inspect outliers, residuals, and final geometry", "purple"),
                        _stage("04", "Publish", "Write clustered_final_outputs and visual QA", "amber"),
                    ],
                    className="ops-stepper",
                ),
                html.Div(
                    [
                        html.Div(id="postprocessing-context", className="mb-3"),
                        html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div("Configuration", className="ops-section-kicker"),
                                        html.H2("RANSAC Clustering Recipe"),
                                        html.P("RANSAC parameters are staged around the future training-output contract."),
                                    ],
                                    className="ops-section-head",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dbc.Label("Training Run"),
                                                dcc.Dropdown(
                                                    id="post-training-run-dropdown",
                                                    options=[],
                                                    placeholder="Waiting for completed training runs",
                                                    clearable=True,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Prediction Source"),
                                                dcc.Dropdown(
                                                    options=[
                                                        {"label": "B2 segmentation output", "value": "b2"},
                                                        {"label": "Local artifact cache", "value": "local"},
                                                        {"label": "Rerun selection", "value": "rerun"},
                                                    ],
                                                    value="b2",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Cluster Target"),
                                                dcc.Dropdown(
                                                    options=[
                                                        {"label": "Building surfaces", "value": "building_surfaces"},
                                                        {"label": "Facade planes", "value": "facades"},
                                                        {"label": "Roof candidates", "value": "roofs"},
                                                    ],
                                                    value="building_surfaces",
                                                    clearable=False,
                                                ),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Residual Threshold"),
                                                dcc.Slider(0.02, 0.5, 0.02, value=0.12, tooltip={"placement": "bottom", "always_visible": False}),
                                            ],
                                            className="ops-field ops-slider-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Min Points"),
                                                dbc.Input(type="number", value=320, min=16, step=16),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Max Trials"),
                                                dbc.Input(type="number", value=600, min=50, step=50),
                                            ],
                                            className="ops-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Confidence Floor"),
                                                dcc.Slider(0.1, 0.95, 0.05, value=0.65, tooltip={"placement": "bottom", "always_visible": False}),
                                            ],
                                            className="ops-field ops-slider-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("QA Outputs"),
                                                dbc.Checklist(
                                                    options=[
                                                        {"label": "Cluster residual map", "value": "residual_map"},
                                                        {"label": "Outlier audit", "value": "outlier_audit"},
                                                        {"label": "Rerun scene", "value": "rerun_scene"},
                                                        {"label": "B2 publish", "value": "b2_publish"},
                                                    ],
                                                    value=["residual_map", "outlier_audit", "rerun_scene"],
                                                    inline=True,
                                                    switch=True,
                                                ),
                                            ],
                                            className="ops-field ops-field-wide",
                                        ),
                                    ],
                                    className="ops-field-grid",
                                ),
                            ],
                            className="ops-panel ops-panel-primary",
                        ),
                        html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div("Readiness", className="ops-section-kicker"),
                                        html.H2("Cluster Job Preview"),
                                        html.P("Completed training runs will populate this execution summary."),
                                    ],
                                    className="ops-section-head",
                                ),
                                html.Div(
                                    [
                                        _readiness_row("Training output", "Pending"),
                                        _readiness_row("Segmentation mask", "Pending"),
                                        _readiness_row("RANSAC parameters", "Ready", "ok"),
                                        _readiness_row("Publish target", "clustered_final_outputs"),
                                    ],
                                    id="post-readiness-card",
                                    className="post-readiness-card",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.H3("Artifact Contract"),
                                                html.Code(
                                                    "03_segmentation/segmentation_outputs/<dataset_id>/<prep_version>/<model_name>/<run_id>/",
                                                    id="post-artifact-contract",
                                                ),
                                            ],
                                            className="ops-review-card",
                                        ),
                                        html.Div(
                                            [
                                                html.H3("QA Scene"),
                                                html.Code("rerun://postprocessing/<dataset>/<run_id>/ransac_clusters"),
                                            ],
                                            className="ops-review-card",
                                        ),
                                    ],
                                    className="ops-review-grid",
                                ),
                                dbc.Button(
                                    "Prepare RANSAC Job",
                                    id="post-prepare-button",
                                    color="success",
                                    size="lg",
                                    disabled=True,
                                    className="ops-primary-action",
                                ),
                            ],
                            className="ops-panel ops-panel-review",
                        ),
                    ],
                    className="ops-stack",
                ),
            ],
            className="ops-workspace",
        ),
    ],
)


@callback(
    Output("postprocessing-context", "children"),
    Output("post-training-run-dropdown", "options"),
    Output("post-readiness-card", "children"),
    Output("post-artifact-contract", "children"),
    Output("post-prepare-button", "disabled"),
    Output("post-prepare-button", "children"),
    Input("selected-dataset-id", "data"),
    Input("url", "search"),
    Input("postprocessing-refresh", "n_intervals"),
)
def update_postprocessing_context(selected_dataset_id, search, _ticks):
    dataset_id = resolve_selected_dataset_id(search, selected_dataset_id)
    if not dataset_id:
        return (
            dbc.Alert("Please select a dataset first.", color="info", className="mb-0"),
            [],
            [
                _readiness_row("Dataset", "Missing", "warn"),
                _readiness_row("Training output", "Pending"),
                _readiness_row("Segmentation mask", "Pending"),
                _readiness_row("Publish target", "clustered_final_outputs"),
            ],
            "03_segmentation/segmentation_outputs/<dataset_id>/<prep_version>/<model_name>/<run_id>/",
            True,
            "Prepare RANSAC Job",
        )

    summary = get_segmentation_output_summary(dataset_id)
    if not summary.get("exists"):
        detail = summary.get("message") or "Dataset selected, but no segmentation output has been generated yet."
        if summary.get("error"):
            detail = f"{detail} Last B2 check: {summary['error']}"
        return (
            dbc.Alert(detail, color="warning", className="mb-0"),
            [],
            [
                _readiness_row("Dataset", dataset_id, "ok"),
                _readiness_row("Training output", "Pending"),
                _readiness_row("Segmentation mask", "Missing", "warn"),
                _readiness_row("Publish target", "clustered_final_outputs"),
            ],
            summary.get("prefix")
            or "03_segmentation/segmentation_outputs/<dataset_id>/<prep_version>/<model_name>/<run_id>/",
            True,
            "Waiting for segmentation",
        )

    rows = summary.get("rows") or []
    options = [
        {
            "label": f"{row['prep_version']} / {row['model_name']} / {row['run_id']} ({row['files']} files)",
            "value": row["prefix"],
        }
        for row in rows
    ]
    first_prefix = rows[0]["prefix"] if rows else summary.get("prefix")
    return (
        dbc.Alert(
            f"Segmentation output found for dataset '{dataset_id}'.",
            color="success",
            className="mb-0",
        ),
        options,
        [
            _readiness_row("Dataset", dataset_id, "ok"),
            _readiness_row("Training output", "Available", "ok"),
            _readiness_row("Segmentation mask", f"{summary.get('file_count', 0)} files", "ok"),
            _readiness_row("Publish target", "04_clustering/clustered_final_outputs"),
        ],
        first_prefix,
        False,
        "Prepare RANSAC Job",
    )
