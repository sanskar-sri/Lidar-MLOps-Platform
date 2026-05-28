from dash import dcc, html

from components.platform_brand import platform_brand
from components.platform_theme import NAV_GROUPS, _active_label, _group_is_active, ops_nav


_CONTEXT_PREFIX = {
    "home": "lp",
    "lp": "lp",
    "data-explorer": "de",
    "data_explorer": "de",
    "de": "de",
    "ops": "ops",
}


def _prefix_for_context(visual_context):
    return _CONTEXT_PREFIX.get(visual_context or "ops", "ops")


def _class_names(*names):
    tokens = []
    for name in names:
        for token in str(name or "").split():
            if token not in tokens:
                tokens.append(token)
    return " ".join(tokens)


def _nav_link_kwargs(path, link_id_scope, variant):
    if not link_id_scope:
        return {}
    return {
        "id": {
            "type": "platform-nav-link",
            "scope": link_id_scope,
            "variant": variant,
            "path": path,
        }
    }


def _item_href(item, href_overrides):
    path = item["path"]
    if href_overrides and path in href_overrides:
        return href_overrides[path]
    return path


def _compact_group(group, current, href_overrides=None, link_id_scope=None):
    return html.Div(
        [
            html.Div(group["label"], className="platform-compact-nav-group-label"),
            html.Div(
                [
                    dcc.Link(
                        item["label"],
                        href=_item_href(item, href_overrides),
                        className=(
                            "platform-compact-nav-item active"
                            if item["label"] == current
                            else "platform-compact-nav-item"
                        ),
                        **_nav_link_kwargs(item["path"], link_id_scope, "compact"),
                    )
                    for item in group["items"]
                ],
                className="platform-compact-nav-items",
            ),
        ],
        className=(
            "platform-compact-nav-group active"
            if _group_is_active(group, current)
            else "platform-compact-nav-group"
        ),
    )


def _compact_nav(active_path, href_overrides=None, link_id_scope=None):
    current = _active_label(active_path)
    return html.Details(
        [
            html.Summary("Menu", className="platform-compact-nav-toggle"),
            html.Div(
                [
                    _compact_group(group, current, href_overrides, link_id_scope)
                    for group in NAV_GROUPS
                ],
                className="platform-compact-nav-menu",
            ),
        ],
        className="platform-compact-nav",
    )


def platform_header(
    *,
    active_path,
    brand_subtitle,
    status_label,
    status_variant="active",
    visual_context="ops",
    href_overrides=None,
    link_id_scope=None,
):
    prefix = _prefix_for_context(visual_context)
    topbar_class = _class_names("platform-topbar", "ops-topbar", f"{prefix}-topbar")
    nav_class = _class_names("platform-nav", f"{prefix}-nav")
    badge_class = _class_names("platform-status-badge", f"{prefix}-live-pill")

    return html.Header(
        [
            platform_brand(brand_subtitle, visual_context=visual_context),
            html.Div(
                [
                    ops_nav(
                        active_path,
                        class_name=nav_class,
                        href_overrides=href_overrides,
                        link_id_scope=link_id_scope,
                    ),
                    _compact_nav(active_path, href_overrides, link_id_scope),
                ],
                className="platform-primary-nav-zone",
            ),
            html.Div(
                status_label,
                className=f"ops-live-pill ops-live-pill-{status_variant} {badge_class}".strip(),
            ),
        ],
        className=topbar_class,
    )
