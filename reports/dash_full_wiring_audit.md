# Dash Full Wiring, UI Smoothness, and Regression Audit

**Audit date:** 2026-05-28 07:51 UTC  
**Git branch:** test  
**B2 bucket detected:** building-identification-mls-v2  
**B2 endpoint detected:** https://s3.us-east-005.backblazeb2.com

---

## 1. B2 Prefix Test Results

**B2 Connection Test:** ✅ PASSED — 8/8 prefixes, 0 failed

| Prefix Var | Path | Keys found | Sample |
|---|---|---|---|
| B2_BRONZE_PREFIX | 01_raw_data/bronze_raw_data/ | 5 | paris-lille-id-1 tiles, label maps, manifests |
| B2_GOLD_PREFIX | 02_preprocessing/gold_model_ready_data/ | 1 | .bzEmpty placeholder |
| B2_TRAINING_RUNS_PREFIX | 03_segmentation/training_runs/ | 1 | .bzEmpty |
| B2_SEGMENTATION_PREFIX | 03_segmentation/segmentation_outputs/ | 1 | .bzEmpty |
| B2_CLUSTERING_PREFIX | 04_clustering/clustered_final_outputs/ | 1 | .bzEmpty |
| B2_GIS_EXPORTS_PREFIX | 05_applications/gis_exports/ | 1 | .bzEmpty |
| B2_METADATA_PREFIX | 06_governance/metadata/ | 5 | deadlock-test-001, id-1, paris-lille-id-1, tiny-browser-001 |
| B2_ANALYTICS_PREFIX | 06_governance/metadata_analytics/ | 5 | deadlock-test-001 parquet files |

**B2 Upload Smoke Test:** ✅ PASSED  
- PUT `06_governance/logs/dash_smoke_test/test_from_mac_dash.txt` → OK  
- HEAD verify → OK (size=18 bytes)

**.env B2 prefix verification:** ✅ All 16 required prefixes present.  
Minor note: `.env` contains a duplicate `B2_ANALYTICS_PREFIX=06_governance/metadata_analytics` alongside the canonical `B2_METADATA_ANALYTICS_PREFIX`. This is harmless but redundant.

**`services/b2_paths.py`:** ✅ Confirmed as the single central source of truth for all 16 B2 prefixes. All helper functions (`bronze_tiles_prefix`, `gold_prefix`, `silver_prefix`, `gis_exports_prefix`, etc.) use `b2_prefix()` internally.

---

## 2. Old Path Reference Audit

Full grep across all `.py`, `.json`, `.yaml`, `.md` files (excluding `.claude/` worktrees).

| File | Line | Pattern | Classification |
|---|---|---|---|
| `airflow_dags/dags/dag_health_b2.py:11` | `B2_HEALTH_PREFIX = os.getenv("B2_HEALTH_PREFIX", "bronze_raw_data/")` | **Airflow DAG — DO NOT CHANGE.** Default won't match v2 bucket. Set `B2_HEALTH_PREFIX=01_raw_data/bronze_raw_data` in Airflow env. |
| `pages/data_explorer.py:1194` | `data/metadata/datasets/{dataset_id}.json` | Safe — local filesystem cache path, not B2 |
| `pages/lineage_governance.py:269` | `_safe_b2_list(f"bronze_raw_data/{dataset_id}/manifests/")` | Safe — legacy READ fallback only, executes after primary v2 path fails |
| `pages/lineage_governance.py:281,285` | `bronze_raw_data/{dataset_id}/manifests/*.json` in filename checks | Safe — read-only filename comparison in legacy fallback |
| `pages/lineage_governance.py:346` | `_safe_b2_list(f"gold_model_ready_data/{dataset_id}/")` | Safe — legacy read fallback |
| `pages/lineage_governance.py:415` | `data/metadata/datasets/{dataset_id}.json` | Safe — local cache path |
| `pages/risk_exposure.py:290,327` | `data/local_staging/gis_exports/{dataset_id}/...` | Safe — local staging directory |
| `pages/dataset_readiness.py:54` | `data/metadata/datasets/{dataset_id}.json` | Safe — local cache read fallback displayed in UI |
| `pages/preprocessing.py:608` | `placeholder="silver_preprocessed_data/..."` | Safe — UI input placeholder text only |
| `pages/preprocessing.py:1572` | `current.startswith("silver_preprocessed_data/")` | Safe — validation logic to auto-reset stale prefix, co-checks v2 path |
| `pages/postprocessing.py:234,294,314` | `03_segmentation/segmentation_outputs/<template>/` | Safe — UI display text / template string, uses full v2 path |
| `pages/gis_exports.py:112` | `data/local_staging/segmentation_outputs/...` | Safe — UI placeholder text |
| `pages/gis_exports.py:296` | `data/local_staging/gis_exports/{dataset_id}/{run_id}` | Safe — local output directory for generated files |
| `pages/training.py:1115` | `02_preprocessing/gold_model_ready_data/<dataset_id>/...` | Safe — informational UI message showing expected prefix |
| `services/analytics_download_service.py.py:4-6` | `silver_preprocessed_data/...`, `gold_model_ready_data/...` | Safe — file header comments (documentation) |
| `services/b2_service.py:167,170,771,960,967,1021,1041` | `bronze_raw_data/<dataset_id>/...` in docstrings | Safe — documentation examples only |
| `services/metadata_service.py:382,383,387,402` | `bronze_raw_data/...`, `data/metadata/datasets/...` | Safe — docstring documentation |
| `services/rerun_service.py:89,98` | `"b2_key": "bronze_raw_data/id-2/..."` | Safe — docstring example, not live code |

**Verdict:** No unsafe live B2 WRITE paths using old/flat prefixes. All old path references are legacy read fallbacks, local cache paths, documentation, or UI text.

---

## 3. Upload Smoke Test Results

Browser upload API endpoints tested (running app):
- `POST /api/browser-upload/sessions` → 200
- `GET /api/browser-upload/sessions/<id>` → 200
- `POST /api/browser-upload/chunk` → 200
- `POST /api/browser-upload/complete-file` → 200
- `POST /api/browser-upload/complete-session` → 200
- `POST /api/browser-upload/abort` → 200

**Expected B2 keys for browser upload (confirmed in service code):**
```
01_raw_data/bronze_raw_data/<dataset_id>/source_files/tiles/<filename>      ← tiles (PLY/LAS/LAZ)
01_raw_data/bronze_raw_data/<dataset_id>/source_files/label_maps/<filename> ← label maps (XML/JSON/YAML)
01_raw_data/bronze_raw_data/<dataset_id>/manifests/upload_manifest.json
01_raw_data/bronze_raw_data/<dataset_id>/manifests/checksum_manifest.json
06_governance/metadata/datasets/<dataset_id>/metadata.json
06_governance/metadata_analytics/<dataset_id>/*.parquet
```

All write paths come from `b2_paths.py` helpers. ✅

---

## 4. All Pages Tested and HTTP Status

| Route | HTTP | Nav group | Shared nav | Visual theme |
|---|---|---|---|---|
| / | 200 | Home | ✅ platform_header | ✅ LiDAR particle bg |
| /data-explorer | 200 | Data Management | ✅ platform_header | ✅ LiDAR particle bg |
| /dataset-readiness | 200 | Data Management | ✅ ops_topbar → platform_header | ✅ platform_hero |
| /silver-gold-outputs | 200 | Data Management | ✅ ops_topbar → platform_header | ✅ platform_hero |
| /preprocessing | 200 | Processing & ML | ✅ ops_topbar → platform_header | ✅ LiDAR canvas |
| /training | 200 | Processing & ML | ✅ platform_header | ✅ LiDAR canvas |
| /postprocessing | 200 | Processing & ML | ✅ platform_header | ✅ LiDAR canvas |
| /gis-exports | 200 | GeoAI Products | ✅ ops_topbar → platform_header | ✅ platform_hero |
| /risk-exposure | 200 | GeoAI Products | ✅ ops_topbar → platform_header | ✅ platform_hero |
| /monitoring-cost | 200 | Platform Operations | ✅ ops_topbar → platform_header | ✅ platform_hero |
| /lineage-governance | 200 | Platform Operations | ✅ ops_topbar → platform_header | ✅ platform_hero |
| /api-integration | 200 | Platform Operations | ✅ ops_topbar → platform_header | ✅ platform_hero |

No 404s, 500s, or loading failures on any route.

---

## 5. Callback Registration

**Total `@callback` decorators across all files:** ~77  
(data_explorer: 18, preprocessing: 20, training: 12, risk_exposure: 10, gis_exports: 5, control_panel: 3, home: 2, monitoring_cost: 2, app.py: 1, others: 4)

No duplicate component ID errors detected at startup. App starts cleanly.  
Docker logs confirm all `_dash-update-component` requests return HTTP 200. No 500 errors found.

---

## 6. Organic Callback Errors

**Zero 500-level callback errors found** in Docker container logs or local startup logs.  
All callback responses return 200.

---

## 7. Dataset Selection URL/Store Synchronization

**Session store:** Exactly one `dcc.Store(id="selected-dataset-id", storage_type="session")` defined in `app.py`. No duplicates.

**URL update on selection:** `data_explorer.py` callback writes both:
- `Output("selected-dataset-id", "data")` — session store
- `Output("url", "search")` — URL query param via `search_with_dataset_id()`

**Downstream priority resolution** (all 7 required pages verified):

| Page | Uses `resolve_selected_dataset_id(search, store)` | URL-first | Store fallback |
|---|---|---|---|
| Dataset Readiness | ✅ | ✅ | ✅ |
| Silver & Gold Outputs | ✅ | ✅ | ✅ |
| Preprocessing | ✅ | ✅ | ✅ |
| Training | ✅ | ✅ | ✅ |
| Postprocessing | ✅ | ✅ | ✅ |
| Lineage & Governance | ✅ | ✅ | ✅ |
| Monitoring & Cost | ✅ | ✅ | ✅ |

**Navigation link `?dataset_id=` preservation:** ⚠️ **Gap (not regression)**  
Nav links use static paths (`/dataset-readiness`, `/training`, etc.) without query parameters. Same-tab navigation and page refresh within a tab work correctly via session store. Direct URL access from a new tab or external link without `?dataset_id=` will not carry dataset context. This is a feature gap for link sharing/deep linking; the session store covers all normal usage flows.

---

## 8. Navigation Consistency

**`platform_theme.py` NAV_GROUPS** matches required hierarchy exactly:
```
Home
Data Management    → Data Explorer, Dataset Readiness, Silver & Gold Outputs
Processing & ML   → Preprocessing, Training, Postprocessing
GeoAI Products    → GIS Exports, Risk & Exposure
Platform Operations → Monitoring & Cost, Lineage & Governance, API & Integration
```

All 12 nav-listed pages use `ops_nav()` from `platform_theme.py` (either directly or via `platform_header()` which calls `ops_nav()`). Active group highlighting and active item highlighting work via `_group_is_active()` and `_active_label()`.

No stale "Inference & Outputs" label found in the navigation system.

**Ghost pages** (registered with `dash.register_page`, accessible by URL, not in nav):
- `/inference-outputs` — `pages/inference_outputs.py` (`name="Inference & Outputs"`)
- `/model-benchmark` — `pages/model_benchmark.py`

These do not cause errors and are intentionally unlinked staging pages.

---

## 9. Workflow-Aware Page Status

| Page | Dependency | Empty state message | Waits for upstream | Status |
|---|---|---|---|---|
| Dataset Readiness | Upload + metadata | "No dataset selected. Please select a dataset from Data Explorer first." | ✅ | ✅ |
| Silver & Gold Outputs | Preprocessing | "Please select a dataset from Data Explorer first." | ✅ Shows workflow_state | ✅ |
| Training | Gold model-ready data | "No Gold model-ready data found. Run preprocessing first." | ✅ Button disabled | ✅ |
| Postprocessing | Segmentation outputs | "Dataset selected, but no segmentation output has been generated yet." | ✅ Button disabled | ✅ |
| GIS Exports | Manual PLY path | Clear empty state | N/A (manual trigger) | ✅ |

No fake or mock Silver/Gold/Segmentation outputs found. All states derive from real B2 or local file checks.

**Limitation:** `services/gis_export_service.py:get_epsg_for_dataset()` only supports `paris-lille-id-1` (EPSG:32631) and `fui9` (EPSG:32617). Any other dataset_id raises a `ValueError`. GIS exports for other datasets will fail until EPSG mappings are extended.

---

## 10. Upload Test Results

**Admin/Server-Path Upload preflight checks confirmed in `services/b2_service.py`:**
- ✅ `os.path.exists(local_file_path)` — raises `FileNotFoundError` with Docker mount hint
- ✅ `os.path.isfile(local_file_path)` — raises `ValueError`
- ✅ `os.access(local_file_path, os.R_OK)` — raises `PermissionError` with Docker mount hint
- ⚠️ No explicit "open and read small chunk" probe before SHA-1 (reads during SHA-1 computation)

Errors are caught by callbacks and shown as `dbc.Alert(..., color="danger")` with actionable messages.

**VirtioFS mitigation:** Files under `/datasets/` are staged to `/tmp` before SHA-1 and B2 upload, bypassing VirtioFS entirely for large files.

**Upload progress accuracy:** Separate counters for `uploaded_files`, `failed_files`, `skipped_files`, `total_files`. Per-file progress JSON is written to `data/upload_progress/<dataset_id>.json`. Failed files do NOT increment `uploaded_files`.

---

## 11. Exact B2 Keys Written During Audit Upload Test

```
06_governance/logs/dash_smoke_test/test_from_mac_dash.txt
  Body: "dash_smoke_test ok" (18 bytes)
  Status: PUT OK → HEAD OK (size=18)
```

---

## 12. Docker / Path Mount Status

**Container:** `lidar-dash` (ce8a621497e0) — Up 28 minutes  
**Status in `docker ps`:** `(unhealthy)` ← FALSE ALARM — see issue below  
**Actual health:** All HTTP routes return 200, no 500 errors in logs.

**`/datasets` mount:** ✅ Accessible inside container  
Contents: `paris-lille-10-classes/`, `tiny_test_dataset/`, `torronto/`

**Docker healthcheck issue:** The running container reports `curl: not found` for its healthcheck. This indicates the image was built before the `docker-compose.yml` healthcheck was updated from a curl-based command to the current python-based command. The image needs to be rebuilt with `docker compose up -d --build`.

---

## 13. VirtioFS / gRPC FUSE Observation

**VirtioFS deadlock for large files is a known issue** and has been mitigated in code:  
- `upload_panel.py` displays two `dbc.Alert(color="danger")` warnings on the Admin Upload form explaining the VirtioFS limitation and directing users to the Browser Upload.
- `b2_service.py:upload_local_file_to_b2_standard()` stages files from `/datasets/` to `/tmp` before reading, bypassing the FUSE layer.
- For extremely large files (multi-GB PLY stacks), the staging copy itself may deadlock under VirtioFS. In that case, Browser Upload is the correct path — the browser reads from the Mac directly without VirtioFS involvement.

**No new code changes are needed for the VirtioFS issue.** The documentation and routing logic are already in place.

---

## 14. UI Smoothness Observations

**LiDAR animated background:** Present on all 12 pages via `lidar_particle_background()` called inside `platform_hero()` or directly in page layouts. ✅

**Reduced-motion support:**
- `assets/landing.js` checks `window.matchMedia('(prefers-reduced-motion: reduce)')` ✅
- `assets/style.css` has `@media (prefers-reduced-motion: reduce)` at 4 locations ✅

**Card styling:** All pages use `ops_page_shell` components or `platform_theme.py` constants. Background color `#05070d`, panel `rgba(15, 22, 34, 0.92)`, border `rgba(125, 180, 255, 0.18)`. ✅

**Nav dropdowns:** Pure CSS dropdowns via `ops-nav-dropdown` classes; no JS dependency for open/close. ✅

**No outdated upload modal text:** Upload panel correctly shows v2 B2 paths:
```
b2://building-identification-mls-v2/01_raw_data/bronze_raw_data/<dataset_id>/source_files/tiles/
b2://building-identification-mls-v2/01_raw_data/bronze_raw_data/<dataset_id>/source_files/label_maps/
b2://building-identification-mls-v2/01_raw_data/bronze_raw_data/<dataset_id>/manifests/
```
✅

**No duplicate headers found** across any page. ✅

---

## 15. Local Cache Alignment

| Local path | Datasets present | Conflicts with B2 v2 |
|---|---|---|
| `data/metadata/datasets/` | 9 JSON files (paris-lille-id-1, torronto-*, id-*, fui9, tiny-browser-001, deadlock-test-001) | None — flat local cache, not conflicting |
| `data/metadata_analytics/` | 9 directories matching above | None — separate namespace |

Local cache paths use flat `<dataset_id>.json` format while B2 v2 uses `06_governance/metadata/datasets/<dataset_id>/metadata.json`. No conflicts. ✅

---

## 16. Reports Directory

- `reports/b2_v2_dash_wiring_report.md` — existing report
- `reports/dash_full_wiring_audit.md` — this document

No credentials found in any report file. ✅

---

## 17. Remaining Blocking Issues

**None.** There are no issues that block basic operation.

---

## 18. Non-Blocking Issues Found

### High priority (fix before production):
1. **Docker image stale — healthcheck reports `curl: not found`**  
   Root cause: Running image was built before the python-based healthcheck was set in `docker-compose.yml`.  
   Fix: `docker compose up -d --build`  
   Impact: `docker ps` shows "(unhealthy)" but app is fully functional.

2. **`dag_health_b2.py` default prefix mismatch**  
   `B2_HEALTH_PREFIX` defaults to `bronze_raw_data/` (finds 0 files in v2 bucket).  
   Fix: Set `B2_HEALTH_PREFIX=01_raw_data/bronze_raw_data` in Airflow env vars.  
   Impact: B2 health DAG reports 0 files; misleads monitoring. Does not affect app.  
   Note: DAG code is out of audit scope.

3. **`services/analytics_download_service.py.py` — double `.py` extension**  
   This file cannot be imported as a Python module. No code currently imports it.  
   Impact: Dormant dead file. If needed later, rename to `analytics_download_service.py`.

### Medium priority (feature gap):
4. **Navigation links do not preserve `?dataset_id=` query parameter**  
   Session store covers same-tab usage. New-tab or direct-URL access loses dataset context.  
   Fix: Add a callback per page that rewrites nav href attributes when dataset_id is set. Requires careful Dash pattern-matching callback setup.

### Low priority (improvements):
5. **GIS export EPSG mapping** only covers `paris-lille-id-1` and `fui9`. Add EPSG for other datasets in `services/gis_export_service.py:get_epsg_for_dataset()`.

6. **Ghost pages** `/inference-outputs` and `/model-benchmark` are deployed but not in nav. Either add them to `NAV_GROUPS` or register them as 404s if deprecated.

7. **Upload preflight chunk-read test** is missing. The SHA-1 computation serves as an implicit read test, but an explicit `f.read(1024)` probe before SHA-1 would return a faster, cleaner error message for zero-byte or unreadable files.

8. **`b2_service.py` docstring examples** use old flat paths. Minor documentation debt.

---

## 19. Recommended Improvements

| Priority | Action |
|---|---|
| High | `docker compose up -d --build` to fix stale image + healthcheck |
| High | Set `B2_HEALTH_PREFIX=01_raw_data/bronze_raw_data` in Airflow env |
| High | Rename `analytics_download_service.py.py` → `analytics_download_service.py` |
| Medium | Add `?dataset_id=` preservation to nav links (Dash pattern-matching callback) |
| Medium | Extend `get_epsg_for_dataset()` to support all registered datasets |
| Low | Add explicit chunk-read probe in admin server-path upload preflight |
| Low | Decide fate of ghost pages (`/inference-outputs`, `/model-benchmark`) |
| Low | Update `b2_service.py` docstrings to use v2 paths |

---

## 20. Safe-to-Proceed Verdict

**✅ The Dash folder is safe to proceed toward Windows Airflow migration.**

- All 12 routes return HTTP 200 with no callback errors.
- B2 v2 connection and upload tests pass (8/8 prefixes, smoke upload verified).
- All B2 write paths use the new v2 prefix structure from `services/b2_paths.py`.
- No unsafe old B2 write paths remain in live code.
- Dataset selection propagates correctly via URL `?dataset_id=` and session store.
- Dataset Readiness, Silver/Gold, Training, and Postprocessing all respect workflow gating.
- Browser upload and admin server-path upload are wired correctly with proper preflight checks.
- UI background, nav, and theme are consistent across all pages.
- Docker mounts are accessible and VirtioFS mitigation is in place.

The three high-priority non-blocking issues (stale Docker image, Airflow `B2_HEALTH_PREFIX`, double-extension file) do not affect core data flow or the Windows Airflow migration path.
