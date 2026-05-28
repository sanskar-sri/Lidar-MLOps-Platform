import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

TEST_KEY = "06_governance/logs/dash_smoke_test/test_from_mac_dash.txt"
TEST_CONTENT = b"dash_smoke_test ok"


def main():
    bucket   = os.getenv("B2_BUCKET_NAME")
    endpoint = os.getenv("B2_ENDPOINT")
    key_id   = os.getenv("B2_KEY_ID")
    app_key  = os.getenv("B2_APPLICATION_KEY")

    missing = [
        name for name, value in {
            "B2_BUCKET_NAME":     bucket,
            "B2_ENDPOINT":        endpoint,
            "B2_KEY_ID":          key_id,
            "B2_APPLICATION_KEY": app_key,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {missing}")

    print(f"Bucket  : {bucket}")
    print(f"Endpoint: {endpoint}")
    print(f"Key     : {TEST_KEY}")
    print()

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
    )

    # Upload
    try:
        s3.put_object(
            Bucket=bucket,
            Key=TEST_KEY,
            Body=TEST_CONTENT,
            ContentType="text/plain",
        )
        print(f"  PUT  {TEST_KEY}  -> OK")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg  = exc.response["Error"]["Message"]
        print(f"  PUT  {TEST_KEY}  -> FAIL: {code}: {msg}")
        raise SystemExit(1)

    # Verify via head_object
    try:
        resp = s3.head_object(Bucket=bucket, Key=TEST_KEY)
        size = resp.get("ContentLength", "?")
        print(f"  HEAD {TEST_KEY}  -> OK (size={size})")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg  = exc.response["Error"]["Message"]
        print(f"  HEAD {TEST_KEY}  -> FAIL: {code}: {msg}")
        raise SystemExit(1)

    print()
    print("Upload OK")


if __name__ == "__main__":
    main()
