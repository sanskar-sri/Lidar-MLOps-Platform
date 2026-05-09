import json as _json
import os
import shutil

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, State, callback
import plotly.graph_objects as go

from components.upload_panel import upload_raw_data_panel
from components.registry_panel import dataset_registry_panel
from components.analytics_panels import (
    kpi_section,
    attribute_analytics_panel,
    label_distribution_panel,
    spatial_summary_panel,
    rerun_viewer_panel,
)
from components.readiness_panel import (
    preprocessing_readiness_panel,
    model_compatibility_panel,
)
from components.lineage_panel import dataset_lineage_panel

from services.metadata_service import (
    list_registered_datasets,
    load_dataset_metadata,
    generate_dataset_metadata_and_analytics,
)

from services.parquet_service import (
    load_file_summary,
    load_dashboard_kpis,
    load_attribute_summary,
    load_label_distribution,
    load_class_label_distribution,
    load_spatial_summary,
    load_class_mapping_summary,
)

from services.b2_service import (
    upload_file_to_b2,
    upload_large_file_to_b2,
    upload_folder_to_b2,
    delete_b2_prefix,
    delete_b2_file_by_name,
)

from services.upload_progress import (
    load_upload_progress,
    update_metadata_progress,
    mark_upload_completed,
    mark_upload_failed,
)
from services.rerun_service import generate_rerun_preview


dash.register_page(__name__, path="/data-explorer", name="Data Explorer")


CHART_COLORWAY = [
    "#4fb3ff",
    "#3dd6b5",
    "#f2b84b",
    "#ff6b6b",
    "#b987ff",
    "#7bd88f",
    "#bde7ff",
    "#9eeadf",
    "#ffe6aa",
]

BINARY_LABEL_COLORS = {
    "Non-building": "#4fb3ff",
    "Building": "#ff4d4f",
}

SEMANTIC_CLASS_COLORS = {
    "Ground": "#4fb3ff",
    "Building": "#ff4d4f",
    "Natural": "#f2b84b",
    "Car": "#ff9f43",
    "Unclassified": "#b987ff",
    "Road_markings": "#7bd88f",
    "Pole": "#bde7ff",
    "Utility_line": "#9eeadf",
    "Fence": "#ffe6aa",
}


def finalize_uploaded_dataset(
    dataset_id,
    dataset_name,
    upload_mode,
    description,
    point_cloud_filenames,
    upload_results,
):
    update_metadata_progress(
        dataset_id,
        message="Generating metadata and analytics from uploaded files",
        percentage=88,
    )

    generate_dataset_metadata_and_analytics(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        upload_mode=upload_mode,
        description=description,
        filenames=point_cloud_filenames,
        uploaded_files=upload_results,
    )

    mark_upload_completed(dataset_id)


def mark_upload_failed_safely(dataset_id, error):
    try:
        if dataset_id:
            mark_upload_failed(dataset_id, str(error))
    except Exception as progress_error:
        print(f"[UPLOAD PROGRESS ERROR] {progress_error}")


def apply_dark_figure_theme(fig):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#14191e",
        font={"color": "#dbe4ee"},
        title_font={"color": "#edf2f7", "size": 18},
        legend={
            "bgcolor": "rgba(0,0,0,0)",
            "font": {"color": "#dbe4ee"},
        },
        colorway=CHART_COLORWAY,
        margin={"l": 42, "r": 24, "t": 58, "b": 42},
    )
    fig.update_xaxes(
        gridcolor="#2b353f",
        zerolinecolor="#3a4650",
        linecolor="#3a4650",
        tickfont={"color": "#b8c3cf"},
        title_font={"color": "#dbe4ee"},
    )
    fig.update_yaxes(
        gridcolor="#2b353f",
        zerolinecolor="#3a4650",
        linecolor="#3a4650",
        tickfont={"color": "#b8c3cf"},
        title_font={"color": "#dbe4ee"},
    )
    return fig


def empty_figure(title):
    return go.Figure(layout={"title": title})


def bar_figure(data_frame, x, y, title, labels=None, color=None, color_map=None):
    labels = labels or {}
    color_map = color_map or {}
    fig = go.Figure()

    if color and color in data_frame.columns:
        grouped_values = [
            value for value in color_map.keys() if value in set(data_frame[color].dropna())
        ]
        grouped_values.extend(
            value
            for value in data_frame[color].dropna().unique()
            if value not in grouped_values
        )

        if data_frame[color].isna().any():
            grouped_values.append(None)

        for color_value in grouped_values:
            if color_value is None:
                group = data_frame[data_frame[color].isna()]
            else:
                group = data_frame[data_frame[color] == color_value]

            if group.empty:
                continue

            fig.add_bar(
                x=group[x],
                y=group[y],
                name=str(color_value) if color_value not in (None, "") else "Unspecified",
                marker_color=color_map.get(color_value),
            )
    else:
        fig.add_bar(x=data_frame[x], y=data_frame[y], name=labels.get(y, y))

    fig.update_layout(
        title=title,
        xaxis_title=labels.get(x, x),
        yaxis_title=labels.get(y, y),
        legend_title_text=labels.get(color, color) if color else None,
    )
    return fig


def pie_figure(data_frame, names, values, title, color_map=None):
    color_map = color_map or {}
    marker_colors = [
        color_map.get(name)
        for name in data_frame[names]
    ]

    return go.Figure(
        data=[
            go.Pie(
                labels=data_frame[names],
                values=data_frame[values],
                hole=0.32,
                marker={"colors": marker_colors} if any(marker_colors) else None,
            )
        ],
        layout={"title": title},
    )


# -------------------------------------------------------------------
# Page Layout
# -------------------------------------------------------------------

layout = dbc.Container(
    fluid=True,
    children=[
        html.H2("Data Explorer"),
        html.P(
            "Upload, register, inspect, profile, visualize, and validate raw mobile LiDAR point cloud datasets."
        ),

        dcc.Store(id="selected-dataset-id"),
        dcc.Store(id="upload-status-store"),

        dbc.Row(
            [
                dbc.Col(upload_raw_data_panel(), width=12),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(dataset_registry_panel(), width=12),
            ],
            className="mb-4",
        ),

        html.Hr(),

        dbc.Row(
            [
                dbc.Col(html.Div(id="kpi-cards-container"), width=12),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(attribute_analytics_panel(), width=12),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(label_distribution_panel(), width=12),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(spatial_summary_panel(), width=12),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(rerun_viewer_panel(), width=12),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(preprocessing_readiness_panel(), width=6),
                dbc.Col(model_compatibility_panel(), width=6),
            ],
            className="mb-4",
        ),

        dbc.Row(
            [
                dbc.Col(dataset_lineage_panel(), width=12),
            ],
            className="mb-4",
        ),
    ],
)


# -------------------------------------------------------------------
# 1. Refresh Dataset Registry
# -------------------------------------------------------------------

@callback(
    Output("dataset-registry-table", "data"),
    Input("upload-status-store", "data"),
)
def refresh_dataset_registry(_):
    try:
        return list_registered_datasets()
    except Exception as e:
        print(f"[REGISTRY ERROR] {e}")
        return []


# -------------------------------------------------------------------
# 2. Select Dataset From Registry
# -------------------------------------------------------------------

@callback(
    Output("selected-dataset-id", "data"),
    Input("dataset-registry-table", "selected_rows"),
    State("dataset-registry-table", "data"),
)
def select_dataset(selected_rows, table_data):
    if not selected_rows or not table_data:
        return None

    row_index = selected_rows[0]

    if row_index is None or row_index >= len(table_data):
        return None

    dataset_id = table_data[row_index].get("dataset_id")

    print("=" * 80)
    print("[DATASET SELECTED]")
    print(f"Selected dataset_id: {dataset_id}")
    print("=" * 80)

    return dataset_id


# -------------------------------------------------------------------
# 3. Small File Upload Callback
# -------------------------------------------------------------------

@callback(
    Output("upload-status-store", "data"),
    Output("upload-message", "children"),
    Output("upload-result-details", "children"),
    Input("upload-button", "n_clicks"),
    State("dataset-id-input", "value"),
    State("dataset-name-input", "value"),
    State("upload-mode-dropdown", "value"),
    State("dataset-description-input", "value"),
    State("raw-file-upload", "contents"),
    State("raw-file-upload", "filename"),
    prevent_initial_call=True,
)
def handle_upload(
    n_clicks,
    dataset_id,
    dataset_name,
    upload_mode,
    description,
    contents,
    filenames,
):
    if not dataset_id:
        return None, dbc.Alert("Please enter a Dataset ID.", color="danger"), ""

    dataset_id = dataset_id.strip()

    if not dataset_name:
        dataset_name = dataset_id

    if not contents:
        return (
            None,
            dbc.Alert(
                "Please select at least one file using the upload box above.",
                color="danger",
            ),
            "",
        )

    if isinstance(contents, str):
        contents = [contents]
        filenames = [filenames]

    try:
        upload_results = []

        for file_content, filename in zip(contents, filenames):
            result = upload_file_to_b2(
                dataset_id=dataset_id,
                filename=filename,
                file_content_base64=file_content,
            )
            upload_results.append(result)

        point_cloud_filenames = [
            item["filename"]
            for item in upload_results
            if item.get("file_role") == "point_cloud_tile"
        ]

        finalize_uploaded_dataset(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            upload_mode=upload_mode,
            description=description,
            point_cloud_filenames=point_cloud_filenames,
            upload_results=upload_results,
        )

        result_card = build_upload_result_card(
            title="Small File Upload Confirmation",
            upload_results=upload_results,
        )

        return (
            {"status": "uploaded", "dataset_id": dataset_id},
            dbc.Alert(
                f"Small file upload completed successfully for dataset: {dataset_id}",
                color="success",
            ),
            result_card,
        )

    except Exception as e:
        print(f"[SMALL UPLOAD ERROR] {e}")
        mark_upload_failed_safely(dataset_id, e)
        return (
            {"status": "failed", "dataset_id": dataset_id},
            dbc.Alert(f"Small file upload failed: {str(e)}", color="danger"),
            "",
        )


# -------------------------------------------------------------------
# 4. Large Single Tile + Optional Label Map Upload
# -------------------------------------------------------------------

@callback(
    Output("upload-status-store", "data", allow_duplicate=True),
    Output("upload-message", "children", allow_duplicate=True),
    Output("upload-result-details", "children", allow_duplicate=True),
    Input("large-file-upload-button", "n_clicks"),
    State("dataset-id-input", "value"),
    State("dataset-name-input", "value"),
    State("upload-mode-dropdown", "value"),
    State("dataset-description-input", "value"),
    State("local-file-path-input", "value"),
    State("local-label-map-path-input", "value"),
    prevent_initial_call=True,
)
def handle_large_file_upload(
    n_clicks,
    dataset_id,
    dataset_name,
    upload_mode,
    description,
    local_file_path,
    local_label_map_path,
):
    if not dataset_id:
        return None, dbc.Alert("Please enter a Dataset ID.", color="danger"), ""

    dataset_id = dataset_id.strip()

    if not dataset_name:
        dataset_name = dataset_id

    if not local_file_path:
        return None, dbc.Alert("Please enter a local tile path.", color="danger"), ""

    try:
        upload_results = []
        local_upload_paths = [local_file_path]

        if local_label_map_path and local_label_map_path.strip():
            local_upload_paths.append(local_label_map_path)

        total_upload_files = len(local_upload_paths)

        for file_index, path in enumerate(local_upload_paths, start=1):
            result = upload_large_file_to_b2(
                dataset_id=dataset_id,
                local_file_path=path,
                total_files=total_upload_files,
                file_index=file_index,
                reset_progress=file_index == 1,
                complete_progress=False,
            )
            upload_results.append(result)

        point_cloud_filenames = [
            item["filename"]
            for item in upload_results
            if item.get("file_role") == "point_cloud_tile"
        ]

        finalize_uploaded_dataset(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            upload_mode=upload_mode,
            description=description,
            point_cloud_filenames=point_cloud_filenames,
            upload_results=upload_results,
        )

        result_card = build_upload_result_card(
            title="Tile and Label Map Upload Confirmation",
            upload_results=upload_results,
        )

        verified_count = sum(
            1 for item in upload_results if item.get("verified_in_b2")
        )

        return (
            {
                "status": "uploaded",
                "dataset_id": dataset_id,
                "uploaded_count": len(upload_results),
                "verified_count": verified_count,
            },
            dbc.Alert(
                f"Upload completed. {len(upload_results)} file(s) uploaded, {verified_count} verified in B2.",
                color="success",
            ),
            result_card,
        )

    except Exception as e:
        print(f"[LARGE UPLOAD ERROR] {e}")
        mark_upload_failed_safely(dataset_id, e)
        return (
            {"status": "failed", "dataset_id": dataset_id},
            dbc.Alert(f"Large file upload failed: {str(e)}", color="danger"),
            "",
        )


# -------------------------------------------------------------------
# 5. Large Folder Upload Callback
# -------------------------------------------------------------------

@callback(
    Output("upload-status-store", "data", allow_duplicate=True),
    Output("upload-message", "children", allow_duplicate=True),
    Output("upload-result-details", "children", allow_duplicate=True),
    Input("folder-upload-button", "n_clicks"),
    State("dataset-id-input", "value"),
    State("dataset-name-input", "value"),
    State("upload-mode-dropdown", "value"),
    State("dataset-description-input", "value"),
    State("local-folder-path-input", "value"),
    prevent_initial_call=True,
)
def handle_folder_upload(
    n_clicks,
    dataset_id,
    dataset_name,
    upload_mode,
    description,
    local_folder_path,
):
    if not dataset_id:
        return None, dbc.Alert("Please enter a Dataset ID.", color="danger"), ""

    dataset_id = dataset_id.strip()

    if not dataset_name:
        dataset_name = dataset_id

    if not local_folder_path:
        return None, dbc.Alert("Please enter a local folder path.", color="danger"), ""

    try:
        upload_results = upload_folder_to_b2(
            dataset_id=dataset_id,
            folder_path=local_folder_path,
            complete_progress=False,
        )

        point_cloud_filenames = [
            item["filename"]
            for item in upload_results
            if item.get("file_role") == "point_cloud_tile"
        ]

        finalize_uploaded_dataset(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            upload_mode=upload_mode,
            description=description,
            point_cloud_filenames=point_cloud_filenames,
            upload_results=upload_results,
        )

        uploaded_count = len(upload_results)
        verified_count = sum(
            1 for item in upload_results if item.get("verified_in_b2")
        )

        all_verified = uploaded_count == verified_count
        alert_color = "success" if all_verified else "warning"

        result_card = build_upload_result_card(
            title="Folder Upload Confirmation",
            upload_results=upload_results,
        )

        return (
            {
                "status": "uploaded" if all_verified else "uploaded_but_not_fully_verified",
                "dataset_id": dataset_id,
                "uploaded_count": uploaded_count,
                "verified_count": verified_count,
            },
            dbc.Alert(
                f"Folder upload completed. {uploaded_count} file(s) uploaded, "
                f"{verified_count} verified in B2.",
                color=alert_color,
            ),
            result_card,
        )

    except Exception as e:
        print(f"[FOLDER UPLOAD ERROR] {e}")
        mark_upload_failed_safely(dataset_id, e)
        return (
            {"status": "failed", "dataset_id": dataset_id},
            dbc.Alert(f"Folder upload failed: {str(e)}", color="danger"),
            "",
        )


# -------------------------------------------------------------------
# 6. Upload Progress Bar Callback
# -------------------------------------------------------------------

@callback(
    Output("upload-progress-bar", "value"),
    Output("upload-progress-bar", "label"),
    Output("upload-progress-text", "children"),
    Input("upload-progress-interval", "n_intervals"),
    State("dataset-id-input", "value"),
)
def update_upload_progress_ui(n_intervals, dataset_id):
    if not dataset_id:
        return 0, "0%", "No dataset selected."

    dataset_id = dataset_id.strip()

    progress = load_upload_progress(dataset_id)

    percentage = progress.get("percentage", 0)
    status = progress.get("status", "not_started")
    stage = progress.get("stage", "idle")
    current_file = progress.get("current_file", "")
    uploaded_files = progress.get("uploaded_files", 0)
    total_files = progress.get("total_files", 0)
    failed_files = progress.get("failed_files", 0)
    message = progress.get("message", "")

    text = (
        f"Status: {status} | "
        f"Stage: {stage} | "
        f"Uploaded: {uploaded_files}/{total_files} | "
        f"Failed: {failed_files} | "
        f"Current file: {current_file} | "
        f"{message}"
    )

    return percentage, f"{percentage}%", text


# -------------------------------------------------------------------
# 7. Upload Result Card Helper
# -------------------------------------------------------------------

def build_upload_result_card(title, upload_results):
    rows = []

    for item in upload_results:
        status_text = (
            "Verified in B2"
            if item.get("verified_in_b2")
            else "Uploaded, but verification failed"
        )

        rows.append(
            html.Tr(
                [
                    html.Td(item.get("filename", "")),
                    html.Td(item.get("file_role", "")),
                    html.Td(item.get("b2_path", "")),
                    html.Td(f'{item.get("file_size_bytes", 0):,} bytes'),
                    html.Td(str(item.get("verified_size_bytes", ""))),
                    html.Td(item.get("sha1", "")),
                    html.Td(item.get("b2_file_id", "")),
                    html.Td(status_text),
                ]
            )
        )

    return dbc.Card(
        dbc.CardBody(
            [
                html.H5(title),
                html.P(
                    f"{len(upload_results)} file(s) processed. "
                    "Point cloud tiles and label mapping files are stored separately."
                ),
                html.Div(
                    html.Table(
                        [
                            html.Thead(
                                html.Tr(
                                    [
                                        html.Th("File Name"),
                                        html.Th("File Role"),
                                        html.Th("B2 Path"),
                                        html.Th("Local Size"),
                                        html.Th("Verified B2 Size"),
                                        html.Th("SHA-1"),
                                        html.Th("B2 File ID"),
                                        html.Th("Status"),
                                    ]
                                )
                            ),
                            html.Tbody(rows),
                        ],
                        className="table table-sm table-bordered",
                    ),
                    style={
                        "overflowX": "auto",
                        "maxWidth": "100%",
                    },
                ),
            ]
        ),
        color="success",
        outline=True,
    )


# -------------------------------------------------------------------
# 8. Delete Dataset or Specific Tile
# -------------------------------------------------------------------

@callback(
    Output("delete-message", "children"),
    Output("upload-status-store", "data", allow_duplicate=True),
    Input("delete-dataset-button", "n_clicks"),
    State("delete-dataset-id-input", "value"),
    State("delete-tile-name-input", "value"),
    prevent_initial_call=True,
)
def delete_dataset_or_tile(n_clicks, dataset_id, tile_name):
    if not dataset_id:
        return (
            dbc.Alert("Please enter a Dataset ID to delete.", color="danger"),
            None,
        )

    dataset_id = dataset_id.strip()
    tile_name = tile_name.strip() if tile_name else ""

    try:
        deleted_b2_files = []

        if tile_name:
            b2_tile_path = (
                f"bronze_raw_data/{dataset_id}/source_files/tiles/{tile_name}"
            )

            deleted_file = delete_b2_file_by_name(b2_tile_path)
            deleted_b2_files.append(deleted_file)

            message = (
                f"Deleted tile from B2: {deleted_file}. "
                f"Please regenerate metadata for dataset '{dataset_id}'."
            )

        else:
            prefixes = [
                f"bronze_raw_data/{dataset_id}/",
                f"metadata/datasets/{dataset_id}.json",
                f"metadata_analytics/{dataset_id}/",
            ]

            for prefix in prefixes:
                try:
                    deleted_b2_files.extend(delete_b2_prefix(prefix))
                except Exception as prefix_error:
                    print(f"[DELETE WARNING] Could not delete prefix {prefix}: {prefix_error}")

            local_metadata_file = f"data/metadata/datasets/{dataset_id}.json"
            local_analytics_dir = f"data/metadata_analytics/{dataset_id}"
            local_progress_file = f"data/upload_progress/{dataset_id}.json"
            local_download_dir = f"data/local_staging/b2_metadata_downloads/{dataset_id}"

            if os.path.exists(local_metadata_file):
                os.remove(local_metadata_file)

            if os.path.exists(local_analytics_dir):
                shutil.rmtree(local_analytics_dir)

            if os.path.exists(local_progress_file):
                os.remove(local_progress_file)

            if os.path.exists(local_download_dir):
                shutil.rmtree(local_download_dir)

            message = (
                f"Dataset '{dataset_id}' deleted from B2 and local metadata. "
                f"Deleted B2 objects: {len(deleted_b2_files)}"
            )

        return (
            dbc.Alert(message, color="success"),
            {"status": "deleted", "dataset_id": dataset_id},
        )

    except Exception as e:
        print(f"[DELETE ERROR] {e}")
        return (
            dbc.Alert(f"Delete failed: {str(e)}", color="danger"),
            {"status": "delete_failed", "dataset_id": dataset_id},
        )


# -------------------------------------------------------------------
# 9. Load Selected Dataset Dashboard
# -------------------------------------------------------------------

@callback(
    Output("kpi-cards-container", "children"),
    Output("attribute-table", "data"),
    Output("attribute-chart", "figure"),
    Output("label-table", "data"),
    Output("label-chart", "figure"),
    Output("class-label-table", "data"),
    Output("class-label-pie-chart", "figure"),
    Output("class-label-bar-chart", "figure"),
    Output("spatial-table", "data"),
    Output("spatial-z-range-chart", "figure"),
    Output("spatial-point-count-chart", "figure"),
    Output("spatial-density-chart", "figure"),
    Output("readiness-table", "data"),
    Output("model-compatibility-table", "data"),
    Output("lineage-content", "children"),
    Input("selected-dataset-id", "data"),
)
def load_dataset_dashboard(dataset_id):
    print("=" * 80)
    print("[LOAD DATASET DASHBOARD]")
    print(f"dataset_id received: {dataset_id}")
    print("=" * 80)

    if not dataset_id:
        empty_fig = apply_dark_figure_theme(empty_figure("No dataset selected"))

        return (
            dbc.Alert(
                "Select a dataset from the registry to view KPIs.",
                color="info",
            ),
            [],
            empty_fig,
            [],
            empty_fig,
            [],
            empty_fig,
            empty_fig,
            [],
            empty_fig,
            empty_fig,
            empty_fig,
            [],
            [],
            "No dataset selected.",
        )

    metadata = load_dataset_metadata(dataset_id)

    kpis = load_dashboard_kpis(dataset_id)
    attributes = load_attribute_summary(dataset_id)
    labels = load_label_distribution(dataset_id)
    class_labels = load_class_label_distribution(dataset_id)
    spatial = load_spatial_summary(dataset_id)
    class_mapping_summary = load_class_mapping_summary(dataset_id)

    print("[LOCAL METADATA CHECK]")
    print(f"Metadata keys: {list(metadata.keys()) if metadata else 'No metadata found'}")
    print(f"KPI rows: {len(kpis) if kpis is not None else 0}")
    print(f"Attribute rows: {len(attributes) if attributes is not None else 0}")
    print(f"Binary label rows: {len(labels) if labels is not None else 0}")
    print(f"Class label rows: {len(class_labels) if class_labels is not None else 0}")
    print(f"Spatial rows: {len(spatial) if spatial is not None else 0}")
    print(
        f"Class mapping rows: {len(class_mapping_summary) if class_mapping_summary is not None else 0}"
    )
    print("=" * 80)

    kpi_cards = kpi_section(kpis)

    # ---------------------------------------------------------------
    # Attribute chart
    # ---------------------------------------------------------------

    if attributes is not None and not attributes.empty:
        attr_fig = bar_figure(
            attributes,
            x="attribute",
            y="available_numeric",
            title="Available vs Missing Attributes",
            labels={
                "attribute": "Point Attribute",
                "available_numeric": "Available",
            },
        )
        attribute_data = attributes.to_dict("records")
    else:
        attr_fig = empty_figure("No attribute data available")
        attribute_data = []

    # ---------------------------------------------------------------
    # Binary label chart
    # ---------------------------------------------------------------

    if labels is not None and not labels.empty:
        label_fig = pie_figure(
            labels,
            names="class_name",
            values="point_count",
            title="Building vs Non-building Distribution",
            color_map=BINARY_LABEL_COLORS,
        )
        label_data = labels.to_dict("records")
    else:
        label_fig = empty_figure("No binary label data available")
        label_data = []

    # ---------------------------------------------------------------
    # Individual semantic class-label charts
    # ---------------------------------------------------------------

    if class_labels is not None and not class_labels.empty:
        class_labels = class_labels.copy()

        if "class_id" not in class_labels.columns:
            class_labels["class_id"] = ""

        if "class_name" not in class_labels.columns:
            class_labels["class_name"] = class_labels["class_id"].astype(str)

        class_labels["class_name"] = (
            class_labels["class_name"]
            .fillna(class_labels["class_id"].astype(str))
            .astype(str)
        )

        if "coarse_id" not in class_labels.columns:
            class_labels["coarse_id"] = ""

        if "coarse_class_name" not in class_labels.columns:
            class_labels["coarse_class_name"] = ""

        if "binary_label" not in class_labels.columns:
            class_labels["binary_label"] = ""

        if "proportion" not in class_labels.columns:
            total_class_points = class_labels["point_count"].sum()
            if total_class_points:
                class_labels["proportion"] = (
                    class_labels["point_count"] / total_class_points
                ).round(6)
            else:
                class_labels["proportion"] = 0

        class_labels["point_count"] = class_labels["point_count"].astype(int)

        class_label_pie_fig = pie_figure(
            class_labels,
            names="class_name",
            values="point_count",
            title="Individual Semantic Class Distribution",
            color_map=SEMANTIC_CLASS_COLORS,
        )

        class_label_bar_fig = bar_figure(
            class_labels.sort_values("point_count", ascending=False),
            x="class_name",
            y="point_count",
            color="binary_label" if "binary_label" in class_labels.columns else None,
            title="Point Count per Semantic Class",
            labels={
                "class_name": "Semantic Class",
                "point_count": "Point Count",
                "binary_label": "Binary Label",
            },
            color_map=BINARY_LABEL_COLORS,
        )

        class_label_data = class_labels.to_dict("records")

    else:
        class_label_pie_fig = empty_figure("No individual semantic class data available")
        class_label_bar_fig = empty_figure("No individual semantic class data available")
        class_label_data = []

    # ---------------------------------------------------------------
    # Spatial charts
    # ---------------------------------------------------------------

    if spatial is not None and not spatial.empty:
        spatial_z_range_fig = bar_figure(
            spatial,
            x="tile_name",
            y="z_range",
            title="Z Range per Tile",
            labels={
                "tile_name": "Tile",
                "z_range": "Z Range",
            },
        )

        spatial_point_count_fig = bar_figure(
            spatial,
            x="tile_name",
            y="point_count",
            title="Point Count per Tile",
            labels={
                "tile_name": "Tile",
                "point_count": "Point Count",
            },
        )

        spatial_density_fig = bar_figure(
            spatial,
            x="tile_name",
            y="density_estimate",
            title="Density Estimate per Tile",
            labels={
                "tile_name": "Tile",
                "density_estimate": "Points per Square Meter",
            },
        )

        spatial_data = spatial.to_dict("records")

    else:
        spatial_z_range_fig = empty_figure("No spatial data available")
        spatial_point_count_fig = empty_figure("No spatial data available")
        spatial_density_fig = empty_figure("No spatial data available")
        spatial_data = []

    readiness_data = metadata.get("readiness_checks", [])
    model_data = metadata.get("model_compatibility", [])

    lineage = html.Pre(
        f"""
Raw Upload
bronze_raw_data/{dataset_id}/source_files/tiles/
bronze_raw_data/{dataset_id}/source_files/label_maps/
        ↓
Upload Manifests
bronze_raw_data/{dataset_id}/manifests/upload_manifest.json
bronze_raw_data/{dataset_id}/manifests/checksum_manifest.json
        ↓
Metadata Extraction
metadata/datasets/{dataset_id}.json
metadata_analytics/{dataset_id}/*.parquet
        ↓
Preprocessing
gold_model_ready_data/{dataset_id}/prep_version/
        ↓
Training / Inference
segmentation_outputs/{dataset_id}/prep_version/<model>/<run_id>/
        ↓
Clustering
clustered_final_outputs/{dataset_id}/prep_version/<model>/<run_id>/
        ↓
Rerun Visualization
data/rerun_outputs/{dataset_id}_<run_id>.rrd
        """,
        className="lineage-box",
    )

    for fig in [
        attr_fig,
        label_fig,
        class_label_pie_fig,
        class_label_bar_fig,
        spatial_z_range_fig,
        spatial_point_count_fig,
        spatial_density_fig,
    ]:
        apply_dark_figure_theme(fig)

    return (
        kpi_cards,
        attribute_data,
        attr_fig,
        label_data,
        label_fig,
        class_label_data,
        class_label_pie_fig,
        class_label_bar_fig,
        spatial_data,
        spatial_z_range_fig,
        spatial_point_count_fig,
        spatial_density_fig,
        readiness_data,
        model_data,
        lineage,
    )


# -------------------------------------------------------------------
# 10. Load Dataset Button
# -------------------------------------------------------------------

@callback(
    Output("selected-dataset-id", "data", allow_duplicate=True),
    Output("registry-action-message", "children"),
    Input("load-dataset-button", "n_clicks"),
    State("dataset-registry-table", "selected_rows"),
    State("dataset-registry-table", "data"),
    prevent_initial_call=True,
)
def handle_load_dataset_button(n_clicks, selected_rows, table_data):
    if not selected_rows or not table_data:
        return dash.no_update, dbc.Alert(
            "Please select a dataset row in the table first, then click Load Dataset.",
            color="warning",
        )

    row_index = selected_rows[0]

    if row_index is None or row_index >= len(table_data):
        return dash.no_update, dbc.Alert("Invalid row selected.", color="danger")

    dataset_id = table_data[row_index].get("dataset_id")

    return dataset_id, dbc.Alert(
        f"Dataset '{dataset_id}' loaded — scroll down to view KPIs and analytics.",
        color="success",
    )


# -------------------------------------------------------------------
# 11. View Metadata Button
# -------------------------------------------------------------------

@callback(
    Output("registry-action-message", "children", allow_duplicate=True),
    Input("view-metadata-button", "n_clicks"),
    State("selected-dataset-id", "data"),
    prevent_initial_call=True,
)
def view_metadata(n_clicks, dataset_id):
    if not dataset_id:
        return dbc.Alert("Please select a dataset first.", color="warning")

    metadata = load_dataset_metadata(dataset_id)

    if not metadata:
        return dbc.Alert(
            f"No local metadata found for '{dataset_id}'. Upload and register the dataset first.",
            color="warning",
        )

    return dbc.Card(
        dbc.CardBody(
            [
                html.H5(f"Metadata — {dataset_id}"),
                html.Pre(
                    _json.dumps(metadata, indent=2),
                    style={
                        "fontSize": "12px",
                        "maxHeight": "420px",
                        "overflowY": "auto",
                        "background": "#111827",
                        "color": "#e5e7eb",
                        "padding": "16px",
                        "borderRadius": "8px",
                    },
                ),
            ]
        ),
        className="mt-2",
    )


# -------------------------------------------------------------------
# 12. Run Preprocessing Button — placeholder
# -------------------------------------------------------------------

@callback(
    Output("registry-action-message", "children", allow_duplicate=True),
    Input("run-preprocessing-button", "n_clicks"),
    State("selected-dataset-id", "data"),
    prevent_initial_call=True,
)
def run_preprocessing(n_clicks, dataset_id):
    if not dataset_id:
        return dbc.Alert("Please select a dataset first.", color="warning")

    return dbc.Alert(
        [
            html.Strong("Preprocessing is controlled from the Airflow page. "),
            html.Br(),
            "Open the Preprocessing page to create or trigger a remote Airflow run for dataset: ",
            html.Code(dataset_id),
            html.Br(),
            dcc.Link("Go to Preprocessing Control", href="/preprocessing"),
        ],
        color="info",
    )


# -------------------------------------------------------------------
# 13. Open in Rerun Button — helper message
# -------------------------------------------------------------------

@callback(
    Output("registry-action-message", "children", allow_duplicate=True),
    Input("open-rerun-button", "n_clicks"),
    State("selected-dataset-id", "data"),
    prevent_initial_call=True,
)
def open_in_rerun(n_clicks, dataset_id):
    if not dataset_id:
        return dbc.Alert("Please select a dataset first.", color="warning")

    return dbc.Alert(
        [
            html.Strong("Use the Rerun panel below. "),
            html.Br(),
            "Scroll down to Section 6, select one real tile, choose a real color mode, and click ",
            html.Code("Generate Rerun Recording"),
            ". Dataset: ",
            html.Code(dataset_id),
        ],
        color="info",
    )


# -------------------------------------------------------------------
# 14. Populate Rerun Tile Selector
# -------------------------------------------------------------------

@callback(
    Output("rerun-tile-selector", "options"),
    Input("selected-dataset-id", "data"),
)
def populate_rerun_tile_selector(dataset_id):
    if not dataset_id:
        return []

    file_summary = load_file_summary(dataset_id)

    if file_summary is None or file_summary.empty:
        return []

    options = []

    for _, row in file_summary.iterrows():
        filename = row.get("filename", "")
        b2_path = row.get("b2_path", "")

        if not filename:
            continue

        if not str(filename).lower().endswith(".ply"):
            continue

        if not b2_path:
            b2_path = f"bronze_raw_data/{dataset_id}/source_files/tiles/{filename}"

        options.append(
            {
                "label": str(filename),
                "value": str(b2_path),
            }
        )

    return options


# -------------------------------------------------------------------
# 15. Generate Rerun Recording Button — real data only
# -------------------------------------------------------------------

@callback(
    Output("rerun-viewer-placeholder", "children"),
    Input("load-rerun-button", "n_clicks"),
    State("rerun-tile-selector", "value"),
    State("point-budget-selector", "value"),
    State("color-mode-selector", "value"),
    State("view-mode-selector", "value"),
    State("selected-dataset-id", "data"),
    prevent_initial_call=True,
)
def load_rerun_preview(
    n_clicks,
    selected_tiles,
    point_budget,
    color_mode,
    view_mode,
    dataset_id,
):
    if not dataset_id:
        return dbc.Alert("No dataset selected.", color="warning")

    if not selected_tiles:
        return dbc.Alert(
            "Please select one tile from the Tile Selector dropdown first.",
            color="warning",
        )

    # dcc.Dropdown(multi=True) returns list.
    # This keeps compatibility if the value is ever a single string.
    if isinstance(selected_tiles, str):
        selected_tiles = [selected_tiles]

    if len(selected_tiles) > 1:
        return dbc.Alert(
            "Large Rerun visualization is optimized for one tile at a time. Please select only one tile.",
            color="warning",
        )

    metadata = load_dataset_metadata(dataset_id)

    if not metadata:
        return dbc.Alert(
            f"No metadata found for dataset '{dataset_id}'. Please regenerate metadata first.",
            color="danger",
        )

    # ---------------------------------------------------------------
    # Build real selected tile list from B2 paths
    # ---------------------------------------------------------------

    tile_items = []

    for tile_path in selected_tiles:
        if not tile_path:
            continue

        tile_items.append(
            {
                "name": os.path.basename(str(tile_path)),
                "b2_key": str(tile_path),
            }
        )

    if not tile_items:
        return dbc.Alert(
            "No valid tile path was selected.",
            color="warning",
        )

    # ---------------------------------------------------------------
    # Build real label-map list from metadata
    # ---------------------------------------------------------------

    label_maps = metadata.get("label_maps", []) or []
    label_map_items = []

    for item in label_maps:
        b2_path = item.get("b2_path")

        if b2_path:
            label_map_items.append(
                {
                    "name": item.get("file_name", os.path.basename(str(b2_path))),
                    "b2_key": str(b2_path),
                }
            )

    requires_label_map = (
        str(color_mode).strip().lower() in {"semantic_label", "binary_label"}
        or str(view_mode).strip().lower() in {"semantic", "binary"}
    )

    if requires_label_map and not label_map_items:
        return dbc.Alert(
            [
                html.Strong("Rerun preview cannot run semantic visualization."),
                html.Br(),
                "Semantic Label and Building vs Non-building modes require a real XML/JSON/YAML label map in dataset metadata.",
            ],
            color="danger",
        )

    # ---------------------------------------------------------------
    # Generate real Rerun .rrd file without opening the native viewer.
    # ---------------------------------------------------------------

    try:
        result = generate_rerun_preview(
            dataset_id=dataset_id,
            tile_items=tile_items,
            label_map_items=label_map_items,
            point_budget=int(point_budget),
            color_mode=color_mode,
            view_mode=view_mode,
            open_viewer=False,
        )

        tile_rows = []

        for item in result.get("tile_summaries", []):
            detected = item.get("detected_columns", {}) or {}

            tile_rows.append(
                html.Tr(
                    [
                        html.Td(item.get("tile_name", "")),
                        html.Td(f'{int(item.get("original_points", 0)):,}'),
                        html.Td(f'{int(item.get("logged_points", 0)):,}'),
                        html.Td(html.Code(item.get("b2_key", ""))),
                        html.Td(item.get("color_source", "")),
                        html.Td(html.Code(str(detected.get("rgb", "")))),
                        html.Td(html.Code(str(detected.get("intensity", "")))),
                        html.Td(html.Code(str(detected.get("semantic_label", "")))),
                    ]
                )
            )

        return dbc.Card(
            dbc.CardBody(
                [
                    dbc.Alert(
                        [
                            html.Strong("Rerun preview generated successfully."),
                            html.Br(),
                            "The recording was saved without opening the native viewer, to keep large point-cloud previews from freezing the system.",
                        ],
                        color="success",
                    ),

                    html.H5("Generated Rerun Recording"),

                    html.Div(
                        [
                            html.Strong("RRD file: "),
                            html.Code(result.get("rrd_path", "")),
                        ],
                        className="mb-2",
                    ),

                    html.Div(
                        [
                            html.Strong("Open manually from terminal when you are ready: "),
                            html.Code(f'rerun "{result.get("rrd_path", "")}"'),
                        ],
                        className="mb-3",
                    ),

                    dbc.Row(
                        [
                            dbc.Col(
                                dbc.Card(
                                    dbc.CardBody(
                                        [
                                            html.H6("Tiles Loaded"),
                                            html.H4(str(result.get("tiles_loaded", 0))),
                                        ]
                                    ),
                                    color="light",
                                ),
                                xs=12,
                                md=3,
                            ),
                            dbc.Col(
                                dbc.Card(
                                    dbc.CardBody(
                                        [
                                            html.H6("Logged Points"),
                                            html.H4(
                                                f'{int(result.get("total_logged_points", 0)):,}'
                                            ),
                                        ]
                                    ),
                                    color="light",
                                ),
                                xs=12,
                                md=3,
                            ),
                            dbc.Col(
                                dbc.Card(
                                    dbc.CardBody(
                                        [
                                            html.H6("Color Mode"),
                                            html.H4(str(result.get("color_mode", ""))),
                                        ]
                                    ),
                                    color="light",
                                ),
                                xs=12,
                                md=3,
                            ),
                            dbc.Col(
                                dbc.Card(
                                    dbc.CardBody(
                                        [
                                            html.H6("View Mode"),
                                            html.H4(str(result.get("view_mode", ""))),
                                        ]
                                    ),
                                    color="light",
                                ),
                                xs=12,
                                md=3,
                            ),
                        ],
                        className="g-3 mb-3",
                    ),

                    html.Div(
                        [
                            html.Strong("Available Rerun tabs/modes: "),
                            html.Code(", ".join(result.get("available_modes", []))),
                        ],
                        className="mb-3",
                    ),

                    html.H6("Tile Summary"),

                    html.Div(
                        html.Table(
                            [
                                html.Thead(
                                    html.Tr(
                                        [
                                            html.Th("Tile"),
                                            html.Th("Original Points"),
                                            html.Th("Logged Points"),
                                            html.Th("B2 Path"),
                                            html.Th("Selected Color Source"),
                                            html.Th("RGB Field"),
                                            html.Th("Intensity Field"),
                                            html.Th("Semantic Label Field"),
                                        ]
                                    )
                                ),
                                html.Tbody(tile_rows),
                            ],
                            className="table table-sm table-bordered",
                        ),
                        style={
                            "overflowX": "auto",
                            "maxWidth": "100%",
                        },
                    ),
                ]
            ),
            color="success",
            outline=True,
        )

    except Exception as e:
        print("=" * 80)
        print("[RERUN PREVIEW ERROR]")
        print(str(e))
        print("=" * 80)

        return dbc.Alert(
            [
                html.Strong("Rerun preview failed."),
                html.Br(),
                html.Br(),
                html.Strong("Reason:"),
                html.Br(),
                html.Code(str(e)),
                html.Br(),
                html.Br(),
                "No mock visualization was generated. Fix the missing field, label map, selected color mode, or Rerun installation and try again.",
            ],
            color="danger",
        )
