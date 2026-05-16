# Airflow Health DAGs

Copy these DAG files into the Windows Airflow Docker project `dags` folder:

- `dag_health_b2.py`
- `dag_health_remote.py`

The B2 health DAG needs `b2sdk` installed inside the Airflow image. The minimal dependency is listed in `requirements-health.txt`.

Required Airflow environment variables:

```env
B2_KEY_ID=<your-b2-key-id>
B2_APPLICATION_KEY=<your-b2-application-key>
B2_BUCKET_NAME=Building-Identification-MLS
B2_HEALTH_PREFIX=bronze_raw_data/
B2_HEALTH_INTERVAL_SECONDS=90
REMOTE_HEALTH_INTERVAL_SECONDS=90
MLFLOW_HEALTH_URL=http://<mlflow-host>:5000/health
```

Both DAGs use `catchup=False`, `max_active_runs=1`, and 30-second timeouts so health checks do not pile up when a service is down.

After copying the files, restart Airflow services and confirm from the Mac:

```bash
curl -u airflow:airflow http://100.88.150.103:8080/api/v1/dags/dag_health_b2
curl -u airflow:airflow http://100.88.150.103:8080/api/v1/dags/dag_health_remote
```
