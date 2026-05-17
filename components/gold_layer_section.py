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


def build_gold_layer_section(dataset_id, prep_version, silver_readiness=None, verify_existing=True):
    if not dataset_id:
        return empty_state("Gold output contract", "Select a dataset to preview the Gold model-ready target path.")

    readiness = silver_readiness or {"status": "failed", "passed": False, "failed_checks": []}
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
    can_generate = bool(readiness.get("passed"))

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Gold Layer", className="ops-section-kicker"),
                            html.H2("Gold Output Contract"),
                            html.P("Gold artifacts remain planned until B2 verification finds real folders or files. Training consumes the blocks, split files, and metadata from this contract."),
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
                        ],
                        className="ops-review-card gold-card",
                    ),
                    html.Div(
                        [
                            html.H3("Readiness Gate"),
                            html.Div(
                                "enabled" if can_generate else "blocked",
                                className=f"silver-readiness silver-readiness-{'passed' if can_generate else 'failed'}",
                            ),
                            html.Ul([html.Li(item) for item in readiness.get("failed_checks") or []])
                            if not can_generate
                            else html.Div("Silver checks passed; Gold generation can proceed.", className="silver-pass-copy"),
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
            html.Div(
                [
                    html.H3("Expected Gold Folders and Files"),
                    _contract_table(verification.get("rows") or []),
                    html.Div(
                        verification.get("error") or "",
                        className="ops-error-copy",
                    ),
                ],
                className="ops-review-card gold-card gold-contract-table",
            ),
        ],
        className="gold-layer-section ops-panel",
    )
