import dash_bootstrap_components as dbc
from dash import html


def dataset_lineage_panel():
    return dbc.Card(
        [
            dbc.CardHeader(html.H4("8. Dataset Lineage Preview")),
            dbc.CardBody(
                [
                    html.P(
                        "Shows the expected data flow from raw B2 upload to metadata extraction, preprocessing, model outputs, and final clustered building outputs.",
                        className="text-muted",
                    ),

                    html.Div(
                        id="lineage-content",
                        children=dbc.Alert(
                            "Select a dataset from the Dataset Registry to view lineage.",
                            color="info",
                        ),
                    ),
                ]
            ),
        ]
    )