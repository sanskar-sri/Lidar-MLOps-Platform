import os

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html
from dash.exceptions import PreventUpdate
from flask import jsonify, request

import pathlib

from services.dataset_selection import dataset_id_from_search
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
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="selected-dataset-id", storage_type="session"),
        html.Main(
            dash.page_container,
            className="page-frame",
        ),
    ],
)


@app.callback(
    Output("selected-dataset-id", "data", allow_duplicate=True),
    Input("url", "search"),
    State("selected-dataset-id", "data"),
    prevent_initial_call="initial_duplicate",
)
def sync_selected_dataset_from_url(search, current_dataset_id):
    dataset_id = dataset_id_from_search(search)
    if not dataset_id or dataset_id == str(current_dataset_id or "").strip():
        raise PreventUpdate
    return dataset_id


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


# ---------------------------------------------------------------------------
# Serve .rrd files so friends can open them in the Rerun web viewer at
# https://app.rerun.io/?url=<tunnel-url>/api/rerun-files/<filename>
# ---------------------------------------------------------------------------
_RERUN_OUTPUT_DIR = pathlib.Path(__file__).resolve().parent / "data" / "rerun_outputs"

@app.server.route("/api/rerun-files/<path:filename>", methods=["GET"])
def serve_rrd_file(filename):
    import re
    from flask import send_from_directory, abort
    # Only allow .rrd files, no path traversal
    if not re.fullmatch(r"[\w\-\.]+\.rrd", filename):
        abort(400)
    if not (_RERUN_OUTPUT_DIR / filename).exists():
        abort(404)
    return send_from_directory(
        str(_RERUN_OUTPUT_DIR),
        filename,
        mimetype="application/octet-stream",
        as_attachment=False,
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
