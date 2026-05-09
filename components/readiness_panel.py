import dash_bootstrap_components as dbc
from dash import html, dash_table


def _default_table_style():
    return {
        "style_table": {
            "overflowX": "auto",
            "width": "100%",
            "border": "1px solid #303943",
            "borderRadius": "8px",
        },
        "style_cell": {
            "textAlign": "left",
            "padding": "8px",
            "fontFamily": "Arial",
            "fontSize": "14px",
            "whiteSpace": "normal",
            "height": "auto",
            "minWidth": "100px",
            "maxWidth": "360px",
            "backgroundColor": "#15191d",
            "color": "#edf2f7",
            "border": "1px solid #303943",
        },
        "style_header": {
            "fontWeight": "bold",
            "backgroundColor": "#1b2127",
            "color": "#edf2f7",
            "border": "1px solid #303943",
        },
        "style_data": {
            "backgroundColor": "#15191d",
            "color": "#edf2f7",
            "border": "1px solid #303943",
        },
    }


def preprocessing_readiness_panel():
    table_style = _default_table_style()

    return dbc.Card(
        [
            dbc.CardHeader(html.H4("7A. Preprocessing Readiness Check")),
            dbc.CardBody(
                [
                    html.P(
                        "Checks whether the selected dataset has the required geometry, label mapping, spatial bounds, and supported format for preprocessing.",
                        className="text-muted",
                    ),

                    dash_table.DataTable(
                        id="readiness-table",
                        columns=[
                            {"name": "Check", "id": "check"},
                            {"name": "Status", "id": "status"},
                            {"name": "Message", "id": "message"},
                        ],
                        data=[],
                        page_size=10,
                        sort_action="native",
                        filter_action="native",
                        style_data_conditional=[
                            {
                                "if": {
                                    "filter_query": "{status} = 'Pass'",
                                    "column_id": "status",
                                },
                                "backgroundColor": "rgba(93, 211, 158, 0.18)",
                                "color": "#bff4dc",
                                "fontWeight": "bold",
                            },
                            {
                                "if": {
                                    "filter_query": "{status} = 'Warning'",
                                    "column_id": "status",
                                },
                                "backgroundColor": "rgba(242, 184, 75, 0.18)",
                                "color": "#ffe0a0",
                                "fontWeight": "bold",
                            },
                            {
                                "if": {
                                    "filter_query": "{status} = 'Fail'",
                                    "column_id": "status",
                                },
                                "backgroundColor": "rgba(255, 107, 107, 0.18)",
                                "color": "#ffc4c4",
                                "fontWeight": "bold",
                            },
                            {
                                "if": {"row_index": "odd"},
                                "backgroundColor": "#171d23",
                            },
                        ],
                        **table_style,
                    ),

                    html.Br(),

                    dbc.Alert(
                        [
                            html.Strong("Rule: "),
                            "If semantic labels or a valid label mapping file are available, enable supervised training preprocessing. If labels are missing, enable inference-only preprocessing.",
                        ],
                        color="secondary",
                    ),
                ]
            ),
        ]
    )


def model_compatibility_panel():
    table_style = _default_table_style()

    return dbc.Card(
        [
            dbc.CardHeader(html.H4("7B. Model Output Compatibility")),
            dbc.CardBody(
                [
                    html.P(
                        "Shows whether model-ready preprocessing outputs have been generated for each planned architecture.",
                        className="text-muted",
                    ),

                    dash_table.DataTable(
                        id="model-compatibility-table",
                        columns=[
                            {"name": "Model", "id": "model"},
                            {"name": "Required Format", "id": "required_format"},
                            {"name": "Status", "id": "status"},
                        ],
                        data=[],
                        page_size=10,
                        sort_action="native",
                        filter_action="native",
                        style_data_conditional=[
                            {
                                "if": {
                                    "filter_query": "{status} = 'Ready'",
                                    "column_id": "status",
                                },
                                "backgroundColor": "rgba(93, 211, 158, 0.18)",
                                "color": "#bff4dc",
                                "fontWeight": "bold",
                            },
                            {
                                "if": {
                                    "filter_query": "{status} = 'Not generated'",
                                    "column_id": "status",
                                },
                                "backgroundColor": "rgba(242, 184, 75, 0.18)",
                                "color": "#ffe0a0",
                                "fontWeight": "bold",
                            },
                            {
                                "if": {
                                    "filter_query": "{status} = 'Failed'",
                                    "column_id": "status",
                                },
                                "backgroundColor": "rgba(255, 107, 107, 0.18)",
                                "color": "#ffc4c4",
                                "fontWeight": "bold",
                            },
                            {
                                "if": {"row_index": "odd"},
                                "backgroundColor": "#171d23",
                            },
                        ],
                        **table_style,
                    ),
                ]
            ),
        ]
    )
