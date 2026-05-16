import os

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc


print("Loading dashboard pages and services...", flush=True)

app = dash.Dash(
    __name__,
    use_pages=True,
    assets_folder="assets",
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
    prevent_initial_callbacks="initial_duplicate",
)

app.title = "Building Identification on Mobile LiDAR Data"


app.layout = dbc.Container(
    fluid=True,
    className="app-shell",
    children=[
        dcc.Location(id="url"),

        html.Div(
            html.Div(
                [
                    html.H3("Building Identification on Mobile LiDAR Data"),
                    html.P(
                        "Data Explorer, preprocessing, training, and visualization dashboard."
                    ),
                ],
                className="app-header-content",
            ),
            className="app-header",
        ),

        html.Div(
            dbc.Nav(
                [
                    dbc.NavLink(
                        "Home",
                        href="/",
                        active="exact",
                    ),
                    dbc.NavLink(
                        "Data Explorer",
                        href="/data-explorer",
                        active="exact",
                    ),
                    dbc.NavLink(
                        "Control Panel",
                        href="/control-panel",
                        active="exact",
                    ),
                    dbc.NavLink(
                        "Preprocessing",
                        href="/preprocessing",
                        active="exact",
                    ),
                    dbc.NavLink(
                        "Training",
                        href="/training",
                        active="exact",
                    ),
                ],
                pills=True,
                className="top-nav",
            ),
            className="app-nav-strip",
        ),

        html.Main(
            dash.page_container,
            className="page-frame",
        ),
    ],
)


if __name__ == "__main__":
    debug_enabled = os.getenv("DASH_DEBUG", "0").strip() == "1"
    port = int(os.getenv("DASH_PORT", "8051").strip() or "8051")

    print(
        f"Starting Dash server at http://127.0.0.1:{port}/ "
        f"(debug={'on' if debug_enabled else 'off'})",
        flush=True,
    )
    app.run(
        debug=debug_enabled,
        host=os.getenv("DASH_HOST", "0.0.0.0"),
        port=port,
        use_reloader=False,
    )
