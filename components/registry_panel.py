import dash_bootstrap_components as dbc
from dash import html, dash_table


def dataset_registry_panel():
    return dbc.Card(
        [
            dbc.CardHeader(html.H4("2. Dataset Registry")),
            dbc.CardBody(
                [
                    html.P(
                        "Select a dataset from the registry to load KPIs, metadata, analytics, readiness checks, and lineage.",
                        className="text-muted",
                    ),

                    dash_table.DataTable(
                        id="dataset-registry-table",
                        columns=[
                            {"name": "Dataset ID", "id": "dataset_id"},
                            {"name": "Dataset Name", "id": "dataset_name"},
                            {"name": "Files", "id": "total_files"},
                            {"name": "Points", "id": "total_points"},
                            {"name": "Labels", "id": "labels"},
                            {"name": "Status", "id": "status"},
                        ],
                        data=[],
                        page_size=8,

                        # Radio-button row selector
                        row_selectable="single",
                        selected_rows=[],

                        # Table interaction
                        sort_action="native",
                        filter_action="native",

                        style_table={
                            "overflowX": "auto",
                            "width": "100%",
                            "border": "1px solid #303943",
                            "borderRadius": "8px",
                        },
                        style_cell={
                            "textAlign": "left",
                            "padding": "8px",
                            "fontFamily": "Arial",
                            "fontSize": "14px",
                            "whiteSpace": "normal",
                            "height": "auto",
                            "minWidth": "100px",
                            "maxWidth": "280px",
                            "backgroundColor": "#15191d",
                            "color": "#edf2f7",
                            "border": "1px solid #303943",
                        },
                        style_header={
                            "fontWeight": "bold",
                            "backgroundColor": "#1b2127",
                            "color": "#edf2f7",
                            "border": "1px solid #303943",
                        },
                        style_data={
                            "backgroundColor": "#15191d",
                            "color": "#edf2f7",
                            "border": "1px solid #303943",
                        },
                        style_data_conditional=[
                            {
                                "if": {"row_index": "odd"},
                                "backgroundColor": "#171d23",
                            }
                        ],
                    ),

                    html.Br(),

                    dbc.ButtonGroup(
                        [
                            dbc.Button(
                                "Load Dataset",
                                id="load-dataset-button",
                                color="secondary",
                                outline=True,
                            ),
                            dbc.Button(
                                "View Metadata",
                                id="view-metadata-button",
                                color="secondary",
                                outline=True,
                            ),
                            dbc.Button(
                                "Run Preprocessing",
                                id="run-preprocessing-button",
                                color="success",
                                outline=True,
                            ),
                            dbc.Button(
                                "Open in Rerun",
                                id="open-rerun-button",
                                color="info",
                                outline=True,
                            ),
                        ],
                        className="mb-2",
                    ),

                    html.Div(id="registry-action-message", className="mt-3"),
                ]
            ),
        ]
    )
