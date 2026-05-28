import dash
from dash import html

from components.ops_page_shell import data_table, info_card, page_shell, section


dash.register_page(
    __name__,
    path="/api-integration",
    name="API & Integration",
    title="API & Integration - LiDAR Platform",
)


_endpoint_rows = [
    {"group": "Dataset API", "endpoint": "GET /datasets", "status": "Planned Contract"},
    {"group": "Dataset API", "endpoint": "GET /datasets/{dataset_id}/readiness", "status": "Planned Contract"},
    {"group": "Preprocessing API", "endpoint": "POST /preprocessing/runs", "status": "Planned Contract"},
    {"group": "Training API", "endpoint": "POST /training/runs", "status": "Planned Contract"},
    {"group": "Inference API", "endpoint": "POST /inference/batch", "status": "Planned Contract"},
    {"group": "Export API", "endpoint": "GET /exports/{export_id}", "status": "Planned Contract"},
    {"group": "Monitoring API", "endpoint": "GET /health", "status": "Planned Contract"},
]


layout = page_shell(
    active="API",
    subtitle="Platform integration contracts",
    status="API Contracts",
    canvas_id="api-cv",
    eyebrow="Platform Operations",
    title="API &",
    accent="Integration",
    description="Document platform endpoints for dataset access, preprocessing, training, inference, exports, and monitoring.",
    metrics=[
        ("API Groups", "6"),
        ("Implemented Endpoints", "Browser upload"),
        ("Planned Endpoints", len(_endpoint_rows)),
        ("Integration Status", "Planned"),
    ],
    page_class="api-page",
    children=[
        section(
            "Contracts",
            "Endpoint roadmap",
            "These endpoints are intentionally labeled as planned contracts until the backend API layer is implemented.",
            data_table(_endpoint_rows),
            "ops-panel-primary",
        ),
        section(
            "Payloads",
            "Example integration shapes",
            "No private IPs, credentials, or machine names are exposed in these contract examples.",
            html.Div(
                [
                    info_card("Dataset readiness", '{"dataset_id": "example-dataset"}', "Request"),
                    info_card("Preprocessing run", '{"dataset_id": "example-dataset", "mode": "train"}', "Request"),
                    info_card("Inference export", '{"run_id": "example-run", "format": "GeoParquet"}', "Request"),
                ],
                className="ops-card-grid",
            ),
        ),
        section(
            "Integration",
            "External consumers",
            "The API layer is the bridge between Dash, Airflow, MLflow, GIS exports, and future enterprise integrations.",
            html.Div(
                [
                    info_card("Dash UI", "Human operations console for upload, trigger, and review.", "Client"),
                    info_card("Airflow", "Orchestration backend for preprocessing and training jobs.", "Worker"),
                    info_card("GIS consumers", "GeoJSON, GeoParquet, CityJSON, 3D Tiles, and COPC outputs.", "Output"),
                ],
                className="ops-card-grid",
            ),
        ),
    ],
)

