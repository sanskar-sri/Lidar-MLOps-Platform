from dash import html


def lidar_particle_background(canvas_id, class_name="", aria_label=None):
    classes = "lidar-particle-canvas"
    if class_name:
        classes = f"{classes} {class_name}"
    if aria_label:
        props = {"id": canvas_id, "className": classes, "aria-label": aria_label}
    else:
        props = {"id": canvas_id, "className": classes, "aria-hidden": "true"}
    return html.Canvas(**props)
