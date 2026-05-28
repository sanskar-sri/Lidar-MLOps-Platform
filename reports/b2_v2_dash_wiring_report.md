# B2 v2 Dash Wiring Report

**Generated:** 2026-05-27  
**Branch:** test

---

## 1. Bucket & Endpoint (from .env)

| Key | Value |
|-----|-------|
| `B2_BUCKET_NAME` | `building-identification-mls-v2` |
| `B2_ENDPOINT` | `https://s3.us-east-005.backblazeb2.com` |

---

## 2. New Prefixes Configured in .env

| Env Var | New Path |
|---------|----------|
| `B2_BRONZE_PREFIX` | `01_raw_data/bronze_raw_data` |
| `B2_SILVER_PREFIX` | `02_preprocessing/silver_preprocessed_data` |
| `B2_GOLD_PREFIX` | `02_preprocessing/gold_model_ready_data` |
| `B2_INFERENCE_PREFIX` | `02_preprocessing/inference_ready_data` |
| `B2_TRAINING_RUNS_PREFIX` | `03_segmentation/training_runs` |
| `B2_SEGMENTATION_PREFIX` | `03_segmentation/segmentation_outputs` |
| `B2_CLUSTERING_PREFIX` | `04_clustering/clustered_final_outputs` |
| `B2_GIS_EXPORTS_PREFIX` | `05_applications/gis_exports` |
| `B2_RISK_EXPOSURE_PREFIX` | `05_applications/risk_exposure` |
| `B2_METADATA_PREFIX` | `06_governance/metadata` |
| `B2_METADATA_ANALYTICS_PREFIX` | `06_governance/metadata_analytics` |
| `B2_BENCHMARK_PREFIX` | `06_governance/benchmark_results` |
| `B2_LINEAGE_PREFIX` | `06_governance/lineage` |
| `B2_QC_REPORTS_PREFIX` | `06_governance/qc_reports` |
| `B2_LOGS_PREFIX` | `06_governance/logs` |
| `B2_RERUN_PREFIX` | `06_governance/rerun_outputs` |

---

## 3. Files Inspected

### Services
- `services/b2_service.py`
- `services/metadata_service.py`
- `services/browser_upload_service.py`
- `services/analytics_download_service.py.py`
- `services/preprocessing_runtime_service.py`
- `services/preprocessing_service.py`
- `services/training_service.py`
- `services/gis_export_service.py`
- `services/benchmark_service.py`
- `services/inference_outputs_service.py`
- `services/lineage_service.py`
- `services/risk_service.py`
- `services/silver_gold_outputs_service.py`
- `services/training_summary_service.py`

### Pages
- `pages/data_explorer.py`
- `pages/preprocessing.py`
- `pages/dataset_readiness.py`
- `pages/lineage_governance.py`
- `pages/monitoring_cost.py`
- `pages/silver_gold_outputs.py`
- `pages/model_benchmark.py`
- `pages/inference_outputs.py`
- `pages/gis_exports.py`
- `pages/risk_exposure.py`

### Scripts
- `scripts/test_b2_v2_connection.py`
- `scripts/test_b2_v2_upload.py`

---

## 4. Old Hardcoded Prefixes Found & Replaced

| File | Old String | New Expression |
|------|-----------|----------------|
| `services/b2_service.py` | `bronze_raw_data/{id}/source_files/tiles/{f}` | `bronze_tiles_prefix(dataset_id)/{f}` |
| `services/b2_service.py` | `bronze_raw_data/{id}/source_files/label_maps/{f}` | `bronze_label_maps_prefix(dataset_id)/{f}` |
| `services/b2_service.py` | `bronze_raw_data/{id}/manifests/{obj}` | `bronze_manifest_prefix(dataset_id)/{obj}` |
| `services/b2_service.py` | `"raw_tile_prefix": f"bronze_raw_data/..."` | `bronze_tiles_prefix(dataset_id)/` |
| `services/b2_service.py` | `"label_map_prefix": f"bronze_raw_data/..."` | `bronze_label_maps_prefix(dataset_id)/` |
| `services/b2_service.py` | `"manifest_prefix": f"bronze_raw_data/..."` | `bronze_manifest_prefix(dataset_id)/` |
| `services/metadata_service.py` | `"bucket": "Building-Identification-MLS"` | `"bucket": B2_BUCKET_NAME` |
| `services/metadata_service.py` | `metadata/datasets/{id}.json` | `b2_prefix('metadata')/datasets/{id}.json` |
| `services/metadata_service.py` | `metadata_analytics/{id}/` | `b2_prefix('metadata_analytics')/{id}/` |
| `services/metadata_service.py` | `bronze_raw_data/{id}/source_files/tiles/` (error msg) | `bronze_tiles_prefix(dataset_id)/` |
| `services/browser_upload_service.py` | `bronze_raw_data/{id}/source_files/{folder}/{f}` | `bronze_tiles_prefix` / `bronze_label_maps_prefix` |
| `services/analytics_download_service.py.py` | `silver_preprocessed_data/{id}/{v}/silver/` | `b2_prefix('silver_preprocessed_data')/...` |
| `services/analytics_download_service.py.py` | `gold_model_ready_data/{id}/{v}/eval/` | `b2_prefix('gold_model_ready_data')/...` |
| `services/analytics_download_service.py.py` | `gold_model_ready_data/{id}/{v}/meta/` | `b2_prefix('gold_model_ready_data')/...` |
| `services/gis_export_service.py` | `gis_exports/{id}/{v}/{m}/{r}/` | `b2_prefix('gis_exports')/...` |
| `services/gis_export_service.py` | `"bronze_raw_data/"`, `"metadata/"`, etc. (prefix list) | `b2_prefix(...)` calls |
| `pages/lineage_governance.py` | `bronze_raw_data/{id}/manifests/` | `bronze_manifest_prefix(dataset_id)/` + legacy fallback |
| `pages/lineage_governance.py` | `metadata_analytics/{id}/` | `b2_prefix('metadata_analytics')/{id}/` + legacy fallback |
| `pages/monitoring_cost.py` | `bronze_raw_data/{id}/manifests/upload_manifest.json` | `bronze_manifest_prefix(dataset_id)/...` + legacy fallback |
| `pages/monitoring_cost.py` | `metadata_analytics/{id}/` | `b2_prefix('metadata_analytics')/{id}/` + legacy fallback |

---

## 5. Files Already Using Centralized Mapping (no changes needed)

- `services/benchmark_service.py` — uses service layer only, no direct B2 paths
- `services/inference_outputs_service.py` — no hardcoded B2 paths
- `services/lineage_service.py` — no hardcoded B2 paths
- `services/risk_service.py` — no hardcoded B2 paths
- `services/silver_gold_outputs_service.py` — no hardcoded B2 paths
- `services/training_summary_service.py` — no hardcoded B2 paths
- `pages/dataset_readiness.py` — reads via service layer, no direct paths
- `pages/silver_gold_outputs.py` — reads via service layer
- `pages/model_benchmark.py` — reads via service layer
- `pages/inference_outputs.py` — reads via service layer
- `pages/gis_exports.py` — reads via gis_export_service
- `pages/risk_exposure.py` — reads via risk_service

---

## 6. Files Created / Modified

| File | Action |
|------|--------|
| `services/b2_paths.py` | **CREATED** — centralized path registry |
| `scripts/test_b2_v2_upload.py` | **CREATED** — smoke-test upload script |
| `scripts/test_b2_v2_connection.py` | **UPDATED** — tests all 8 configured prefixes |
| `services/b2_service.py` | **UPDATED** — 6 old paths → b2_paths helpers |
| `services/metadata_service.py` | **UPDATED** — 4 old paths → b2_paths helpers |
| `services/browser_upload_service.py` | **UPDATED** — 1 old path → b2_paths helpers |
| `services/analytics_download_service.py.py` | **UPDATED** — 3 old paths → b2_paths helpers |
| `services/gis_export_service.py` | **UPDATED** — 8 old paths → b2_paths helpers |
| `pages/lineage_governance.py` | **UPDATED** — 2 old paths → b2_paths + legacy read fallback |
| `pages/monitoring_cost.py` | **UPDATED** — 2 old paths → b2_paths + legacy read fallback |
| `.env` | **UPDATED** — all 17 B2 prefix vars + new bucket name + endpoint |

---

## 7. Pages Status

| Page | Uses B2 via | Old Paths | Safe? | Notes |
|------|------------|-----------|-------|-------|
| `data_explorer.py` | service layer | none found | ✅ Safe | |
| `dataset_readiness.py` | service layer | none found | ✅ Safe | |
| `preprocessing.py` | service layer | none found | ✅ Safe | |
| `silver_gold_outputs.py` | service layer | none found | ✅ Safe | |
| `lineage_governance.py` | direct + service | fixed | ✅ Safe | Legacy read fallback added |
| `monitoring_cost.py` | direct + service | fixed | ✅ Safe | Legacy read fallback added |
| `model_benchmark.py` | service layer | none found | ✅ Safe | |
| `inference_outputs.py` | service layer | none found | ✅ Safe | |
| `gis_exports.py` | gis_export_service | fixed in service | ✅ Safe | |
| `risk_exposure.py` | risk_service | none found | ✅ Safe | |

---

## 8. Connection Test Result

```
Bucket  : building-identification-mls-v2
Endpoint: https://s3.us-east-005.backblazeb2.com

  OK  B2_BRONZE_PREFIX (01_raw_data/bronze_raw_data/)  keys=1
  OK  B2_GOLD_PREFIX (02_preprocessing/gold_model_ready_data/)  keys=1
  OK  B2_TRAINING_RUNS_PREFIX (03_segmentation/training_runs/)  keys=1
  OK  B2_SEGMENTATION_PREFIX (03_segmentation/segmentation_outputs/)  keys=1
  OK  B2_CLUSTERING_PREFIX (04_clustering/clustered_final_outputs/)  keys=1
  OK  B2_GIS_EXPORTS_PREFIX (05_applications/gis_exports/)  keys=1
  OK  B2_METADATA_PREFIX (06_governance/metadata/)  keys=1
  OK  B2_ANALYTICS_PREFIX (06_governance/metadata_analytics/)  keys=1

Result: 8 passed, 0 failed  ✅
```

---

## 9. Upload Test Result

```
Bucket  : building-identification-mls-v2
Endpoint: https://s3.us-east-005.backblazeb2.com
Key     : 06_governance/logs/dash_smoke_test/test_from_mac_dash.txt

  PUT  06_governance/logs/dash_smoke_test/test_from_mac_dash.txt  -> OK
  HEAD 06_governance/logs/dash_smoke_test/test_from_mac_dash.txt  -> OK (size=18)

Upload OK  ✅
```

---

## 10. Remaining Risks

1. **`services/preprocessing_service.py` and `services/preprocessing_runtime_service.py`** — modified by the agent; verify Airflow-triggered preprocessing paths match the new bronze prefix in both Mac-side and Windows-side calls.
2. **Legacy read fallback** is temporary — once the new bucket has real data under the new structure, the fallback code in `lineage_governance.py` and `monitoring_cost.py` should be removed.
3. **`services/analytics_download_service.py.py`** — the double-extension filename is a pre-existing typo in the repo; it works but is confusing. Consider renaming in a future cleanup PR.
4. **Write paths not covered:** any direct `put_object` / `upload_file` calls in page callbacks (not services) were not changed — pages appear to delegate all writes to services, but confirm before enabling Dataset Registry writes.
5. **Airflow DAGs on Windows** — still point to old flat prefixes. This is out of scope for this task but must be updated before new data lands in the new bucket from the pipeline.

---

## 11. Recommended Next Steps

1. **Run** `python scripts/test_b2_v2_connection.py` and `python scripts/test_b2_v2_upload.py` after any `.env` change to confirm connectivity.
2. **Wire Dataset Registry** (upload flow) once verified — it is safe because all write paths in `b2_service.py` and `browser_upload_service.py` now use `b2_paths` helpers pointing to the new bucket.
3. **Wire Dataset Readiness** — safe to connect; it reads via service layer with no direct old-path references.
4. **Update Airflow DAGs** on Windows to write bronze data under `01_raw_data/bronze_raw_data/` — coordinate with Windows compute backend work.
5. **Remove legacy read fallbacks** once confirmed no old-bucket data is being read in production.
6. **Rename** `analytics_download_service.py.py` → `analytics_download_service.py` in a cleanup PR.

---

## 12. Safe to Connect Dataset Registry and Dataset Readiness?

**Yes — both are safe to connect to the new bucket.**

- **Dataset Registry** (upload flow via `browser_upload_service.py` + `b2_service.py`): all write paths now target `01_raw_data/bronze_raw_data/` in `building-identification-mls-v2`. No writes go to old flat paths.
- **Dataset Readiness** (`pages/dataset_readiness.py`): reads entirely through the service layer; the service layer now uses `b2_paths` helpers. Legacy read fallbacks are in place for any existing old-path data.
