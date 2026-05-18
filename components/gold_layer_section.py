from dash import dash_table, html

from components.platform_theme import empty_state, small_status
from services.preprocessing_runtime_service import (
    build_gold_output_contract,
    verify_b2_gold_outputs,
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


def _contract_table(rows):
    return dash_table.DataTable(
        columns=[
            {"name": "Artifact", "id": "artifact"},
            {"name": "Kind", "id": "kind"},
            {"name": "Status", "id": "status"},
            {"name": "Size", "id": "size_display"},
            {"name": "B2 key", "id": "b2_key"},
        ],
        data=rows,
        page_size=14,
        **_table_style(),
    )


def _fmt(value, suffix=""):
    if value is None:
        return "n/a"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M{suffix}"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K{suffix}"
    return f"{int(n):,}{suffix}"


def _pct(value):
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _split_metrics(dataset_stats):
    """Three stat cards — train / val / test — from dataset_stats.csv rows."""
    if not dataset_stats:
        return None

    by_split = {}
    for row in dataset_stats:
        split = (row.get("split") or "").strip().lower()
        if split:
            by_split[split] = row

    cards = []
    for split in ("train", "val", "test"):
        row = by_split.get(split, {})
        accent = {"train": "#4fb3ff", "val": "#3dd6b5", "test": "#f2b84b"}.get(split, "#9ca8b4")
        cards.append(
            html.Div(
                [
                    html.Div(
                        split.upper(),
                        className="preproc-section-label",
                        style={"color": accent},
                    ),
                    html.Div(
                        [
                            html.Div([
                                html.Span(_fmt(row.get("num_scenes")), className="gold-metric-value"),
                                html.Span("scenes", className="gold-metric-label"),
                            ], className="gold-metric-pair"),
                            html.Div([
                                html.Span(_fmt(row.get("num_points")), className="gold-metric-value"),
                                html.Span("total pts", className="gold-metric-label"),
                            ], className="gold-metric-pair"),
                            html.Div([
                                html.Span(_fmt(row.get("building_points")), className="gold-metric-value"),
                                html.Span("bldg pts", className="gold-metric-label"),
                            ], className="gold-metric-pair"),
                            html.Div([
                                html.Span(_pct(row.get("building_ratio")), className="gold-metric-value", style={"color": accent}),
                                html.Span("bldg ratio", className="gold-metric-label"),
                            ], className="gold-metric-pair"),
                        ],
                        className="gold-split-metrics",
                    ),
                ],
                className="gold-split-card",
            )
        )
    return html.Div(cards, className="gold-split-grid")


def _label_map_card(label_map):
    if not label_map:
        return None
    if isinstance(label_map, dict):
        # Prefer binary_classes if present (nested structure from pipeline)
        binary = label_map.get("binary_classes")
        if binary and isinstance(binary, dict):
            rows = [{"id": k, "class": v} for k, v in sorted(binary.items(), key=lambda x: str(x[0]))]
        else:
            # Flat dict — skip nested sub-objects
            rows = [
                {"id": k, "class": str(v)}
                for k, v in sorted(label_map.items(), key=lambda x: str(x[0]))
                if not isinstance(v, (dict, list))
            ]
    elif isinstance(label_map, list):
        rows = label_map
    else:
        return None
    if not rows:
        return None
    ignore = label_map.get("ignore_label") if isinstance(label_map, dict) else None
    return html.Div(
        [
            html.Div("Label Map", className="gold-card-title"),
            dash_table.DataTable(
                columns=[{"name": "ID", "id": "id"}, {"name": "Class", "id": "class"}],
                data=rows,
                page_size=12,
                **_table_style(),
            ),
            *([html.Div(f"Ignore label: {ignore}", className="gold-local-path")] if ignore is not None else []),
        ],
        className="gold-info-card",
    )


def _profile_card(preprocessing_contract, preprocessing_profile):
    if not preprocessing_contract and not preprocessing_profile:
        return None
    pc = preprocessing_contract or {}
    pp = preprocessing_profile or {}

    rows = []
    if pc.get("pipeline_version"):
        rows.append({"param": "Pipeline version", "value": str(pc["pipeline_version"])})
    if pc.get("prep_version"):
        rows.append({"param": "Prep version", "value": str(pc["prep_version"])})

    # Stage-by-stage timings from preprocessing_profile
    for stage in (pp.get("stages") or []):
        name = stage.get("stage") or "unknown"
        wall = stage.get("wall_s")
        ram = stage.get("delta_ram_mb")
        rows.append({
            "param": name,
            "value": f"{wall:.1f}s  /  {ram:.0f} MB RAM" if wall is not None else "n/a",
        })

    if pp.get("total_wall_s") is not None:
        rows.append({"param": "Total wall time", "value": f"{pp['total_wall_s']:.1f}s"})
    if pp.get("peak_gpu_vram_mb") is not None:
        rows.append({"param": "Peak GPU VRAM", "value": f"{pp['peak_gpu_vram_mb']:.0f} MB"})
    if pp.get("determinism"):
        rows.append({"param": "Determinism", "value": str(pp["determinism"])})

    if not rows:
        return None
    return html.Div(
        [
            html.Div("Pipeline Profile", className="gold-card-title"),
            dash_table.DataTable(
                columns=[{"name": "Stage / Parameter", "id": "param"}, {"name": "Value", "id": "value"}],
                data=rows,
                page_size=14,
                **_table_style(),
            ),
        ],
        className="gold-info-card",
    )


def _model_configs_card(model_configs):
    if not model_configs:
        return None

    # Top-level shared fields (feature_channels, input_dim_C, etc.)
    shared_rows = []
    if model_configs.get("feature_channels"):
        shared_rows.append({"param": "Feature channels", "value": ", ".join(model_configs["feature_channels"])})
    if model_configs.get("input_dim_C") is not None:
        shared_rows.append({"param": "Input dim (C)", "value": str(model_configs["input_dim_C"])})

    model_cards = []
    for model_name, cfg in model_configs.items():
        if not isinstance(cfg, dict):
            continue
        rows = [{"param": k, "value": str(v)} for k, v in cfg.items()]
        model_cards.append(
            html.Div(
                [
                    html.Div(model_name, className="gold-card-title"),
                    dash_table.DataTable(
                        columns=[{"name": "Param", "id": "param"}, {"name": "Value", "id": "value"}],
                        data=rows,
                        page_size=10,
                        **_table_style(),
                    ),
                ],
                className="gold-info-card",
            )
        )

    if not shared_rows and not model_cards:
        return None

    children = []
    if shared_rows:
        children.append(
            html.Div(
                [
                    html.Div("Shared Input Config", className="gold-card-title"),
                    dash_table.DataTable(
                        columns=[{"name": "Param", "id": "param"}, {"name": "Value", "id": "value"}],
                        data=shared_rows,
                        page_size=5,
                        **_table_style(),
                    ),
                ],
                className="gold-info-card",
                style={"gridColumn": "1 / -1"},
            )
        )
    children += model_cards
    return html.Div(children, className="gold-model-grid")


def build_gold_layer_section(dataset_id, prep_version, silver_readiness=None, verify_existing=True, gold_payload=None):
    if not dataset_id:
        return empty_state("Gold output contract", "Select a dataset to preview the Gold model-ready target path.")

    readiness = silver_readiness or {"status": "failed", "passed": False, "failed_checks": [], "b2_verified": False, "data_available": False}
    contract = build_gold_output_contract(dataset_id, prep_version)
    verification = verify_b2_gold_outputs(dataset_id, prep_version) if verify_existing else {
        **contract,
        "status": "planned",
        "generated_count": 0,
        "expected_count": len(contract["folders"]) + len(contract["files"]),
        "rows": [
            {**item, "status": "planned", "size_display": "planned"}
            for item in contract["folders"] + contract["files"]
        ],
        "error": "",
    }
    analytics_ready = bool(readiness.get("passed"))
    b2_verified = bool(readiness.get("b2_verified"))
    can_generate = analytics_ready and b2_verified

    data = (gold_payload or {}).get("data") or {}
    local_dir = (gold_payload or {}).get("local_dir", "")
    has_gold_data = bool(data.get("dataset_stats") or data.get("preprocessing_contract"))

    split_metrics = _split_metrics(data.get("dataset_stats"))
    label_card = _label_map_card(data.get("label_map"))
    profile_card = _profile_card(data.get("preprocessing_contract"), data.get("preprocessing_profile"))
    model_card = _model_configs_card(data.get("model_configs"))

    body = [
        # ── Status header ────────────────────────────────────────────────
        html.Div(
            [
                html.Div(
                    [
                        html.Div("Gold Layer", className="ops-section-kicker"),
                        html.H2("Gold Output Contract"),
                        html.P(
                            "Gold artifacts remain planned until B2 verification finds real folders or files. "
                            "Training consumes the blocks, split files, and metadata from this contract."
                        ),
                    ],
                    className="ops-section-head",
                ),
                html.Div(
                    [
                        small_status("Silver gate", readiness.get("status", "failed")),
                        small_status("Gold B2 status", verification.get("status", "planned")),
                    ],
                    className="silver-status-row",
                ),
            ],
            className="silver-section-head",
        ),

        # ── Target path + readiness gate ─────────────────────────────────
        html.Div(
            [
                html.Div(
                    [
                        html.H3("Target B2 Path"),
                        html.Code(contract["b2_uri"]),
                        html.Div(
                            "Training Control Room should use this dataset/prep version once the blocks and split metadata are generated.",
                            className="silver-source-copy",
                        ),
                        *([html.Div(
                            f"Local staging: {local_dir}",
                            className="gold-local-path",
                        )] if local_dir else []),
                    ],
                    className="ops-review-card gold-card",
                ),
                html.Div(
                    [
                        html.H3("Readiness Gate"),
                        html.Div(
                            "enabled" if can_generate else ("local only" if analytics_ready and not b2_verified else "blocked"),
                            className=f"silver-readiness silver-readiness-{'passed' if can_generate else ('partial' if analytics_ready and not b2_verified else 'failed')}",
                        ),
                        (
                            html.Div("Silver checks passed; Gold generation can proceed.", className="silver-pass-copy")
                            if can_generate
                            else html.Div(
                                "Silver data is available locally. Run preprocessing to upload Silver outputs to B2 before Gold can be generated.",
                                className="ops-muted-copy",
                            )
                            if analytics_ready and not b2_verified
                            else html.Ul([html.Li(item) for item in readiness.get("failed_checks") or []])
                        ),
                        html.Button(
                            "Generate Gold Outputs",
                            id="preproc-generate-gold-button",
                            className="btn btn-success ops-gold-button",
                            disabled=not can_generate,
                        ),
                    ],
                    className="ops-review-card gold-card",
                ),
            ],
            className="silver-two-col",
        ),
    ]

    if not has_gold_data:
        # ── No gold data yet — explain why ───────────────────────────────
        body.append(
            html.Div(
                [
                    html.Div(
                        "Gold metadata not yet available",
                        style={"color": "var(--app-text)", "fontWeight": 600, "marginBottom": "6px"},
                    ),
                    html.Div(
                        "Gold artifacts for this dataset/version have not been generated yet. "
                        "Complete preprocessing to produce Silver outputs, then trigger Gold generation. "
                        "Metrics will appear here once the artifacts land in B2 and are synced to local staging.",
                        className="ops-muted-copy",
                    ),
                ],
                style={
                    "margin": "20px 0",
                    "padding": "16px 20px",
                    "background": "var(--app-surface-2)",
                    "border": "1px solid rgba(125,180,255,0.12)",
                    "borderRadius": "10px",
                },
            )
        )
    else:
        # ── Split metrics ─────────────────────────────────────────────────
        if split_metrics:
            body += [
                html.Hr(className="preproc-tab-divider", style={"margin": "20px 0"}),
                html.Div("Dataset Splits", className="preproc-section-label"),
                split_metrics,
            ]

        # ── Profile + Label map side by side ──────────────────────────────
        if profile_card or label_card:
            body += [
                html.Hr(className="preproc-tab-divider", style={"margin": "20px 0"}),
                html.Div(
                    [c for c in [profile_card, label_card] if c],
                    className="gold-info-grid",
                ),
            ]

        # ── Model configs ─────────────────────────────────────────────────
        if model_card:
            body += [
                html.Hr(className="preproc-tab-divider", style={"margin": "20px 0"}),
                html.Div("Model Configurations", className="preproc-section-label"),
                model_card,
            ]

    # ── Contract table ────────────────────────────────────────────────────
    body += [
        html.Hr(className="preproc-tab-divider", style={"margin": "20px 0"}),
        html.Div(
            [
                html.Div("Expected Gold Folders and Files", className="preproc-section-label"),
                _contract_table(verification.get("rows") or []),
                html.Div(verification.get("error") or "", className="ops-error-copy"),
            ],
            className="ops-review-card gold-card gold-contract-table",
        ),
    ]

    return html.Div(body, className="gold-layer-section ops-panel")
