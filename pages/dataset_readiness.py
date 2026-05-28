import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dcc, html

from components.ops_page_shell import data_table, info_card, kv_grid, page_shell, section
from services.b2_paths import dataset_metadata_key
from services.dataset_selection import resolve_selected_dataset_id
from services.metadata_service import list_registered_datasets, load_dataset_metadata
from services.preprocessing_runtime_service import load_b2_json_file


dash.register_page(
    __name__,
    path="/dataset-readiness",
    name="Dataset Readiness",
    title="Dataset Readiness - LiDAR Platform",
)


_REFRESH_INTERVAL_MS = 60_000


# -------------------------------------------------------------------
# Registry rows (unchanged from the original placeholder).
# The registry table is dataset-agnostic and renders at layout time.
# -------------------------------------------------------------------

def _registered_rows():
    try:
        datasets = list_registered_datasets()
    except Exception as exc:
        print(f"[DATASET READINESS REGISTRY ERROR] {exc}")
        datasets = []
    return [
        {
            "dataset_id": item.get("dataset_id", "n/a"),
            "name": item.get("dataset_name", item.get("dataset_id", "n/a")),
            "files": item.get("total_files", "n/a"),
            "points": item.get("total_points", "n/a"),
            "status": item.get("status", "registered"),
        }
        for item in datasets
    ]


# -------------------------------------------------------------------
# Render helpers - consume the existing metadata JSON fields only.
# Pipeline source: services.metadata_service.generate_dataset_metadata_and_analytics
# -------------------------------------------------------------------

def _load_readiness_metadata(dataset_id):
    metadata = load_dataset_metadata(dataset_id)
    if metadata:
        return metadata, f"data/metadata/datasets/{dataset_id}.json", ""

    b2_key = dataset_metadata_key(dataset_id)
    result = load_b2_json_file(b2_key)
    data = result.get("data")
    if isinstance(data, dict) and data:
        return data, b2_key, result.get("error") or ""
    return {}, b2_key, result.get("error") or ""

def _format_number(value):
    if value in (None, "", "n/a"):
        return "Not available"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)


def _empty_alert(message, color="info"):
    return dbc.Alert(message, color=color, className="mb-0")


def _render_header(dataset_id, metadata, source_path):
    name = metadata.get("dataset_name") or dataset_id
    status = str(metadata.get("status") or "registered")
    description = metadata.get("description") or ""
    upload_mode = metadata.get("upload_mode") or ""

    rows = [
        ("Dataset ID", dataset_id),
        ("Dataset Name", name),
        ("Registry Status", status),
    ]
    if upload_mode:
        rows.append(("Upload Mode", upload_mode))
    if description:
        rows.append(("Description", description))

    return html.Div(
        [
            info_card(
                name,
                f"Readiness loaded from {source_path}.",
                "Selected Dataset",
            ),
            kv_grid(rows),
        ],
        className="ops-two-col",
    )


def _render_summary(metadata):
    quality_flags = metadata.get("quality_flags") or {}
    ready_train = bool(quality_flags.get("ready_for_training"))
    ready_infer = bool(quality_flags.get("ready_for_inference"))
    has_label_map = bool(quality_flags.get("label_mapping_available"))
    has_building_mapping = bool(quality_flags.get("building_mapping_available"))
    label_status = str(metadata.get("labels") or "Unknown")

    rows = [
        ("Total Files", _format_number(metadata.get("total_files"))),
        ("Total Points", _format_number(metadata.get("total_points"))),
        ("Labels", label_status),
        ("Label Map", "Available" if has_label_map else "Missing"),
        ("Building Mapping", "Available" if has_building_mapping else "Missing"),
        ("Training Ready", "Yes" if ready_train else "No"),
        ("Inference Ready", "Yes" if ready_infer else "No"),
    ]
    created_at = metadata.get("created_at")
    if created_at:
        rows.append(("Registered At", str(created_at)))

    return kv_grid(rows)


def _status_pill_class(status):
    s = str(status or "").strip().lower()
    if s == "pass":
        return "ops-small-status ops-small-status-ok"
    if s == "warning":
        return "ops-small-status ops-small-status-warn"
    if s == "fail":
        return "ops-small-status ops-small-status-danger"
    return "ops-small-status ops-small-status-info"


def _check_card_class(status):
    s = str(status or "").strip().lower()
    if s in {"fail", "warning"}:
        return "ops-mini-card ops-mini-card-warn"
    return "ops-mini-card"


def _render_checks(metadata):
    checks = metadata.get("readiness_checks") or []
    if not checks:
        return _empty_alert(
            "No readiness checks were recorded for this dataset. Regenerate metadata "
            "analytics from Data Explorer to populate readiness checks.",
            color="warning",
        )

    cards = []
    for item in checks:
        name = str(item.get("check") or "Check")
        status = str(item.get("status") or "Unknown")
        message = str(item.get("message") or "")
        cards.append(
            html.Div(
                [
                    html.Span(status.upper(), className=_status_pill_class(status)),
                    html.H3(name),
                    html.P(message),
                ],
                className=_check_card_class(status),
            )
        )
    return html.Div(cards, className="ops-card-grid")


def _render_models(metadata):
    rows = metadata.get("model_compatibility") or []
    if not rows:
        return _empty_alert(
            "No model compatibility entries were recorded for this dataset.",
            color="warning",
        )
    table_rows = [
        {
            "model": str(item.get("model") or "n/a"),
            "required_format": str(item.get("required_format") or "n/a"),
            "status": str(item.get("status") or "Not generated"),
        }
        for item in rows
    ]
    return data_table(
        table_rows,
        columns=[
            {"name": "Model", "id": "model"},
            {"name": "Required Format", "id": "required_format"},
            {"name": "Status", "id": "status"},
        ],
        empty_title="No model compatibility entries",
        empty_detail="model_compatibility was not generated for this dataset.",
    )


# -------------------------------------------------------------------
# Layout
# -------------------------------------------------------------------

_EMPTY_SELECTED = _empty_alert(
    "No dataset selected. Please select a dataset from Data Explorer first.",
    color="info",
)


layout = page_shell(
    active="Readiness",
    subtitle="Dataset validation and model-readiness checks",
    status="Readiness Shell",
    canvas_id="readiness-cv",
    eyebrow="Data Management",
    title="Dataset",
    accent="Readiness",
    description=(
        "Validate LiDAR datasets before preprocessing using metadata, attribute availability, "
        "label checks, coordinate checks, and block-generation feasibility."
    ),
    metrics=[
        ("Readiness Score", "Live"),
        ("Required Fields", "Live"),
        ("Label Status", "Live"),
        ("Auto Refresh", "60s"),
    ],
    page_class="readiness-page",
    children=[
        dcc.Interval(
            id="dataset-readiness-refresh",
            interval=_REFRESH_INTERVAL_MS,
            n_intervals=0,
        ),
        section(
            "Selection",
            "Selected dataset",
            "The selected dataset is read from the cross-page URL parameter "
            "(?dataset_id=...) used by Data Explorer, or from the session store "
            "when present.",
            html.Div(_EMPTY_SELECTED, id="dataset-readiness-header"),
            "ops-panel-primary",
        ),
        section(
            "Snapshot",
            "Dataset snapshot",
            "Counts, label availability, and overall preprocessing readiness pulled "
            "from the existing dataset metadata JSON.",
            html.Div("", id="dataset-readiness-summary"),
        ),
        section(
            "Checks",
            "Readiness checks",
            "Each check is rendered from readiness_checks in the dataset metadata. "
            "Statuses use the existing Pass / Warning / Fail vocabulary produced by "
            "the metadata pipeline.",
            html.Div(_EMPTY_SELECTED, id="dataset-readiness-checks"),
        ),
        section(
            "Models",
            "Model compatibility",
            "Compatibility matrix from model_compatibility in the dataset metadata. "
            "Statuses reflect whether Gold artifacts exist for each model.",
            html.Div("", id="dataset-model-compatibility"),
        ),
        section(
            "Registry",
            "Registered datasets",
            "Current local registry rows are shown read-only so the page can load "
            "safely without depending on dataset selection.",
            data_table(
                _registered_rows(),
                empty_title="No registered datasets",
                empty_detail="No dataset registry JSON files were found.",
            ),
        ),
    ],
)


# -------------------------------------------------------------------
# Callback wiring
# -------------------------------------------------------------------

@callback(
    Output("dataset-readiness-header", "children"),
    Output("dataset-readiness-summary", "children"),
    Output("dataset-readiness-checks", "children"),
    Output("dataset-model-compatibility", "children"),
    Input("selected-dataset-id", "data"),
    Input("dataset-readiness-refresh", "n_intervals"),
    Input("url", "search"),
)
def update_dataset_readiness(selected_dataset_id, _n_intervals, search):
    dataset_id = resolve_selected_dataset_id(search, selected_dataset_id)

    if not dataset_id:
        empty = _empty_alert(
            "No dataset selected. Please select a dataset from Data Explorer first.",
            color="info",
        )
        return empty, "", empty, ""

    try:
        metadata, metadata_source, metadata_error = _load_readiness_metadata(dataset_id)
    except Exception as exc:
        print(f"[DATASET READINESS LOAD ERROR] dataset_id={dataset_id}: {exc}")
        err = _empty_alert(
            f"Could not load metadata for dataset '{dataset_id}': {exc}",
            color="danger",
        )
        return err, "", err, ""

    if not metadata:
        missing_children = [
            html.Strong(f"No metadata found for dataset '{dataset_id}'."),
            html.Br(),
            "Expected metadata at ",
            html.Code(dataset_metadata_key(dataset_id)),
            ". Upload and register the dataset from Data Explorer to generate readiness checks.",
        ]
        if metadata_error:
            missing_children.extend([html.Br(), metadata_error])
        warn = dbc.Alert(
            missing_children,
            color="warning",
            className="mb-0",
        )
        return warn, "", warn, ""

    try:
        header = _render_header(dataset_id, metadata, metadata_source)
        summary = _render_summary(metadata)
        checks = _render_checks(metadata)
        models = _render_models(metadata)
    except Exception as exc:
        print(f"[DATASET READINESS RENDER ERROR] dataset_id={dataset_id}: {exc}")
        err = _empty_alert(
            f"Metadata for dataset '{dataset_id}' could not be rendered: {exc}",
            color="danger",
        )
        return err, "", err, ""

    return header, summary, checks, models
