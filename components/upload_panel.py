import dash_bootstrap_components as dbc
from dash import html, dcc


def upload_raw_data_panel():
    return dbc.Card(
        [
            dbc.CardHeader(html.H4("1. Upload Raw Data to B2")),
            dbc.CardBody(
                [
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("Bucket"),
                                    dbc.Input(
                                        id="bucket-input",
                                        value="Building-Identification-MLS",
                                        disabled=True,
                                    ),
                                ],
                                width=4,
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Dataset ID"),
                                    dbc.Input(
                                        id="dataset-id-input",
                                        placeholder="Example: paris_lille_3d",
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                width=4,
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Dataset Name"),
                                    dbc.Input(
                                        id="dataset-name-input",
                                        placeholder="Example: Paris-Lille 3D",
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                width=4,
                            ),
                        ],
                        className="mb-3",
                    ),

                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("Upload Mode"),
                                    dcc.Dropdown(
                                        id="upload-mode-dropdown",
                                        options=[
                                            {
                                                "label": "Single Tile",
                                                "value": "single_tile",
                                            },
                                            {
                                                "label": "Multiple Tiles",
                                                "value": "multiple_tiles",
                                            },
                                            {
                                                "label": "Folder Upload",
                                                "value": "folder_upload",
                                            },
                                        ],
                                        value="folder_upload",
                                        clearable=False,
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                width=4,
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Description"),
                                    dbc.Textarea(
                                        id="dataset-description-input",
                                        placeholder="Short description of the dataset.",
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                width=8,
                            ),
                        ],
                        className="mb-3",
                    ),

                    html.Div(
                        [
                            html.Strong("Standard B2 storage structure:"),
                            html.Br(),
                            html.Code(
                                "b2://Building-Identification-MLS/bronze_raw_data/<dataset_id>/source_files/tiles/"
                            ),
                            html.Br(),
                            html.Span(
                                "for raw point cloud tiles such as .ply, .las, .laz"
                            ),
                            html.Br(),
                            html.Code(
                                "b2://Building-Identification-MLS/bronze_raw_data/<dataset_id>/source_files/label_maps/"
                            ),
                            html.Br(),
                            html.Span(
                                "for XML/JSON/YAML label mapping files"
                            ),
                            html.Br(),
                            html.Code(
                                "b2://Building-Identification-MLS/bronze_raw_data/<dataset_id>/manifests/"
                            ),
                            html.Br(),
                            html.Span(
                                "for upload_manifest.json and checksum_manifest.json"
                            ),
                        ],
                        className="helper-text",
                    ),

                    html.Hr(),

                    html.H5("Small File Upload"),

                    dbc.Alert(
                        [
                            html.Strong("Use only for small test files. "),
                            "Do not use this browser upload for large 700 MB MLS/LiDAR tiles, because browser upload uses memory-heavy base64 transfer.",
                        ],
                        color="info",
                    ),

                    dcc.Upload(
                        id="raw-file-upload",
                        children=html.Div(
                            [
                                "Drag and drop small files here, or ",
                                html.A("click to select files"),
                            ]
                        ),
                        className="upload-box",
                        multiple=True,
                    ),

                    html.Br(),

                    dbc.Button(
                        "Upload Selected Small Files to B2",
                        id="upload-button",
                        color="primary",
                    ),

                    html.Hr(),

                    html.H5("Large Point Cloud Tile Upload"),

                    dbc.Alert(
                        "Use this for one large .ply/.las/.laz tile. The file uploads directly from local disk to B2 without browser base64 loading.",
                        color="warning",
                    ),

                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("Local Tile Path"),
                                    dbc.Input(
                                        id="local-file-path-input",
                                        placeholder="/Users/sanskarsrivastava/Desktop/paris_lille_raw/Lille1_1.ply",
                                        type="text",
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                width=8,
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Optional Label Map Path"),
                                    dbc.Input(
                                        id="local-label-map-path-input",
                                        placeholder="/Users/sanskarsrivastava/Desktop/paris_lille_raw/coarse_classes.xml",
                                        type="text",
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                width=4,
                            ),
                        ],
                        className="mb-3",
                    ),

                    dbc.Button(
                        "Upload Tile + Optional Label Map to B2",
                        id="large-file-upload-button",
                        color="warning",
                    ),

                    html.Hr(),

                    html.H5("Large Folder Upload"),

                    dbc.Alert(
                        "Use this when your folder contains multiple .ply tiles and label-map files. Tiles go to source_files/tiles/ and XML/JSON/YAML maps go to source_files/label_maps/.",
                        color="secondary",
                    ),

                    dbc.Label("Local Folder Path"),
                    dbc.Input(
                        id="local-folder-path-input",
                        placeholder="/Users/sanskarsrivastava/Desktop/paris_lille_raw",
                        type="text",
                        persistence=True,
                        persistence_type="session",
                    ),

                    html.Br(),

                    dbc.Button(
                        "Upload Folder to B2",
                        id="folder-upload-button",
                        color="dark",
                    ),

                    html.Hr(),

                    html.H5("Upload Progress"),

                    html.P(
                        "This progress bar updates during folder upload. For one large tile, it may update after the file-level upload step completes.",
                        className="text-muted",
                    ),

                    dcc.Interval(
                        id="upload-progress-interval",
                        interval=1000,
                        n_intervals=0,
                        disabled=False,
                    ),

                    dbc.Progress(
                        id="upload-progress-bar",
                        value=0,
                        label="0%",
                        striped=True,
                        animated=True,
                        style={"height": "24px"},
                        className="mb-2",
                    ),

                    html.Div(id="upload-progress-text", className="mb-3"),

                    html.Div(id="upload-message", className="mt-3"),
                    html.Div(id="upload-result-details", className="mt-3"),

                    html.Hr(),

                    html.H5("Delete Dataset / Tile"),

                    dbc.Alert(
                        [
                            html.Strong("Danger zone. "),
                            "This can delete files from B2 and remove local metadata/analytics. Test this first with a small test dataset.",
                        ],
                        color="danger",
                    ),

                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("Delete Dataset ID"),
                                    dbc.Input(
                                        id="delete-dataset-id-input",
                                        placeholder="Example: paris_lille_3d",
                                        type="text",
                                        persistence=True,
                                        persistence_type="session",
                                    ),
                                ],
                                width=6,
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Optional Tile Name"),
                                    dbc.Input(
                                        id="delete-tile-name-input",
                                        placeholder="Example: Lille1_1.ply. Leave empty to delete full dataset.",
                                        type="text",
                                    ),
                                ],
                                width=6,
                            ),
                        ],
                        className="mb-3",
                    ),

                    dbc.Button(
                        "Delete from B2 and Local Metadata",
                        id="delete-dataset-button",
                        color="danger",
                    ),

                    html.Div(id="delete-message", className="mt-3"),
                ]
            ),
        ]
    )