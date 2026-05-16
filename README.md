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
3. Remote Airflow v9 preprocessing:
   Dash builds the payload for `mls_preprocessing_v9`; the workstation stages
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

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
```
