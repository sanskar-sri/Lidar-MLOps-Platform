import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

PREFIXES = {
    "B2_BRONZE_PREFIX":       "01_raw_data/bronze_raw_data",
    "B2_GOLD_PREFIX":         "02_preprocessing/gold_model_ready_data",
    "B2_TRAINING_RUNS_PREFIX":"03_segmentation/training_runs",
    "B2_SEGMENTATION_PREFIX": "03_segmentation/segmentation_outputs",
    "B2_CLUSTERING_PREFIX":   "04_clustering/clustered_final_outputs",
    "B2_GIS_EXPORTS_PREFIX":  "05_applications/gis_exports",
    "B2_METADATA_PREFIX":     "06_governance/metadata",
    "B2_METADATA_ANALYTICS_PREFIX": "06_governance/metadata_analytics",
}


def main():
    bucket   = os.getenv("B2_BUCKET_NAME")
    endpoint = os.getenv("B2_ENDPOINT")
    key_id   = os.getenv("B2_KEY_ID")
    app_key  = os.getenv("B2_APPLICATION_KEY")

    missing = [
        name for name, value in {
            "B2_BUCKET_NAME":      bucket,
            "B2_ENDPOINT":         endpoint,
            "B2_KEY_ID":           key_id,
            "B2_APPLICATION_KEY":  app_key,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {missing}")

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
    )

    print(f"Bucket  : {bucket}")
    print(f"Endpoint: {endpoint}")
    print()

    passed = 0
    failed = 0

    for env_var, default_prefix in PREFIXES.items():
        prefix = os.getenv(env_var, default_prefix)
        try:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/", MaxKeys=5)
            count = resp.get("KeyCount", 0)
            sample = [item["Key"] for item in resp.get("Contents", [])]
            print(f"  OK  {env_var} ({prefix}/)  keys={count}")
            for key in sample:
                print(f"        - {key}")
            passed += 1
        except ClientError as exc:
            print(f"  FAIL {env_var} ({prefix}/)  -> {exc.response['Error']['Code']}: {exc.response['Error']['Message']}")
            failed += 1

    print()
    print(f"Result: {passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
