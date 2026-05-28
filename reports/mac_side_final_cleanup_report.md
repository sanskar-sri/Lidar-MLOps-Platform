# Mac-Side Final Cleanup Report

**Date:** 2026-05-28  
**Git branch:** test  
**Based on audit:** `reports/dash_full_wiring_audit.md`  
**Goal:** Clear all non-blocking issues before Windows Airflow migration.

---

## 1. Files Changed

| File | Change |
|---|---|
| `.env` | Removed duplicate `B2_ANALYTICS_PREFIX` line (kept canonical `B2_METADATA_ANALYTICS_PREFIX`) |
| `scripts/test_b2_v2_connection.py` | Renamed `B2_ANALYTICS_PREFIX` key to `B2_METADATA_ANALYTICS_PREFIX` to match the canonical env var |
| `services/analytics_download_service.py` | Renamed from `analytics_download_service.py.py` (double-extension removed) |
| `services/b2_service.py` | Added chunk-read preflight probe in `upload_local_file_to_b2_standard()`; updated all stale flat-path docstring examples to v2 paths |

---

## 2. Healthcheck Result — Before / After

**Before:**  
Container `lidar-dash` reported `(unhealthy)` — docker logs showed `curl: not found`.  
Root cause: running image was built before the `docker-compose.yml` healthcheck was updated from curl to Python.

**Rebuild steps:**
```
docker stop lidar-dash && docker rm lidar-dash
docker compose up -d --build
```

**After:**  
```
NAME           STATUS             PORTS
lidar-dash     Up (healthy)       0.0.0.0:8051->8051/tcp
lidar-mlflow   Up (healthy)       0.0.0.0:5001->5000/tcp
```

Both containers healthy. Healthcheck command in use:
```
python -c "import urllib.request; urllib.request.urlopen('http://localhost:8051/')"
```

---

## 3. .env Analytics Variable Decision

**Decision:** Removed `B2_ANALYTICS_PREFIX`. Canonical variable is `B2_METADATA_ANALYTICS_PREFIX`.

**Rationale:**  
- `services/b2_paths.py` reads `B2_METADATA_ANALYTICS_PREFIX` — this is the live code path.  
- `B2_ANALYTICS_PREFIX` was only referenced in `scripts/test_b2_v2_connection.py`, where it was a test-harness key (not a service dependency).  
- The test script was updated to use `B2_METADATA_ANALYTICS_PREFIX`.  
- No service, callback, or runtime code reads `B2_ANALYTICS_PREFIX`. Removing it is safe.

**Result:** One canonical analytics prefix. B2 connection test passes 8/8 with `B2_METADATA_ANALYTICS_PREFIX`.

---

## 4. Double-Extension File Status

**Before:** `services/analytics_download_service.py.py` — could not be imported as a Python module.  
**After:** `services/analytics_download_service.py` — clean module name.

Verification:
- No code imported the old `.py.py` filename before the rename.
- Post-rename import check: `python3 -c "import services.analytics_download_service"` → OK.
- No import errors introduced.

---

## 5. Upload Preflight Chunk-Read Status

**Added** to `services/b2_service.py` in `upload_local_file_to_b2_standard()`, after the `os.access` check and before the expensive staging + SHA-1 step:

```python
# Quick read probe before the expensive staging + SHA-1 step.
# Catches locked, unreadable, or deadlocked files immediately with a clean error.
try:
    with open(local_file_path, "rb") as _probe:
        _probe.read(1024)
except OSError as _exc:
    raise OSError(
        f"The Dash server cannot read '{local_file_path}': {_exc}. "
        f"Ensure the file is not locked or corrupted.{_mount_hint}"
    ) from _exc
```

**Behaviour:**
- Unreadable or deadlocked files fail immediately with a user-friendly message that includes the Docker mount hint.
- No raw kernel tracebacks reach the UI (errors are caught by the callback and shown as `dbc.Alert(color="danger")`).
- Normal upload flow is unaffected — the 1 KB read adds negligible overhead.

---

## 6. Docstring Old-Path Cleanup Result

**Updated** 7 locations in `services/b2_service.py`. All docstring examples now show v2 paths.

| Function | Old example | New example |
|---|---|---|
| `get_b2_destination_path()` | `bronze_raw_data/<id>/source_files/tiles/<f>` | `01_raw_data/bronze_raw_data/<id>/source_files/tiles/<f>` |
| `get_b2_destination_path()` | `bronze_raw_data/<id>/source_files/label_maps/<f>` | `01_raw_data/bronze_raw_data/<id>/source_files/label_maps/<f>` |
| `upload_local_file_to_b2_standard()` | `bronze_raw_data structure` | `v2 bronze structure` with full v2 paths |
| `upload_json_to_b2()` | `bronze_raw_data/<id>/manifests/<obj>` | `01_raw_data/bronze_raw_data/<id>/manifests/<obj>` |
| `download_b2_file_to_local()` | `bronze_raw_data/...` (×2 call styles) | `01_raw_data/bronze_raw_data/<id>/source_files/tiles/<file>` |
| `get_b2_tiles_for_dataset()` | `bronze_raw_data/<id>/source_files/tiles/` | `01_raw_data/bronze_raw_data/<id>/source_files/tiles/` |
| `get_b2_label_maps_for_dataset()` | `bronze_raw_data/<id>/source_files/label_maps/` | `01_raw_data/bronze_raw_data/<id>/source_files/label_maps/` |

No live code logic was changed. Legacy read-fallback code in `pages/lineage_governance.py` retains its existing comments and is intentionally left unchanged.

---

## 7. Ghost Pages Decision

**Status:** Intentional staging pages — remain registered, not in navigation.

| Route | File | Decision |
|---|---|---|
| `/inference-outputs` | `pages/inference_outputs.py` | Staging page for future "Inference & Outputs" feature. Registered under `Processing & ML`. Keep unlinked until inference pipeline is wired. |
| `/model-benchmark` | `pages/model_benchmark.py` | Staging page for model comparison dashboard. Keep unlinked until benchmark service produces real data. |

Both pages load without errors (HTTP 200). Neither causes callback registration conflicts. They will be added to `NAV_GROUPS` in `components/platform_theme.py` when their backing services are production-ready.

---

## 8. GIS EPSG Limitation Status

**Finding:** Dataset metadata JSON files (`data/metadata/datasets/*.json`) do not contain any `epsg`, `crs`, or coordinate reference system fields. Dynamic EPSG lookup from metadata is not currently possible.

**Current state of `services/gis_export_service.py:get_epsg_for_dataset()`:**
- Supports: `paris-lille-id-1` → EPSG:32631 (UTM zone 31N)
- Supports: `fui9` → EPSG:32617 (UTM zone 17N)
- Any other dataset_id raises `ValueError` immediately — no silent wrong-EPSG behaviour.

**Known limitation:** GIS exports for datasets other than `paris-lille-id-1` and `fui9` will fail with a clear `ValueError` until their EPSG codes are registered.

**Future improvement path:** When dataset upload metadata is extended to include a `coordinate_reference_system.epsg` field (e.g. via the upload panel or metadata service), `get_epsg_for_dataset()` can be updated to read EPSG dynamically from local metadata cache before falling back to the hardcoded map.

**No code change made.** Explicit ValueError is the correct behaviour — better than silently projecting to the wrong CRS.

---

## 9. Query Parameter Preservation Status

**Current behaviour:**
- Same-tab navigation: ✅ Works via `dcc.Store(id="selected-dataset-id", storage_type="session")`.
- Page refresh within a tab: ✅ Works (session store persists for the tab lifetime).
- URL `?dataset_id=` is set when a dataset is selected in Data Explorer (`data_explorer.py` writes both store and URL).
- All 7 downstream pages call `resolve_selected_dataset_id(search, store)` — URL-first with store fallback.

**Gap:** Static nav links (`/dataset-readiness`, `/training`, etc.) do not carry `?dataset_id=` forward. A new tab opened from the nav or a copied URL without the query param will not carry dataset context.

**Decision:** No code change at this time. The same-tab workflow (the normal usage path) is fully functional. The deep-link / link-sharing gap is a medium-priority improvement for a future PR. It requires Dash pattern-matching callbacks to rewrite nav `href` attributes when `selected-dataset-id` store changes — a safe but non-trivial addition that should not be rushed before the Windows Airflow migration.

---

## 10. B2 Test Results

### Connection Test (`scripts/test_b2_v2_connection.py`)

```
Bucket  : building-identification-mls-v2
Endpoint: https://s3.us-east-005.backblazeb2.com

  OK  B2_BRONZE_PREFIX          (01_raw_data/bronze_raw_data/)            keys=5
  OK  B2_GOLD_PREFIX            (02_preprocessing/gold_model_ready_data/) keys=1
  OK  B2_TRAINING_RUNS_PREFIX   (03_segmentation/training_runs/)          keys=1
  OK  B2_SEGMENTATION_PREFIX    (03_segmentation/segmentation_outputs/)   keys=1
  OK  B2_CLUSTERING_PREFIX      (04_clustering/clustered_final_outputs/)  keys=1
  OK  B2_GIS_EXPORTS_PREFIX     (05_applications/gis_exports/)            keys=1
  OK  B2_METADATA_PREFIX        (06_governance/metadata/)                 keys=5
  OK  B2_METADATA_ANALYTICS_PREFIX (06_governance/metadata_analytics/)   keys=5

Result: 8 passed, 0 failed
```

### Upload Smoke Test (`scripts/test_b2_v2_upload.py`)

```
  PUT  06_governance/logs/dash_smoke_test/test_from_mac_dash.txt  -> OK
  HEAD 06_governance/logs/dash_smoke_test/test_from_mac_dash.txt  -> OK (size=18)

Upload OK
```

---

## 11. Route Test Results

All 12 production routes tested against the rebuilt container:

| Route | HTTP |
|---|---|
| / | 200 |
| /data-explorer | 200 |
| /dataset-readiness | 200 |
| /silver-gold-outputs | 200 |
| /preprocessing | 200 |
| /training | 200 |
| /postprocessing | 200 |
| /gis-exports | 200 |
| /risk-exposure | 200 |
| /monitoring-cost | 200 |
| /lineage-governance | 200 |
| /api-integration | 200 |

No 404s or 500s.

---

## 12. Callback Error Status

- No duplicate component ID errors on startup.
- Zero organic `_dash-update-component` 500 errors in Docker logs after rebuild.
- All `~77` registered callbacks respond 200.

---

## 13. Final Safe-to-Proceed Verdict

**✅ Mac Dash folder is clean and safe to proceed to Windows Airflow migration.**

All high-priority non-blocking issues from the audit have been resolved:

| Issue | Status |
|---|---|
| Docker stale image / `curl: not found` healthcheck | ✅ Fixed — rebuilt, both containers healthy |
| Duplicate `B2_ANALYTICS_PREFIX` in `.env` | ✅ Fixed — removed; canonical `B2_METADATA_ANALYTICS_PREFIX` retained |
| `analytics_download_service.py.py` double extension | ✅ Fixed — renamed, imports cleanly |
| No chunk-read preflight before SHA-1 | ✅ Fixed — 1 KB probe added with clean user-facing error |
| Stale flat-path docstring examples in `b2_service.py` | ✅ Fixed — all 7 locations updated to v2 paths |
| Ghost pages `/inference-outputs`, `/model-benchmark` | ✅ Documented — intentional staging pages, no action needed |
| GIS EPSG mapping coverage | ✅ Documented — ValueError on unknown dataset is correct behaviour |
| Nav `?dataset_id=` deep-link gap | ✅ Documented — medium-priority future improvement, same-tab flow unaffected |

The Dash application is fully functional: B2 bucket wired (8/8 prefixes), all 12 routes HTTP 200, zero callback errors, container healthcheck passing, upload flows intact.
