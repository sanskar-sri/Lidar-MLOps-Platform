import dash
import dash_bootstrap_components as dbc
from dash import dcc, html


dash.register_page(__name__, path="/postprocessing", name="Postprocessing")


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
        html.Header(
            [
                html.Div(
                    [
                        html.Div(className="ops-brand-mark"),
                        html.Div(
                            [
                                html.Div("LiDAR Platform", className="ops-brand-title"),
                                html.Div("Segmentation refinement and geometric QA", className="ops-brand-subtitle"),
                            ]
                        ),
                    ],
                    className="ops-brand",
                ),
                _ops_nav("Postprocessing"),
                html.Div("RANSAC Planned", className="ops-live-pill"),
            ],
            className="ops-topbar",
        ),
        html.Section(
            [
                html.Canvas(id="postprocessing-cv", className="ops-hero-canvas"),
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
                                    className="post-readiness-card",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.H3("Artifact Contract"),
                                                html.Code("b2://<bucket>/clustered_final_outputs/<dataset>/<prep_version>/<model>/<run_id>/"),
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
                                dbc.Button("Prepare RANSAC Job", color="success", size="lg", disabled=True, className="ops-primary-action"),
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
