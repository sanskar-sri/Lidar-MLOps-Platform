import dash_bootstrap_components as dbc
from dash import html


def dataset_lineage_panel():
    return dbc.Card(
        [
            dbc.CardHeader(html.H4("Lineage Flow")),
            dbc.CardBody(
                [
                    html.P(
                        "Shows the medallion data flow from bronze raw upload through metadata profiling, silver preprocessing, gold model artifacts, and downstream segmentation outputs.",
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
