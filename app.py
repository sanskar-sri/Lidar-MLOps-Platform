import os

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc
from flask import jsonify, request

from services.browser_upload_service import (
    abort_browser_upload_session,
    complete_browser_upload_file,
    complete_browser_upload_session,
    create_browser_upload_session,
    load_browser_upload_session,
    receive_browser_upload_chunk,
)


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
                    dbc.NavLink(
                        "Postprocessing",
                        href="/postprocessing",
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


def _json_payload():
    return request.get_json(silent=True) or {}


def _json_error(error, status_code=400):
    return jsonify({"ok": False, "error": str(error)}), status_code


@app.server.route("/api/browser-upload/sessions", methods=["POST"])
def api_create_browser_upload_session():
    try:
        session = create_browser_upload_session(_json_payload())
        return jsonify({"ok": True, "session": session})
    except Exception as exc:
        return _json_error(exc)


@app.server.route("/api/browser-upload/sessions/<session_id>", methods=["GET"])
def api_get_browser_upload_session(session_id):
    try:
        return jsonify({"ok": True, "session": load_browser_upload_session(session_id)})
    except FileNotFoundError as exc:
        return _json_error(exc, status_code=404)
    except Exception as exc:
        return _json_error(exc)


@app.server.route("/api/browser-upload/chunk", methods=["POST"])
def api_receive_browser_upload_chunk():
    try:
        return jsonify(
            {"ok": True, **receive_browser_upload_chunk(request.form, request.files)}
        )
    except Exception as exc:
        return _json_error(exc)


@app.server.route("/api/browser-upload/complete-file", methods=["POST"])
def api_complete_browser_upload_file():
    try:
        result = complete_browser_upload_file(_json_payload())
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _json_error(exc)


@app.server.route("/api/browser-upload/complete-session", methods=["POST"])
def api_complete_browser_upload_session():
    try:
        session = complete_browser_upload_session(_json_payload())
        return jsonify({"ok": True, "session": session})
    except Exception as exc:
        return _json_error(exc)


@app.server.route("/api/browser-upload/abort", methods=["POST"])
def api_abort_browser_upload_session():
    try:
        session = abort_browser_upload_session(_json_payload())
        return jsonify({"ok": True, "session": session})
    except Exception as exc:
        return _json_error(exc)


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
