from dash import html


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


def platform_brand(
    subtitle="Data Explorer · Medallion Preprocessing · Training · Rerun",
    visual_context="ops",
):
    prefix = _prefix_for_context(visual_context)
    return html.Div(
        [
            html.Div(
                className=_class_names(
                    "app-brand-badge",
                    "platform-brand-mark",
                    "ops-brand-mark",
                    f"{prefix}-brand-grid",
                ),
                **{"aria-hidden": "true"},
            ),
            html.Div(
                [
                    html.Div(
                        "LiDAR Platform",
                        className=_class_names(
                            "app-brand-title",
                            "platform-brand-title",
                            "ops-brand-title",
                            f"{prefix}-brand-title",
                        ),
                    ),
                    html.Div(
                        subtitle,
                        className=_class_names(
                            "app-brand-subtitle",
                            "platform-brand-subtitle",
                            "ops-brand-subtitle",
                            f"{prefix}-brand-subtitle",
                        ),
                    ),
                ],
                className=_class_names("app-brand-copy", "platform-brand-copy", f"{prefix}-brand-copy"),
            ),
        ],
        className=_class_names("app-brand-lockup", "platform-brand", "ops-brand", f"{prefix}-brand"),
    )
