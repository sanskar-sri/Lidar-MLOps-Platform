"""
pages/home.py
Landing page — served at the root path "/" so visitors no longer
see a blank 404 when they open the app without a route.

Dash picks this up automatically via use_pages=True in app.py.
The canvas point-cloud animation is driven by assets/landing.js
which Dash auto-includes on every page.
"""

import dash
from dash import html, dcc

dash.register_page(__name__, path="/", name="Home")

# ── Pipeline stage card data ───────────────────────────────────────

_STAGES = [
    {
        "num": "Stage 01",
        "tag": "RAW",
        "name": "Bronze Raw Upload",
        "desc": "Upload tiles to bronze_raw_data/source_files/tiles and label maps to source_files/label_maps.",
        "color": "#4fb3ff",
        "bg": "rgba(79,179,255,0.12)",
    },
    {
        "num": "Stage 02",
        "tag": "MAN",
        "name": "Manifests & Checksums",
        "desc": "Create upload_manifest.json and checksum_manifest.json with B2 paths, file sizes, and SHA-1 values.",
        "color": "#3dd6b5",
        "bg": "rgba(61,214,181,0.12)",
    },
    {
        "num": "Stage 03",
        "tag": "META",
        "name": "Metadata Extraction",
        "desc": "Download real B2 tiles, read PLY/LAS fields, parse label maps, and write metadata/datasets.",
        "color": "#f2b84b",
        "bg": "rgba(242,184,75,0.12)",
    },
    {
        "num": "Stage 04",
        "tag": "ANA",
        "name": "Analytics Parquets",
        "desc": "Save file summaries, KPIs, class labels, spatial ranges, and quality checks to metadata_analytics.",
        "color": "#b987ff",
        "bg": "rgba(185,135,255,0.12)",
    },
    {
        "num": "Stage 05",
        "tag": "QA",
        "name": "Dashboard Review",
        "desc": "Inspect registry rows, KPI cards, readiness checks, class mapping, and expected dataset lineage.",
        "color": "#7bd88f",
        "bg": "rgba(123,216,143,0.12)",
    },
    {
        "num": "Stage 06",
        "tag": "RRD",
        "name": "Rerun Recording",
        "desc": "Generate a real .rrd preview from one selected tile using RGB, height, intensity, semantic, or binary color.",
        "color": "#ff6b6b",
        "bg": "rgba(255,107,107,0.12)",
    },
]

# ── Stat bar data ──────────────────────────────────────────────────

_STATS = [
    {"id": "lp-s-p", "label": "Preview point budget", "color": "#4fb3ff"},
    {"id": "lp-s-m", "label": "Rerun color modes", "color": "#3dd6b5"},
    {"id": "lp-s-v", "label": "B2 workflow folders", "color": "#f2b84b"},
    {"id": "lp-s-s", "label": "Pipeline stages", "color": "#b987ff"},
]


def _stage_card(s):
    return html.Div(
        [
            html.Div(s["num"], className="lp-cnum"),
            html.Div(
                s["tag"],
                className="lp-cico",
                style={"background": s["bg"], "color": s["color"]},
            ),
            html.Div(s["name"], className="lp-cname"),
            html.Div(s["desc"], className="lp-cdesc"),
            html.Div(className="lp-cbar", style={"background": s["color"]}),
        ],
        className="lp-card",
    )


def _stat_box(s):
    return html.Div(
        [
            html.Div("0", id=s["id"], className="lp-sv", style={"color": s["color"]}),
            html.Div(s["label"], className="lp-sl"),
        ],
        className="lp-st",
    )


# ── Page layout ────────────────────────────────────────────────────

layout = html.Div(
    id="lp-home",
    className="lp-root",
    children=[
        html.Canvas(id="lp-cv"),

        html.Div(
            className="lp-topbar",
            children=[
                html.Div(
                    className="lp-brand",
                    children=[
                        html.Span(className="lp-brand-dot"),
                        html.Div(
                            [
                                html.Div(
                                    "Building Identification on Mobile LiDAR Data",
                                    className="lp-brand-title",
                                ),
                                html.Div(
                                    "Data Explorer · Preprocessing · Rerun Visualization",
                                    className="lp-brand-subtitle",
                                ),
                            ],
                            className="lp-brand-copy",
                        ),
                    ],
                ),
                dcc.Link(
                    "Data Explorer →",
                    href="/data-explorer",
                    className="lp-top-cta",
                ),
            ],
        ),

        # ── Hero section with animated canvas ─────────────────────
        html.Div(
            className="lp-hero",
            children=[
                # Horizontal scan-line sweep
                html.Div(className="lp-scan"),

                # Text overlay
                html.Div(
                    className="lp-hcnt",
                    children=[
                        # "Pipeline Active" badge
                        html.Div(
                            [html.Span(className="lp-bdot"), "Pipeline Active"],
                            className="lp-badge",
                        ),

                        html.H1(
                            ["Building Identification", html.Br(),
                             html.Em("on Mobile LiDAR Data")],
                            className="lp-h1",
                        ),

                        html.P(
                            ["Upload, register, profile, and visualize 3D point cloud datasets",
                             html.Br(),
                             "for building segmentation and model training."],
                            className="lp-p",
                        ),

                        html.Div(
                            [
                                dcc.Link(
                                    "Open Data Explorer →",
                                    href="/data-explorer",
                                    className="lp-bp",
                                ),
                                html.A(
                                    "About the Pipeline",
                                    href="#lp-pipeline",
                                    className="lp-bg",
                                ),
                            ],
                            className="lp-btns",
                        ),
                    ],
                ),
            ],
        ),

        # ── Stat bar ───────────────────────────────────────────────
        html.Div(
            [_stat_box(s) for s in _STATS],
            className="lp-stats",
        ),

        html.Div(
            className="lp-pipeline",
            id="lp-pipeline",
            children=[
                html.Div(
                    [
                        html.Div("Data Pipeline", className="lp-stl"),
                        html.Div(
                            "bronze_raw_data → metadata → metadata_analytics → dashboard QA → rerun_outputs",
                            className="lp-sts",
                        ),
                    ],
                    className="lp-section-head",
                ),
                html.Div(
                    [_stage_card(s) for s in _STAGES],
                    className="lp-cards",
                ),
            ],
        ),

        # ── Footer ─────────────────────────────────────────────────
        html.Div(
            [
                html.Span(
                    "Backblaze B2 · Open3D · Plotly Dash · Rerun SDK 0.31",
                    className="lp-ftl",
                ),
                html.Span("data_explorer v2", className="lp-ftr"),
            ],
            className="lp-ft",
        ),
    ],
)
