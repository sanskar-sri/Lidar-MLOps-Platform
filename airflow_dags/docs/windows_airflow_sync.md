# Windows Airflow Sync

The Mac remains the LiDAR platform controller. The Windows workstation remains
the Airflow runtime.

Use this folder as the shared DAG source:

```text
/Users/sanskarsrivastava/Desktop/data-platform/airflow_dags/dags
```

Then make the Windows Airflow Docker project load those files into:

```text
/opt/airflow/dags
```

Common options:

- Copy the files manually when you change them.
- Put this folder in Git and pull it on the Windows workstation.
- Use a file sync tool from Mac to the Windows Airflow project folder.
- Mount the synced folder in Docker Compose with `./airflow_dags/dags:/opt/airflow/dags`.

Do not run a second production Airflow container on Mac. Use Mac for editing and
Dash control, and use the Windows Airflow scheduler/webserver/workers to execute
the DAGs.

