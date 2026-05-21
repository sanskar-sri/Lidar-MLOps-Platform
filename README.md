# data-platform

Dash controller for the MLS building-identification workflow. The app does not
run heavy preprocessing or training locally; it registers datasets, checks
remote health, creates Airflow payloads, and tracks outputs written back to the
Backblaze B2 data lake.

## Medallion Workflow

The preprocessing package in `/Users/sanskarsrivastava/Desktop/preprocessing`
defines the current storage and execution contract:

1. Bronze raw input:
   `bronze_raw_data/<dataset_id>/source_files/tiles/` stores source
   `.ply`, `.las`, and `.laz` tiles. Optional label maps live under
   `bronze_raw_data/<dataset_id>/source_files/label_maps/`.
2. Metadata profiling:
   dataset registry JSON and analytics parquet files are produced under
   `metadata/` and `metadata_analytics/<dataset_id>/`.
3. Remote Airflow preprocessing:
   Dash builds the payload for `lidar_preprocessing_pipeline`; the workstation stages
   bronze inputs and runs `preprocess_mls_v9_compat.py`.
4. Silver conformed cloud:
   `silver_preprocessed_data/<dataset_id>/<prep_version>/` stores the
   voxelised, offset-normalized, feature-enriched `processed_cloud.npz`.
5. Gold model-ready data:
   `gold_model_ready_data/<dataset_id>/<prep_version>/` stores PointNet++ and
   RandLA-Net blocks, Pointcept scenes, splits, and evaluation artifacts.
6. Downstream training and inference:
   remote training consumes gold data and writes model runs, segmentation
   outputs, logs, MLflow records, and DVC context.

## Data Explorer Raw Uploads

The Data Explorer page now supports production-oriented raw MLS dataset intake
into the B2 bronze layer. The standard storage layout is:

```text
b2://Building-Identification-MLS/bronze_raw_data/<dataset_id>/source_files/tiles/
b2://Building-Identification-MLS/bronze_raw_data/<dataset_id>/source_files/label_maps/
b2://Building-Identification-MLS/bronze_raw_data/<dataset_id>/manifests/
b2://Building-Identification-MLS/metadata/datasets/<dataset_id>.json
b2://Building-Identification-MLS/metadata_analytics/<dataset_id>/
```

Supported raw point-cloud tiles are `.ply`, `.las`, and `.laz`. XML, JSON, YAML,
and YML files are treated as optional label-map inputs.

### Browser Upload Mode

Browser upload is for deployed users whose data is on their own workstation.
Files or folders are selected in the UI and streamed to Dash in resumable chunks.
Dash stages those chunks under `data/browser_upload_staging/`, then uploads the
assembled files to B2, writes upload and checksum manifests, extracts dataset
metadata, and publishes analytics parquet files.

The browser phase reports byte-level progress while chunks are moving from the
browser to Dash. The server-to-B2 phase reports file/stage progress and records
retry attempts; large files can stay on the same file name while B2 is receiving
and committing the object.

### Admin Server Path Mode

Admin path upload is for folders already visible to the Dash server/container.
For local Docker runs, host paths under:

```text
/Users/sanskarsrivastava/Desktop/Datasets
```

are mapped into the container as:

```text
/datasets
```

For example, the host folder
`/Users/sanskarsrivastava/Desktop/Datasets/torronto` is read by Dash as
`/datasets/torronto`. This avoids browser chunk staging, but it still performs
the same server-to-B2 upload, B2 verification, manifest creation, metadata
profiling, and analytics publishing steps.

### Reliability Features Added

- Folder upload accepts multiple raw tiles plus label maps and routes each file
  to the correct bronze subfolder.
- Browser upload supports large files without loading the full dataset into Dash
  memory at once.
- Server-staged browser uploads can resume B2 finalization from already staged
  files, so a B2 timeout does not require selecting and sending the folder again.
- B2 upload finalization records per-file status, attempts, errors, verified
  object size, B2 file ID, and clean B2 object names.
- Upload progress is persisted in `data/upload_progress/<dataset_id>.json`.
- Browser upload session state is persisted in `data/upload_sessions/`.
- Upload manifests are stored locally under `data/local_staging/<dataset_id>/`
  and uploaded to `bronze_raw_data/<dataset_id>/manifests/`.
- The Data Explorer cleanup flow can remove stale raw, metadata, analytics,
  progress, session, and app-owned local staging artifacts while preserving
  selected dataset IDs.

The verified full Toronto upload path completed successfully for `id-full-3`
with five files uploaded, zero failed files, B2 verification, manifests,
metadata JSON, and parquet analytics generation.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
```
