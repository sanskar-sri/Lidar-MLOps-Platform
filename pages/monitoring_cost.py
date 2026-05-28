from datetime import datetime, timezone

import dash
from dash import Input, Output, callback, dcc, html

from components.ops_page_shell import data_table, kv_grid, page_shell, section
from services.airflow_health_service import get_backend_status_cards
from services.b2_paths import b2_prefix as _b2_prefix, bronze_tiles_prefix, dataset_metadata_key
from services.b2_service import B2_BUCKET_NAME, get_b2_bucket
from services.compute_nodes_service import check_compute_nodes
from services.dataset_selection import resolve_selected_dataset_id
from services.mlflow_service import check_mlflow_service


dash.register_page(
    __name__,
    path="/monitoring-cost",
    name="Monitoring & Cost",
    title="Monitoring & Cost - LiDAR Platform",
)


REFRESH_INTERVAL_MS = 60_000
B2_STORAGE_RATE_PER_GB_MONTH = 0.00695
B2_FREE_STORAGE_GB = 10
B2_COST_PREFIXES = [
    f"{_b2_prefix('bronze_raw_data')}/",
    f"{_b2_prefix('metadata')}/",
    f"{_b2_prefix('metadata_analytics')}/",
    f"{_b2_prefix('gold_model_ready_data')}/",
    f"{_b2_prefix('inference_ready_data')}/",
    f"{_b2_prefix('segmentation_outputs')}/",
    f"{_b2_prefix('clustered_final_outputs')}/",
    f"{_b2_prefix('logs')}/",
]


def _short_detail(value, limit=220):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _format_count(value):
    return f"{_safe_int(value):,}"


def _bytes_to_gb(value):
    return _safe_int(value) / (1024 ** 3)


def _format_gb(value):
    return f"{float(value or 0):,.3f} GB"


def _format_bytes(value):
    size = _safe_int(value)
    if size >= 1024 ** 4:
        return f"{size / (1024 ** 4):,.3f} TB"
    if size >= 1024 ** 3:
        return f"{size / (1024 ** 3):,.3f} GB"
    if size >= 1024 ** 2:
        return f"{size / (1024 ** 2):,.2f} MB"
    if size >= 1024:
        return f"{size / 1024:,.1f} KB"
    return f"{size:,} B"


def _format_money(value):
    return f"${float(value or 0):,.4f}/month"


def _file_timestamp(file_version):
    candidates = [
        getattr(file_version, "upload_timestamp", None),
        getattr(file_version, "upload_timestamp_millis", None),
        getattr(file_version, "mod_time_millis", None),
    ]
    file_info = getattr(file_version, "file_info", None)
    if isinstance(file_info, dict):
        candidates.append(file_info.get("src_last_modified_millis"))

    for value in candidates:
        if value in (None, ""):
            continue
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
    return None


def _format_timestamp(value):
    if not value:
        return "Not available"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _service_name(item):
    return str(item.get("service") or item.get("name") or item.get("id") or "Service")


def _service_status(item):
    status = item.get("status")
    if status in (None, ""):
        status = item.get("state")
    return str(status or "Unknown")


def _tone_slug(item):
    value = str(item.get("tone") or _service_status(item)).strip().lower()
    if value in {"connected", "online", "ok", "healthy", "success", "available"}:
        return "ok"
    if value in {"offline", "failed", "error", "timeout", "unavailable"}:
        return "danger"
    if value in {
        "warning",
        "checking",
        "attention",
        "not configured",
        "not_configured",
        "no dag run",
        "missing",
        "degraded",
        "unknown",
    }:
        return "warn"
    return "info"


def _display_item(item, service=None):
    item = dict(item or {})
    if service:
        item["service"] = service

    status = _service_status(item)
    detail = str(item.get("detail") or "").strip()
    if status.lower() == "checking" and "disabled" in detail.lower():
        item["status"] = "Probe Disabled"
        item["tone"] = "warning"
    return item


def _status_card(item, service=None):
    item = _display_item(item, service=service)
    title = _service_name(item)
    status = _service_status(item)
    detail = _short_detail(item.get("detail") or "No status detail returned.")
    tone = _tone_slug(item)
    card_class = "ops-mini-card ops-mini-card-warn" if tone in {"warn", "danger"} else "ops-mini-card"

    rows = []
    if item.get("airflow_queue"):
        rows.append(("Queue", item.get("airflow_queue")))
    if item.get("health_url"):
        rows.append(("Health URL", item.get("health_url")))
    if item.get("roles"):
        rows.append(("Roles", ", ".join(item.get("roles") or [])))

    metrics = item.get("metrics") or []
    for metric in metrics:
        rows.append(
            (
                metric.get("label") or "Metric",
                " - ".join(
                    part
                    for part in [str(metric.get("value") or ""), str(metric.get("detail") or "")]
                    if part
                ),
            )
        )

    return html.Div(
        [
            html.Span(status, className=f"ops-small-status ops-small-status-{tone}"),
            html.H3(title),
            html.P(detail),
            kv_grid(rows) if rows else None,
        ],
        className=card_class,
    )


def _empty_state(title, detail):
    return html.Div(
        [
            html.Div(title, className="ops-empty-title"),
            html.Div(detail, className="ops-empty-detail"),
        ],
        className="ops-empty-state",
    )


def _b2_prefix_for_file(file_name):
    for prefix in B2_COST_PREFIXES:
        if str(file_name or "").startswith(prefix):
            return prefix
    return None


def _empty_prefix_usage(prefix):
    return {
        "prefix": prefix,
        "file_count": 0,
        "size_bytes": 0,
        "latest_modified": None,
    }


def _load_b2_storage_cost_snapshot():
    bucket = get_b2_bucket()
    prefix_usage = {prefix: _empty_prefix_usage(prefix) for prefix in B2_COST_PREFIXES}
    total_size_bytes = 0
    total_file_count = 0
    latest_modified = None

    for file_version, _folder_name in bucket.ls(folder_to_list="", recursive=True):
        if file_version is None:
            continue

        file_name = getattr(file_version, "file_name", "")
        size_bytes = _safe_int(getattr(file_version, "size", 0))
        modified_at = _file_timestamp(file_version)

        total_size_bytes += size_bytes
        total_file_count += 1
        if modified_at and (latest_modified is None or modified_at > latest_modified):
            latest_modified = modified_at

        prefix = _b2_prefix_for_file(file_name)
        if prefix:
            usage = prefix_usage[prefix]
            usage["file_count"] += 1
            usage["size_bytes"] += size_bytes
            if modified_at and (
                usage["latest_modified"] is None or modified_at > usage["latest_modified"]
            ):
                usage["latest_modified"] = modified_at

    total_gb = _bytes_to_gb(total_size_bytes)
    billable_gb = max(total_gb - B2_FREE_STORAGE_GB, 0)
    estimated_monthly_cost = billable_gb * B2_STORAGE_RATE_PER_GB_MONTH

    rows = []
    for prefix in B2_COST_PREFIXES:
        usage = prefix_usage[prefix]
        prefix_gb = _bytes_to_gb(usage["size_bytes"])
        cost_share = (
            estimated_monthly_cost * (usage["size_bytes"] / total_size_bytes)
            if total_size_bytes
            else 0
        )
        rows.append(
            {
                "prefix": prefix,
                "files": _format_count(usage["file_count"]),
                "size": _format_bytes(usage["size_bytes"]),
                "gb": f"{prefix_gb:,.3f}",
                "estimated_cost": _format_money(cost_share),
                "latest_modified": _format_timestamp(usage["latest_modified"]),
            }
        )

    return {
        "bucket": B2_BUCKET_NAME,
        "total_size_bytes": total_size_bytes,
        "total_file_count": total_file_count,
        "latest_modified": latest_modified,
        "total_gb": total_gb,
        "billable_gb": billable_gb,
        "estimated_monthly_cost": estimated_monthly_cost,
        "prefix_rows": rows,
    }


def _b2_cost_panel(snapshot):
    total_gb = snapshot["total_gb"]
    billable_gb = snapshot["billable_gb"]
    estimated_cost = snapshot["estimated_monthly_cost"]

    summary_rows = [
        ("Bucket", snapshot["bucket"]),
        ("Total bucket size", _format_gb(total_gb)),
        ("Total file count", _format_count(snapshot["total_file_count"])),
        ("Latest modified", _format_timestamp(snapshot["latest_modified"])),
        ("Estimated billable GB", _format_gb(billable_gb)),
        ("Rate used", "$0.00695/GB/month after first 10 GB"),
    ]

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            _status_card(
                                {
                                    "service": "Estimated B2 storage cost",
                                    "status": _format_money(estimated_cost),
                                    "detail": (
                                        "This estimate is computed from current stored object bytes "
                                        "listed from the configured Backblaze B2 bucket."
                                    ),
                                    "tone": "connected",
                                }
                            ),
                            html.Div(
                                [
                                    html.H3("B2 bucket inventory"),
                                    kv_grid(summary_rows),
                                ],
                                className="ops-mini-card",
                            ),
                        ],
                        className="ops-card-grid",
                    ),
                    data_table(
                        snapshot["prefix_rows"],
                        columns=[
                            {"name": "Prefix", "id": "prefix"},
                            {"name": "Files", "id": "files"},
                            {"name": "Size", "id": "size"},
                            {"name": "GB", "id": "gb"},
                            {"name": "Estimated Cost", "id": "estimated_cost"},
                            {"name": "Latest Modified", "id": "latest_modified"},
                        ],
                        empty_title="No B2 files",
                        empty_detail="The configured bucket returned no files for the monitored prefixes.",
                    ),
                    html.P(
                        "Rate used: $0.00695/GB/month after first 10 GB",
                        className="ops-empty-detail",
                    ),
                    html.P(
                        (
                            "This is an estimate based on current stored bytes in B2. "
                            "Final billing may differ due to byte-hour averaging, egress, "
                            "transaction classes, credits, taxes, and account-level usage reports."
                        ),
                        className="ops-empty-detail",
                    ),
                ],
                className="ops-card-grid",
            ),
        ]
    )


def _error_card(service, exc):
    return _status_card(
        {
            "service": service,
            "status": "Error",
            "detail": str(exc),
            "tone": "offline",
        }
    )


def _card_grid(items, empty_title, empty_detail):
    items = items or []
    if not items:
        return _empty_state(empty_title, empty_detail)
    return html.Div([_status_card(item) for item in items], className="ops-card-grid")


def _match_backend(cards, *tokens):
    lowered = [token.lower() for token in tokens]
    for card in cards or []:
        service = _service_name(card).lower()
        if any(token in service for token in lowered):
            return card
    return None


def _compute_summary(nodes):
    nodes = nodes or []
    total = len(nodes)
    online = sum(1 for node in nodes if _tone_slug(node) == "ok")
    if not total:
        return {
            "service": "Compute Nodes",
            "status": "Unknown",
            "detail": "No compute nodes were returned by the compute health service.",
            "tone": "warning",
        }
    return {
        "service": "Compute Nodes",
        "status": f"{online}/{total} Online",
        "detail": "Computed from check_compute_nodes() results.",
        "tone": "connected" if online else "warning",
    }


def _gpu_summary(nodes, runtime_card=None):
    available = []
    reported_unavailable = []
    for node in nodes or []:
        payload = node.get("payload") or {}
        gpu = payload.get("gpu") or {}
        if gpu.get("available"):
            available.append(gpu.get("name") or node.get("name") or "GPU")
        elif gpu:
            reported_unavailable.append(
                gpu.get("detail") or node.get("name") or "GPU not available"
            )

    if available:
        return {
            "service": "GPU",
            "status": "Available",
            "detail": ", ".join(available),
            "tone": "connected",
        }
    if reported_unavailable:
        return {
            "service": "GPU",
            "status": "Not Found",
            "detail": " | ".join(reported_unavailable),
            "tone": "warning",
        }
    if runtime_card:
        return {
            "service": "GPU",
            "status": "Unknown",
            "detail": runtime_card.get("detail") or "Remote runtime did not return GPU detail.",
            "tone": runtime_card.get("tone") or "warning",
        }
    return {
        "service": "GPU",
        "status": "Unknown",
        "detail": "No GPU detail was returned by backend or compute-node health services.",
        "tone": "warning",
    }


layout = page_shell(
    active="Monitoring",
    subtitle="Platform health, reliability, and cost controls",
    status="Ops Shell",
    canvas_id="monitoring-cv",
    eyebrow="Platform Operations",
    title="Monitoring",
    accent="Cost",
    description="Monitor platform health, orchestration status, worker availability, storage usage, and processing reliability.",
    metrics=[
        ("Health Source", "Live"),
        ("Refresh", "60s"),
        ("Backend Probes", "Services"),
        ("B2 Cost", "Estimate"),
    ],
    page_class="monitoring-page",
    children=[
        dcc.Interval(
            id="monitoring-cost-refresh",
            interval=REFRESH_INTERVAL_MS,
            n_intervals=0,
        ),
        section(
            "Dataset",
            "Selected dataset context",
            "Dataset context is resolved from ?dataset_id=... first, then the session store.",
            html.Div(
                _empty_state("No dataset selected", "Please select a dataset from Data Explorer first."),
                id="monitoring-dataset-context",
            ),
            "ops-panel-primary",
        ),
        section(
            "Health",
            "Backend service health",
            "Live status cards from the existing Airflow health service.",
            html.Div(
                _empty_state("Waiting for first refresh", "Backend health will load automatically."),
                id="monitoring-backend-health",
            ),
            "ops-panel-primary",
        ),
        section(
            "Storage",
            "B2 storage",
            "Bucket connectivity and object-store health from the existing backend health service.",
            html.Div(
                _empty_state("Waiting for first refresh", "B2 storage status will load automatically."),
                id="monitoring-storage-health",
            ),
        ),
        section(
            "Cost",
            "Estimated B2 storage cost",
            "Monthly storage estimate from real object sizes in the configured Backblaze B2 bucket.",
            html.Div(
                _empty_state("Waiting for first refresh", "B2 size and cost estimate will load automatically."),
                id="monitoring-b2-cost",
            ),
        ),
        section(
            "Tracking",
            "MLflow / experiment tracking",
            "Direct tracking-server health from the existing MLflow service probe.",
            html.Div(
                _empty_state("Waiting for first refresh", "MLflow status will load automatically."),
                id="monitoring-mlflow-health",
            ),
        ),
        section(
            "Versioning",
            "DVC availability",
            "DVC status as reported by the existing Airflow remote-health DAG service.",
            html.Div(
                _empty_state("Waiting for first refresh", "DVC status will load automatically."),
                id="monitoring-dvc-health",
            ),
        ),
        section(
            "Runtime",
            "Remote system and GPU",
            "Remote Airflow runtime and GPU availability from backend and compute-node health.",
            html.Div(
                _empty_state("Waiting for first refresh", "Remote system status will load automatically."),
                id="monitoring-runtime-health",
            ),
        ),
        section(
            "Compute",
            "Compute node health",
            "Online, offline, or unknown worker states from the existing compute-node health service.",
            html.Div(
                _empty_state("Waiting for first refresh", "Compute-node status will load automatically."),
                id="monitoring-compute-health",
            ),
        ),
    ],
)


@callback(
    Output("monitoring-dataset-context", "children"),
    Input("selected-dataset-id", "data"),
    Input("url", "search"),
)
def update_monitoring_dataset_context(selected_dataset_id, search):
    dataset_id = resolve_selected_dataset_id(search, selected_dataset_id)
    if not dataset_id:
        return _empty_state("No dataset selected", "Please select a dataset from Data Explorer first.")

    rows = [
        ("Dataset ID", dataset_id),
        ("Metadata", dataset_metadata_key(dataset_id)),
        ("Bronze tiles", f"{bronze_tiles_prefix(dataset_id)}/"),
        ("Gold root", f"{_b2_prefix('gold_model_ready_data')}/{dataset_id}/"),
        ("Segmentation root", f"{_b2_prefix('segmentation_outputs')}/{dataset_id}/"),
    ]
    return html.Div(
        [
            _status_card(
                {
                    "service": "Selected Dataset",
                    "status": "Context Active",
                    "detail": "Monitoring remains global; dataset prefixes are shown for workflow-aware navigation.",
                    "tone": "connected",
                }
            ),
            kv_grid(rows),
        ],
        className="ops-card-grid",
    )


@callback(
    Output("monitoring-backend-health", "children"),
    Output("monitoring-compute-health", "children"),
    Output("monitoring-mlflow-health", "children"),
    Output("monitoring-storage-health", "children"),
    Output("monitoring-b2-cost", "children"),
    Output("monitoring-dvc-health", "children"),
    Output("monitoring-runtime-health", "children"),
    Input("monitoring-cost-refresh", "n_intervals"),
)
def refresh_monitoring_cost(_ticks):
    try:
        backend_cards = get_backend_status_cards() or []
    except Exception as exc:
        print(f"[MONITORING COST BACKEND ERROR] {exc}")
        backend_cards = []
        backend_section = html.Div(
            [_error_card("Backend Health", exc)],
            className="ops-card-grid",
        )
    else:
        backend_section = _card_grid(
            backend_cards,
            "No backend health cards",
            "get_backend_status_cards() returned no service status records.",
        )

    try:
        compute_nodes = check_compute_nodes() or []
    except Exception as exc:
        print(f"[MONITORING COST COMPUTE ERROR] {exc}")
        compute_nodes = []
        compute_section = html.Div([_error_card("Compute Nodes", exc)], className="ops-card-grid")
    else:
        compute_items = [_compute_summary(compute_nodes), *compute_nodes]
        compute_section = _card_grid(
            compute_items,
            "No compute nodes",
            "check_compute_nodes() returned no node status records.",
        )

    try:
        mlflow_status = check_mlflow_service() or {}
    except Exception as exc:
        print(f"[MONITORING COST MLFLOW ERROR] {exc}")
        mlflow_section = html.Div([_error_card("MLflow", exc)], className="ops-card-grid")
    else:
        mlflow_section = _card_grid(
            [mlflow_status],
            "No MLflow status",
            "check_mlflow_service() returned no tracking status.",
        )

    b2_card = _match_backend(backend_cards, "b2", "storage")
    dvc_card = _match_backend(backend_cards, "dvc")
    airflow_card = _match_backend(backend_cards, "airflow")
    runtime_card = _match_backend(backend_cards, "runtime", "system")

    storage_section = _card_grid(
        [b2_card] if b2_card else [],
        "No B2 status",
        "The backend health service did not return a B2 Storage card.",
    )

    try:
        b2_cost_section = _b2_cost_panel(_load_b2_storage_cost_snapshot())
    except Exception as exc:
        print(f"[MONITORING COST B2 STORAGE COST ERROR] {exc}")
        b2_cost_section = html.Div(
            [_error_card("Estimated B2 storage cost", exc)],
            className="ops-card-grid",
        )

    dvc_section = _card_grid(
        [dvc_card] if dvc_card else [],
        "No DVC status",
        "The backend health service did not return a DVC card.",
    )
    runtime_items = [
        item
        for item in [airflow_card, runtime_card, _gpu_summary(compute_nodes, runtime_card)]
        if item
    ]
    runtime_section = _card_grid(
        runtime_items,
        "No remote runtime status",
        "The backend health service did not return Airflow runtime or GPU status.",
    )

    return (
        backend_section,
        compute_section,
        mlflow_section,
        storage_section,
        b2_cost_section,
        dvc_section,
        runtime_section,
    )
