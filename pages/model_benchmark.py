import dash
from dash import html

from components.ops_page_shell import data_table, info_card, page_shell, section
from services.benchmark_service import load_benchmark_summary


dash.register_page(
    __name__,
    path="/model-benchmark",
    name="Model Benchmark",
    title="Model Benchmark - LiDAR Platform",
)


_summary = load_benchmark_summary()


layout = page_shell(
    active="Benchmark",
    subtitle="Model comparison and 3 + 1 research strategy",
    status="Benchmark Shell",
    canvas_id="benchmark-cv",
    eyebrow="Processing & ML",
    title="Model",
    accent="Benchmark",
    description="Compare segmentation models across accuracy, IoU, runtime, memory, and artifact metadata.",
    metrics=[
        ("Best Model", _summary.get("best_model", "Pending")),
        ("Best Building IoU", _summary.get("best_building_iou", "n/a")),
        ("Best mIoU", _summary.get("best_miou", "n/a")),
        ("Fastest Inference", _summary.get("fastest_inference", "n/a")),
    ],
    page_class="benchmark-page",
    children=[
        section(
            "Benchmark Table",
            "Model comparison",
            "This reads results/benchmark_summary.json when available and otherwise renders a safe empty state.",
            data_table(_summary.get("rows"), empty_title="No benchmark runs", empty_detail=_summary.get("message")),
            "ops-panel-primary",
        ),
        section(
            "Strategy",
            "3 + 1 model strategy",
            "Three comparative baselines plus one advanced model keeps the research story balanced and defensible.",
            html.Div(
                [
                    info_card("PointNet++ SSG", "Hierarchical point-based baseline.", "Baseline 1"),
                    info_card("PointNet++ MSG", "Multi-scale grouping baseline for local geometry.", "Baseline 2"),
                    info_card("RandLA-Net", "Efficient large-scale segmentation baseline.", "Baseline 3"),
                    info_card("PointNeXt-XL / PTv3", "PointNeXt-XL is the safer engineering extension; PTv3 is the SOTA research extension.", "Advanced"),
                ],
                className="ops-card-grid",
            ),
        ),
        section(
            "Planned Charts",
            "Per-class IoU and runtime panels",
            "These panels are placeholders until committed metrics and runtime profiles are available.",
            html.Div(
                [
                    info_card("Per-class IoU", "Building and non-building IoU comparison will render here.", "Accuracy"),
                    info_card("Runtime", "Training time, inference time, and memory footprint will render here.", "Efficiency"),
                    info_card("MLflow links", "Run IDs can be attached once benchmark records include MLflow metadata.", "Tracking"),
                ],
                className="ops-card-grid",
            ),
        ),
    ],
)

