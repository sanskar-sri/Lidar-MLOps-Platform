# Shared Airflow DAGs

This folder is the Mac-side source for DAGs used by the LiDAR `data-platform`
controller. Keep every project DAG here first, then sync or mount `dags/` into
the Windows workstation Airflow Docker project.

```text
airflow_dags/
  dags/
    dag_health_b2.py
    dag_health_remote.py
    preprocessing_dag.py          # copy from the Windows Airflow project
    training_dag.py               # copy from the Windows Airflow project
    data_platform_tracking_dag.py # add controller/registry tracking DAGs here
    data_registry_tracking_dag.py
  docs/
  scripts/
  requirements-health.txt
```

The important rule is: the Airflow scheduler only loads DAGs that are visible
inside the Windows Airflow container. Use this Mac folder as the clean source,
but make the Windows container read the `dags/` subfolder.

Recommended Docker mount on the Windows Airflow project:

```yaml
volumes:
  - ./airflow_dags/dags:/opt/airflow/dags
```

If the Windows Airflow project is separate from this Mac checkout, copy/sync the
contents of this folder's `dags/` directory into the Windows project's mounted
`dags/` directory.

Current local DAGs:

- `dags/dag_health_b2.py`
- `dags/dag_health_remote.py`

The B2 health DAG needs `b2sdk` installed inside the Airflow image. The minimal dependency is listed in `requirements-health.txt`.

Required Airflow environment variables:

```env
AIRFLOW__CORE__LOAD_EXAMPLES=False
B2_KEY_ID=<your-b2-key-id>
B2_APPLICATION_KEY=<your-b2-application-key>
B2_BUCKET_NAME=Building-Identification-MLS
B2_HEALTH_PREFIX=bronze_raw_data/
B2_HEALTH_INTERVAL_SECONDS=90
REMOTE_HEALTH_INTERVAL_SECONDS=90
MLFLOW_HEALTH_URL=http://<mlflow-host>:5000/health
```

Both DAGs use `catchup=False`, `max_active_runs=1`, and 30-second timeouts so health checks do not pile up when a service is down.

Set `AIRFLOW__CORE__LOAD_EXAMPLES=False` on the workstation Airflow webserver, scheduler, triggerer, and worker containers, then restart the Airflow stack. Deleting example DAG records through the REST API is temporary while `load_examples` remains enabled, because the scheduler re-registers bundled example DAGs from the installed Airflow package.

After copying the files, restart Airflow services and confirm from the Mac:

```bash
curl -u airflow:airflow http://100.88.150.103:8080/api/v1/dags/dag_health_b2
curl -u airflow:airflow http://100.88.150.103:8080/api/v1/dags/dag_health_remote
```
