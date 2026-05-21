# LiDAR Data Platform — Controller Dashboard

A Dash-based operations dashboard that orchestrates the full MLS (Mobile LiDAR Scanning) pipeline —
from raw data upload through preprocessing execution. The app does **not** run heavy compute locally.
It manages data, builds payloads, and delegates execution to remote infrastructure.

---

## What Has Been Built

### Stage 1 — Raw Data Intake (Bronze Layer)

- Upload raw `.ply` / `.las` / `.laz` point-cloud tiles and `.xml` label maps to **Backblaze B2**
- Two upload modes:
  - **Admin path** — folder already mounted in the container (`/datasets/`) → uploads directly to B2, no browser staging
  - **Browser upload** — user's files streamed in resumable chunks → assembled on server → pushed to B2
- Per-file SHA-1 checksum verification after every upload
- Upload and checksum manifests written to B2 (`bronze_raw_data/<dataset_id>/manifests/`)
- Upload progress persisted to `data/upload_progress/<dataset_id>.json`
- After upload: automatic metadata extraction from local tile copies (no re-download from B2)

### Stage 2 — Dataset Registration and Analytics

- Tiles are profiled: point counts, bounding boxes, attribute columns, label histograms, class mappings
- Nine analytics Parquet files written locally and pushed to B2:
  - `file_summary`, `attribute_summary`, `label_distribution`, `class_label_distribution`
  - `spatial_summary`, `dashboard_kpis`, `quality_checks`, `class_mapping`, `class_mapping_summary`
- Dataset registry JSON written locally and to `b2://Building-Identification-MLS/metadata/datasets/<dataset_id>.json`
- Data Explorer dashboard renders live analytics charts from local Parquet cache

### Stage 3 — Preprocessing Trigger (Silver + Gold)

- Mac UI sends a **minimal conf** to the remote Airflow DAG:
  ```json
  { "dataset_id": "fui9", "mode": "train", "run_id": "fui9_prep_v001_..." }
  ```
  The workstation owns all defaults (voxel size, block size, workers, splits, etc.)
- `prep_version` is omitted by default — workstation auto-increments from last successful run (`prep_v001` on first run)
- Full conf (all ~50 parameters) is still persisted locally to `data/airflow_preprocessing_requests/` for audit
- After trigger: 5-second polling of Airflow REST API for DAG run status and task progress
- On DAG success: automatic Silver artifact verification against B2
- Silver and Gold output sections unlock with real metadata from B2

---

## Tech Stack

### Dashboard (this repo)

| Layer | Technology |
|---|---|
| UI framework | [Dash 2.x](https://dash.plotly.com/) + [Dash Bootstrap Components](https://dash-bootstrap-components.opensource.faculty.ai/) |
| Charts | Plotly |
| Language | Python 3.11 |
| Data handling | Pandas, PyArrow (Parquet) |
| Point cloud reading | Open3D, plyfile, laspy + lazrs |
| 3D visualization | [Rerun SDK](https://rerun.io/) |
| Config | python-dotenv |

### Storage

| Layer | Technology |
|---|---|
| Data lake | [Backblaze B2](https://www.backblaze.com/cloud-storage) (`Building-Identification-MLS` bucket) |
| B2 SDK | b2sdk (native) + boto3/s3 (S3-compatible fallback) |
| Local cache | `data/` directory (mounted as Docker volume) |

### Orchestration (remote)

| Layer | Technology |
|---|---|
| Pipeline scheduler | Apache Airflow (remote workstation) |
| Preprocessing script | `preprocess_mls_v9_compat.py` — runs on GPU worker |
| Experiment tracking | MLflow (`http://100.90.110.60:5001`) |
| Data versioning | DVC (`b2remote`) |

### Infrastructure

| Layer | Technology |
|---|---|
| Containerisation | Docker + Docker Compose |
| Dash container | `lidar-dash` on port `8051` |
| MLflow container | `lidar-mlflow` on port `5001` |
| Host dataset mount | `/Users/sanskarsrivastava/Desktop/Datasets` → `/datasets` inside container |

---

## Data Architecture (Medallion)

```
B2 Bucket: Building-Identification-MLS
│
├── bronze_raw_data/<dataset_id>/
│   ├── source_files/tiles/          ← raw .ply / .las / .laz tiles
│   ├── source_files/label_maps/     ← .xml label mapping files
│   └── manifests/                   ← upload_manifest.json, checksum_manifest.json
│
├── metadata/datasets/<dataset_id>.json        ← dataset registry
├── metadata_analytics/<dataset_id>/           ← 9x Parquet analytics files
│
├── silver_preprocessed_data/<dataset_id>/<prep_version>/
│   ├── processed_cloud_meta.json
│   ├── silver_stats.json
│   └── silver_density_grid.parquet
│
├── gold_model_ready_data/<dataset_id>/<prep_version>/
│   ├── training/ptv3/               ← PTv3 / Pointcept scenes
│   ├── training/traditional/blocks/ ← PointNet++ / RandLA-Net blocks
│   ├── artifacts/meta/              ← label_map, splits, dataset_stats, contract
│   └── artifacts/eval/              ← split_stats, density_report, class_weights
│
└── logs/<dataset_id>/<run_id>/      ← preprocessing run logs
```

---

## Pages

| Page | Path | Purpose |
|---|---|---|
| Home | `/` | Platform overview and live health |
| Data Explorer | `/data-explorer` | Upload raw data, browse datasets, view analytics |
| Preprocessing | `/preprocessing` | Configure and trigger Airflow preprocessing runs |
| Training | `/training` | Monitor remote training jobs |
| Postprocessing | `/postprocessing` | Review model outputs |
| Control Panel | `/control-panel` | Compute node health, service status |

---

## Callback Wiring (Preprocessing Page)

```
[User clicks Start Preprocessing]
        │
        ▼
handle_preprocessing_action()
  ├── builds full conf (display + local record)
  ├── saves to data/airflow_preprocessing_requests/<run_id>.json
  ├── builds minimal conf {dataset_id, mode, run_id}
  ├── POST /api/v1/dags/lidar_preprocessing_pipeline/dagRuns  → Airflow
  └── dag_run_store ← {dag_id, dag_run_id, state, b2_silver_prefix, prep_version}
        │
        ▼ (every 5s)
poll_airflow_status()
  ├── GET /api/v1/dags/.../dagRuns/<run_id>       → DAG state
  ├── GET /api/v1/dags/.../taskInstances          → task progress
  ├── updates airflow_status_store + progress UI
  └── disables interval on success / failed
        │
        ▼ (on state == "success")
verify_silver_outputs()          ← auto-triggered, uses b2_silver_prefix from dag_run_store
  ├── lists B2 silver prefix
  ├── checks for processed_cloud_meta.json, silver_stats.json, silver_density_grid.parquet
  └── silver_status_store ← {status, rows, verified_count}
        │
        ▼
render_output_layers()
  ├── loads Silver metadata + Parquet from local cache or B2
  ├── computes silver readiness (gates gold unlock)
  ├── renders Silver analytics section
  └── renders Gold output contract (planned → generated as files appear)
```

---

## Airflow DAGs (on remote workstation)

| DAG | Schedule | Purpose |
|---|---|---|
| `lidar_preprocessing_pipeline` | Triggered manually | Runs `preprocess_mls_v9_compat.py` on GPU worker |
| `dag_health_b2` | Every 90s | B2 reachability and prefix listing check |
| `dag_health_remote` | Every 90s | MLflow, DVC, GPU (nvidia-smi), OS health check |

---

## Local Development

```bash
# Create and activate virtualenv
python3 -m venv .venvvv
source .venvvv/bin/activate
pip install -r requirements.txt

# Copy and fill in credentials
cp .env.example .env   # set B2_KEY_ID, B2_APPLICATION_KEY, AIRFLOW_BASE_URL, etc.

# Run via Docker (recommended)
docker compose up --build
# App: http://localhost:8051
# MLflow: http://localhost:5001
```

### Required `.env` keys

```
B2_KEY_ID=
B2_APPLICATION_KEY=
B2_BUCKET_NAME=Building-Identification-MLS
AIRFLOW_BASE_URL=http://<airflow-host>:8080
AIRFLOW_USERNAME=
AIRFLOW_PASSWORD=
MLFLOW_TRACKING_URI=http://100.90.110.60:5001
```

---

## Key Bugs Fixed

| Bug | Root cause | Fix |
|---|---|---|
| `IndexError: list index out of range` (HTTP 500 on preprocessing page) | `update_preprocessing_preview` fired on initial page load before all 38 tab inputs were initialized | Added `prevent_initial_call=True` |
| Verify section used stale UI state if user changed fields after trigger | `dag_run_store` didn't carry `b2_silver_prefix` or `prep_version` | Store both at trigger time; verify callbacks prefer stored values |
| Airflow trigger sent full ~50-field conf | Remote workstation expected minimal conf and owns its own defaults | Mac now sends `{dataset_id, mode, run_id}`; workstation handles everything else |
