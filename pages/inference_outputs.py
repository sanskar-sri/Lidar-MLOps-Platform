import dash
from dash import html

from components.ops_page_shell import data_table, info_card, page_shell, placeholder_button, section
from services.inference_outputs_service import list_inference_outputs
from services.training_summary_service import list_training_models


dash.register_page(
    __name__,
    path="/inference-outputs",
    name="Inference & Outputs",
    title="Inference & Outputs - LiDAR Platform",
)


_summary = list_inference_outputs()


layout = page_shell(
    active="Inference",
    subtitle="Batch prediction outputs and segmentation manifests",
    status="Inference Shell",
    canvas_id="inference-cv",
    eyebrow="Processing & ML",
    title="Inference",
    accent="Outputs",
    description="Run or inspect batch inference outputs for building / non-building segmentation.",
    metrics=[
        ("Inference Runs", _summary.get("inference_runs", 0)),
        ("Prediction Files", _summary.get("prediction_files", 0)),
        ("Latest Output", _summary.get("latest_output", "Pending")),
    ],
    page_class="inference-page",
    children=[
        section(
            "Configuration",
            "Batch inference shell",
            "The backend is intentionally not triggered in this page-shell phase.",
            html.Div(
                [
                    data_table(list_training_models(), empty_title="No models", empty_detail="No model registry data is available."),
                    html.Div(
                        [
                            info_card("Select Gold dataset", "Gold dataset selection will be wired to the Silver & Gold outputs service.", "Input"),
                            info_card("Select model", "Champion/challenger model selection will be wired after benchmark reports exist.", "Model"),
                            placeholder_button("Run Batch Inference"),
                        ],
                        className="ops-stack",
                    ),
                ],
                className="ops-two-col",
            ),
            "ops-panel-primary",
        ),
        section(
            "Outputs",
            "Prediction manifest",
            "Predicted PLY/LAS/LAZ, confidence summaries, and Rerun scenes will appear here when the inference backend is implemented.",
            data_table(_summary.get("runs"), empty_title="No inference outputs", empty_detail=_summary.get("message")),
        ),
        section(
            "Download Targets",
            "Output products",
            "These are placeholder contracts for downstream pages and export services.",
            html.Div(
                [
                    info_card("Predicted point cloud", "PLY/LAS/LAZ prediction export placeholder.", "Point Cloud"),
                    info_card("Confidence summary", "Mean, p10, p90, and low-confidence review queue placeholder.", "Quality"),
                    info_card("Open in Rerun", "Rerun scene launch hook placeholder.", "3D QA"),
                ],
                className="ops-card-grid",
            ),
        ),
    ],
)

