import dash_bootstrap_components as dbc
from dash import html, dcc, dash_table


def single_kpi_card(title, value, subtitle=None, color="primary"):
    return dbc.Card(
        dbc.CardBody(
            [
                html.H6(str(title), className="kpi-title"),
                html.H3(str(value), className="kpi-value"),
                html.P(str(subtitle or ""), className="kpi-subtitle"),
            ]
        ),
        className=f"kpi-card border-{color}",
    )


def kpi_section(kpi_df):
    if kpi_df is None or kpi_df.empty:
        return dbc.Alert("No KPI data available for this dataset.", color="warning")

    required_cols = {"kpi_name", "kpi_value"}

    if not required_cols.issubset(set(kpi_df.columns)):
        return dbc.Alert(
            "KPI data exists, but required columns are missing: kpi_name, kpi_value.",
            color="danger",
        )

    cards = []

    for _, row in kpi_df.iterrows():
        cards.append(
            dbc.Col(
                single_kpi_card(
                    title=row.get("kpi_name", ""),
                    value=row.get("kpi_value", ""),
                    subtitle=row.get("kpi_description", ""),
                ),
                xs=12,
                sm=6,
                md=4,
                lg=3,
                className="mb-3",
            )
        )

    return dbc.Row(cards)


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
            "minWidth": "90px",
            "maxWidth": "280px",
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
        "style_data_conditional": [
            {
                "if": {"row_index": "odd"},
                "backgroundColor": "#171d23",
            }
        ],
    }


def empty_state_mount(component_id):
    return html.Div(id=component_id, className="analytics-empty-state")


def attribute_analytics_panel():
    table_style = _default_table_style()

    return dbc.Card(
        [
            dbc.CardHeader(html.H4("4A. Attribute Analytics")),
            dbc.CardBody(
                [
                    html.P(
                        "Shows normalized point attributes detected in the uploaded dataset. "
                        "Columns such as class, label, classification, semantic_label, and scalar_Label "
                        "are treated as one semantic_label attribute.",
                        className="text-muted",
                    ),

                    empty_state_mount("attribute-empty-state"),

                    dash_table.DataTable(
                        id="attribute-table",
                        columns=[
                            {"name": "Attribute", "id": "attribute"},
                            {"name": "Available", "id": "available"},
                            {"name": "Use", "id": "use"},
                        ],
                        data=[],
                        page_size=12,
                        sort_action="native",
                        filter_action="native",
                        **table_style,
                    ),

                    dcc.Graph(
                        id="attribute-chart",
                        config={"displayModeBar": True},
                    ),
                ]
            ),
        ],
        className="mb-4",
    )


def label_distribution_panel():
    table_style = _default_table_style()

    return dbc.Card(
        [
            dbc.CardHeader(html.H4("4B. Label Distribution")),
            dbc.CardBody(
                [
                    html.P(
                        "Shows binary building/non-building distribution and individual semantic-class proportions "
                        "computed from real point-cloud semantic labels and XML/JSON class mapping.",
                        className="text-muted",
                    ),

                    html.H5("Binary Building vs Non-building Distribution"),

                    empty_state_mount("label-empty-state"),

                    dash_table.DataTable(
                        id="label-table",
                        columns=[
                            {"name": "Class", "id": "class_name"},
                            {"name": "Point Count", "id": "point_count"},
                            {"name": "Proportion", "id": "proportion"},
                        ],
                        data=[],
                        page_size=10,
                        sort_action="native",
                        filter_action="native",
                        **table_style,
                    ),

                    dbc.Alert(
                        "Warning: if the dataset is class-imbalanced, overall accuracy may look high even when building IoU is weak. Use building IoU, mIoU, F1-score, Precision, and Recall.",
                        color="warning",
                        className="mt-3",
                    ),

                    dcc.Graph(
                        id="label-chart",
                        config={"displayModeBar": True},
                    ),

                    html.Hr(),

                    html.H5("Individual Semantic Class Distribution"),

                    html.P(
                        "Shows each original semantic class after joining real PLY/LAS semantic-label values "
                        "with the uploaded XML/JSON/YAML class mapping. For Toronto, scalar_Label is treated as semantic_label.",
                        className="text-muted",
                    ),

                    empty_state_mount("class-label-empty-state"),

                    dash_table.DataTable(
                        id="class-label-table",
                        columns=[
                            {"name": "Class ID from Point Cloud", "id": "class_id"},
                            {"name": "Class Name", "id": "class_name"},
                            {"name": "Coarse ID", "id": "coarse_id"},
                            {"name": "Coarse Class", "id": "coarse_class_name"},
                            {"name": "Binary Label", "id": "binary_label"},
                            {"name": "Point Count", "id": "point_count"},
                            {"name": "Proportion", "id": "proportion"},
                        ],
                        data=[],
                        page_size=10,
                        sort_action="native",
                        filter_action="native",
                        **table_style,
                    ),

                    dcc.Graph(
                        id="class-label-pie-chart",
                        config={"displayModeBar": True},
                    ),

                    dcc.Graph(
                        id="class-label-bar-chart",
                        config={"displayModeBar": True},
                    ),
                ]
            ),
        ],
        className="mb-4",
    )


def spatial_summary_panel():
    table_style = _default_table_style()

    return dbc.Card(
        [
            dbc.CardHeader(html.H4("5. Spatial Summary")),
            dbc.CardBody(
                [
                    html.P(
                        "Shows tile-wise point count, X/Y/Z ranges, density estimate, and spatial extent.",
                        className="text-muted",
                    ),

                    empty_state_mount("spatial-empty-state"),

                    dash_table.DataTable(
                        id="spatial-table",
                        columns=[
                            {"name": "Tile", "id": "tile_name"},
                            {"name": "Points", "id": "point_count"},
                            {"name": "X Range", "id": "x_range"},
                            {"name": "Y Range", "id": "y_range"},
                            {"name": "Z Range", "id": "z_range"},
                            {"name": "Density Estimate", "id": "density_estimate"},
                        ],
                        data=[],
                        page_size=10,
                        sort_action="native",
                        filter_action="native",
                        **table_style,
                    ),

                    dcc.Graph(
                        id="spatial-z-range-chart",
                        config={"displayModeBar": True},
                    ),

                    dcc.Graph(
                        id="spatial-point-count-chart",
                        config={"displayModeBar": True},
                    ),

                    dcc.Graph(
                        id="spatial-density-chart",
                        config={"displayModeBar": True},
                    ),
                ]
            ),
        ],
        className="mb-4",
    )


def rerun_viewer_panel():
    return dbc.Card(
        [
            dbc.CardHeader(html.H4("6. Rerun 3D Viewer")),
            dbc.CardBody(
                [
                    html.P(
                        "Generate a Rerun preview from real uploaded point-cloud attributes. "
                        "No mock point cloud or fake labels should be visualized.",
                        className="text-muted",
                    ),

                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    html.Label("Tile Selector"),
                                    dcc.Dropdown(
                                        id="rerun-tile-selector",
                                        options=[],
                                        placeholder="Select one tile",
                                        multi=False,
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                    html.Div(id="rerun-tile-state", className="mt-2"),
                                ],
                                xs=12,
                                md=3,
                            ),
                            dbc.Col(
                                [
                                    html.Label("Point Budget"),
                                    dcc.Dropdown(
                                        id="point-budget-selector",
                                        options=[
                                            {"label": "25k", "value": 25000},
                                            {"label": "50k", "value": 50000},
                                            {"label": "100k", "value": 100000},
                                            {"label": "250k", "value": 250000},
                                            {"label": "500k", "value": 500000},
                                            {"label": "1M", "value": 1000000},
                                            {"label": "2M", "value": 2000000},
                                            {"label": "4.5M", "value": 4500000},
                                            {"label": "5M", "value": 5000000},
                                        ],
                                        value=50000,
                                        clearable=False,
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                xs=12,
                                md=3,
                            ),
                            dbc.Col(
                                [
                                    html.Label("Color Mode"),
                                    dcc.Dropdown(
                                        id="color-mode-selector",
                                        options=[
                                            {
                                                "label": "Fast Single Color",
                                                "value": "solid",
                                            },
                                            {"label": "RGB", "value": "rgb"},
                                            {"label": "Height / Z", "value": "height"},
                                            {
                                                "label": "Intensity / Reflectance",
                                                "value": "intensity",
                                            },
                                            {
                                                "label": "Semantic Label",
                                                "value": "semantic_label",
                                            },
                                            {
                                                "label": "Building vs Non-building",
                                                "value": "binary_label",
                                            },
                                        ],
                                        value="solid",
                                        clearable=False,
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                xs=12,
                                md=3,
                            ),
                            dbc.Col(
                                [
                                    html.Label("View Mode"),
                                    dcc.Dropdown(
                                        id="view-mode-selector",
                                        options=[
                                            {"label": "Raw Cloud", "value": "raw"},
                                            {
                                                "label": "Semantic Label Cloud",
                                                "value": "semantic",
                                            },
                                            {
                                                "label": "Binary Label Cloud",
                                                "value": "binary",
                                            },
                                            {
                                                "label": "Z-Slice Preview",
                                                "value": "z_slice",
                                            },
                                        ],
                                        value="raw",
                                        clearable=False,
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                xs=12,
                                md=3,
                            ),
                        ],
                        className="g-3",
                    ),

                    html.Br(),

                    dbc.Button(
                        "Generate Rerun Recording",
                        id="load-rerun-button",
                        color="info",
                    ),

                    html.Div(
                        id="rerun-viewer-placeholder",
                        children=[
                            html.Br(),
                            dbc.Alert(
                                "Select a dataset, choose one tile, then generate a Rerun recording from real PLY/LAS fields.",
                                color="info",
                            ),
                        ],
                    ),
                ]
            ),
        ],
        className="mb-4",
    )
