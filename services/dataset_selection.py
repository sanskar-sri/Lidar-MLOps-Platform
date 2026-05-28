from urllib.parse import parse_qs, parse_qsl, urlencode


DATASET_CONTEXT_PATHS = {
    "/data-explorer",
    "/dataset-readiness",
    "/silver-gold-outputs",
    "/preprocessing",
    "/training",
    "/postprocessing",
    "/lineage-governance",
    "/monitoring-cost",
}


def dataset_id_from_search(search):
    """Return dataset_id from a Dash dcc.Location search string."""
    if not search:
        return ""
    try:
        params = parse_qs(str(search).lstrip("?"), keep_blank_values=False)
    except Exception:
        return ""
    return str((params.get("dataset_id") or [""])[0] or "").strip()


def resolve_selected_dataset_id(search=None, selected_dataset_id=None):
    """Resolve selected dataset using URL first, then the app session store."""
    url_dataset_id = dataset_id_from_search(search)
    if url_dataset_id:
        return url_dataset_id
    if selected_dataset_id:
        return str(selected_dataset_id).strip()
    return ""


def search_with_dataset_id(search, dataset_id):
    """Return a query string with dataset_id set, preserving other params."""
    dataset_id = str(dataset_id or "").strip()
    if not dataset_id:
        return search or ""

    pairs = [
        (key, value)
        for key, value in parse_qsl(str(search or "").lstrip("?"), keep_blank_values=True)
        if key != "dataset_id"
    ]
    pairs.append(("dataset_id", dataset_id))
    return "?" + urlencode(pairs)


def carries_dataset_context(pathname):
    path = str(pathname or "").split("?", 1)[0].rstrip("/") or "/"
    return path in DATASET_CONTEXT_PATHS
