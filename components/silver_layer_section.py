import math

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import dash_table, dcc, html

from components.platform_theme import empty_state, small_status
from services.preprocessing_runtime_service import (
    SILVER_DENSITY_REQUIRED_COLUMNS,
    compute_silver_readiness,
    load_local_or_b2_silver_metadata,
)


def _table_style():
    return {
        "style_table": {
            "overflowX": "auto",
            "width": "100%",
            "border": "1px solid rgba(125, 180, 255, 0.18)",
            "borderRadius": "8px",
        },
        "style_cell": {
            "textAlign": "left",
            "padding": "9px",
            "fontFamily": "Arial",
            "fontSize": "12px",
            "whiteSpace": "normal",
            "height": "auto",
            "backgroundColor": "#0b111b",
            "color": "#edf2f7",
            "border": "1px solid rgba(125, 180, 255, 0.14)",
        },
        "style_header": {
            "fontWeight": "bold",
            "backgroundColor": "#111827",
            "color": "#edf2f7",
            "border": "1px solid rgba(125, 180, 255, 0.18)",
        },
    }


def _format_number(value):
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number):
        return "n/a"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.3f}"


def _metric_card(label, value, detail=""):
    return html.Div(
        [
            html.Div(label, className="silver-metric-label"),
            html.Div(value, className="silver-metric-value"),
            html.Div(detail, className="silver-metric-detail"),
        ],
        className="silver-metric-card",
    )


def _availability_item(label, available):
    tone = "ok" if bool(available) else "missing"
    return html.Div(
        [
            html.Span(className=f"silver-availability-dot silver-availability-dot-{tone}"),
            html.Span(label, className="silver-availability-label"),
            html.Span("available" if available else "missing", className=f"silver-availability-status silver-availability-status-{tone}"),
        ],
        className="silver-availability-item",
    )


def _blank_figure(title, message):
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title=title,
        annotations=[
            {
                "text": message,
                "showarrow": False,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "font": {"color": "#9aa9bd", "size": 13},
            }
        ],
        margin={"l": 30, "r": 18, "t": 46, "b": 28},
        height=320,
    )
    return fig


def _density_heatmap(density_df):
    if density_df is None:
        return _blank_figure("Density heatmap", "silver_density_grid.parquet is not available.")
    required = {"cx", "cy", "total_pts"}
    missing = required - set(density_df.columns)
    if missing:
        return _blank_figure("Density heatmap", f"Missing columns: {', '.join(sorted(missing))}")

    pivot = density_df.pivot_table(
        index="cy",
        columns="cx",
        values="total_pts",
        aggfunc="sum",
        fill_value=0,
    )
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=list(pivot.columns),
            y=list(pivot.index),
            colorscale="Tealgrn",
            colorbar={"title": "points"},
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title="Density heatmap",
        margin={"l": 42, "r": 18, "t": 46, "b": 36},
        height=340,
        xaxis_title="cx",
        yaxis_title="cy",
    )
    return fig


def _density_histogram(density_df):
    if density_df is None or "total_pts" not in getattr(density_df, "columns", []):
        return _blank_figure("Density histogram", "total_pts is not available.")
    fig = go.Figure(
        data=go.Histogram(
            x=density_df["total_pts"],
            nbinsx=40,
            marker={"color": "#61b8ff", "line": {"color": "rgba(255,255,255,0.18)", "width": 1}},
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title="Density histogram",
        margin={"l": 42, "r": 18, "t": 46, "b": 36},
        height=340,
        xaxis_title="points per grid cell",
        yaxis_title="cells",
    )
    return fig


def _class_distribution_rows(stats):
    distribution = (stats or {}).get("class_distribution") or (stats or {}).get("label_distribution")
    if not distribution:
        return []
    if isinstance(distribution, dict):
        labels = distribution.get("labels")
        names = distribution.get("names")
        counts = distribution.get("counts")
        if isinstance(counts, list):
            rows = []
            for index, count in enumerate(counts):
                label = None
                if isinstance(names, list) and index < len(names):
                    label = names[index]
                elif isinstance(labels, list) and index < len(labels):
                    label = labels[index]
                rows.append({"class": str(label if label is not None else index), "points": count})
            return rows
        return [
            {"class": str(label), "points": count}
            for label, count in distribution.items()
        ]
    if isinstance(distribution, list):
        rows = []
        for item in distribution:
            if not isinstance(item, dict):
                continue
            label = item.get("class") or item.get("class_id") or item.get("label") or item.get("name")
            count = item.get("points") or item.get("point_count") or item.get("count")
            rows.append({"class": str(label), "points": count})
        return rows
    return []


def _class_distribution_chart(stats):
    rows = _class_distribution_rows(stats)
    if not rows:
        return _blank_figure("Class distribution", "class_distribution or label_distribution is not present in silver_stats.json.")
    fig = go.Figure(data=go.Pie(labels=[row["class"] for row in rows], values=[row["points"] for row in rows], hole=0.45))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title="Class distribution",
        margin={"l": 20, "r": 20, "t": 46, "b": 20},
        height=330,
    )
    return fig


def _binary_building_chart(stats, density_df):
    building = None
    non_building = None

    if stats:
        binary = stats.get("binary_distribution") or stats.get("building_distribution") or {}
        building = binary.get("building_pts") or binary.get("building") or stats.get("building_pts")
        non_building = binary.get("non_building_pts") or binary.get("non_building") or stats.get("non_building_pts")

    if (building is None or non_building is None) and density_df is not None:
        columns = set(density_df.columns)
        if {"building_pts", "non_building_pts"}.issubset(columns):
            building = density_df["building_pts"].sum()
            non_building = density_df["non_building_pts"].sum()

    if building is None or non_building is None:
        return _blank_figure("Building vs non-building", "building_pts and non_building_pts are not available.")

    fig = go.Figure(
        data=go.Bar(
            x=["building", "non-building"],
            y=[building, non_building],
            marker={"color": ["#55e2a7", "#61b8ff"]},
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title="Building vs non-building",
        margin={"l": 42, "r": 18, "t": 46, "b": 36},
        height=330,
        yaxis_title="points",
    )
    return fig


def _density_metrics(density_df):
    if density_df is None or "total_pts" not in getattr(density_df, "columns", []):
        return [
            _metric_card("Active grid cells", "n/a", "density grid missing"),
            _metric_card("Coverage", "n/a", "density grid missing"),
            _metric_card("Max points / cell", "n/a", "density grid missing"),
            _metric_card("Avg active density", "n/a", "density grid missing"),
        ]

    total_cells = len(density_df)
    active_cells = int((density_df["total_pts"] > 0).sum())
    coverage = (active_cells / total_cells * 100) if total_cells else 0
    max_pts = density_df["total_pts"].max() if total_cells else 0
    avg_pts = density_df.loc[density_df["total_pts"] > 0, "total_pts"].mean() if active_cells else 0
    return [
        _metric_card("Active grid cells", _format_number(active_cells), f"{_format_number(total_cells)} total cells"),
        _metric_card("Coverage", f"{coverage:.1f}%", "active cells / total cells"),
        _metric_card("Max points / cell", _format_number(max_pts), "peak density"),
        _metric_card("Avg active density", _format_number(avg_pts), "points per active cell"),
    ]


def _bbox_summary(metadata):
    bbox_min = metadata.get("bbox_min") or []
    bbox_max = metadata.get("bbox_max") or []
    rows = []
    axes = ["X", "Y", "Z"]
    for index, axis in enumerate(axes):
        min_value = bbox_min[index] if index < len(bbox_min) else None
        max_value = bbox_max[index] if index < len(bbox_max) else None
        extent = None
        try:
            extent = float(max_value) - float(min_value)
        except (TypeError, ValueError):
            pass
        rows.append(
            {
                "axis": axis,
                "min": _format_number(min_value),
                "max": _format_number(max_value),
                "extent": _format_number(extent),
            }
        )
    return rows


def _verification_table(verification):
    rows = (verification or {}).get("rows") or []
    if not rows:
        return empty_state("Silver verification pending", "Run B2 verification after Airflow succeeds or after selecting an existing dataset/prep version.")
    return dash_table.DataTable(
        columns=[
            {"name": "Artifact", "id": "artifact"},
            {"name": "Status", "id": "status"},
            {"name": "Size", "id": "size_display"},
            {"name": "B2 key", "id": "b2_key"},
        ],
        data=rows,
        page_size=8,
        **_table_style(),
    )


def build_silver_layer_section(dataset_id, prep_version, b2_prefix, verification=None, silver_payload=None):
    if not dataset_id:
        return empty_state("Silver Layer analytics", "Select a dataset to load real Silver metadata and density outputs.")

    payload = silver_payload or load_local_or_b2_silver_metadata(dataset_id, b2_prefix)
    metadata = payload.get("metadata") or {}
    stats = payload.get("stats") or {}
    density_df = payload.get("density_df")
    errors = payload.get("errors") or {}
    readiness = compute_silver_readiness(verification, payload)

    warnings = [message for message in errors.values() if message]
    density_columns = set(list(getattr(density_df, "columns", []))) if density_df is not None else set()
    optional_supported = sorted(density_columns & SILVER_DENSITY_REQUIRED_COLUMNS)

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Silver Layer", className="ops-section-kicker"),
                            html.H2("Verified Silver Analytics"),
                            html.P("All charts below are built from processed_cloud_meta.json, silver_stats.json, and silver_density_grid.parquet. Missing data stays visible as a warning."),
                        ],
                        className="ops-section-head",
                    ),
                    html.Div(
                        [
                            small_status("Silver readiness", readiness["status"]),
                            small_status("B2 upload", (verification or {}).get("status", "pending")),
                        ],
                        className="silver-status-row",
                    ),
                ],
                className="silver-section-head",
            ),
            html.Div(
                [
                    _metric_card("Dataset", metadata.get("dataset") or dataset_id, "processed_cloud_meta.json"),
                    _metric_card("Processed points", _format_number(metadata.get("num_points")), "voxelized points"),
                    _metric_card("Voxel size", metadata.get("voxel_size", "n/a"), metadata.get("voxel_keep_strategy", "strategy n/a")),
                    _metric_card("Pipeline", metadata.get("pipeline_version") or "n/a", metadata.get("prep_version") or prep_version),
                    _metric_card("B2 upload", (verification or {}).get("status", "pending"), f"{(verification or {}).get('verified_count', 0)}/{(verification or {}).get('expected_count', 0)} files"),
                    _metric_card("Gold readiness", readiness["status"], "computed from real checks"),
                ],
                className="silver-metric-grid",
            ),
            dbc.Alert(
                [html.Div(message) for message in warnings],
                color="warning",
                className="silver-warning",
                is_open=bool(warnings),
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Attribute Availability"),
                            html.Div(
                                [
                                    _availability_item("XYZ", True),
                                    _availability_item("intensity", metadata.get("has_intensity")),
                                    _availability_item("RGB", metadata.get("has_rgb")),
                                    _availability_item("labels", metadata.get("has_labels")),
                                    _availability_item("normals", metadata.get("has_normals")),
                                    _availability_item("density", metadata.get("has_density")),
                                ],
                                className="silver-availability-grid",
                            ),
                        ],
                        className="ops-review-card silver-card",
                    ),
                    html.Div(
                        [
                            html.H3("Coordinate Offset"),
                            html.Code(str(metadata.get("coord_offset_subtracted") or "not available")),
                            html.H3("Spatial Extent", className="silver-subtitle"),
                            dash_table.DataTable(
                                columns=[
                                    {"name": "Axis", "id": "axis"},
                                    {"name": "Min", "id": "min"},
                                    {"name": "Max", "id": "max"},
                                    {"name": "Extent", "id": "extent"},
                                ],
                                data=_bbox_summary(metadata),
                                page_size=3,
                                **_table_style(),
                            ),
                        ],
                        className="ops-review-card silver-card",
                    ),
                ],
                className="silver-two-col",
            ),
            html.Div(_density_metrics(density_df), className="silver-metric-grid silver-density-metrics"),
            html.Div(
                [
                    dcc.Graph(figure=_density_heatmap(density_df), config={"displayModeBar": False}, className="silver-chart"),
                    dcc.Graph(figure=_density_histogram(density_df), config={"displayModeBar": False}, className="silver-chart"),
                ],
                className="silver-two-col",
            ),
            html.Div(
                [
                    dcc.Graph(figure=_class_distribution_chart(stats), config={"displayModeBar": False}, className="silver-chart"),
                    dcc.Graph(figure=_binary_building_chart(stats, density_df), config={"displayModeBar": False}, className="silver-chart"),
                ],
                className="silver-two-col",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Generated Silver Files"),
                            _verification_table(verification),
                        ],
                        className="ops-review-card silver-card",
                    ),
                    html.Div(
                        [
                            html.H3("Gold Readiness Check"),
                            html.Div(
                                readiness["status"],
                                className=f"silver-readiness silver-readiness-{readiness['status']}",
                            ),
                            html.Ul([html.Li(item) for item in readiness["failed_checks"]])
                            if readiness["failed_checks"]
                            else html.Div("All required Silver checks passed.", className="silver-pass-copy"),
                            html.Div(
                                "Density columns available: " + (", ".join(optional_supported) or "none"),
                                className="silver-source-copy",
                            ),
                        ],
                        className="ops-review-card silver-card",
                    ),
                ],
                className="silver-two-col",
            ),
        ],
        className="silver-layer-section ops-panel",
    )
