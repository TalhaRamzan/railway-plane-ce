#!/usr/bin/env python3
"""Set the CORS rule on Plane's S3 bucket so browsers can upload to it.

Plane uploads attachments straight from the browser to the bucket with a
presigned POST. Railway buckets are private and reject cross-origin requests
until a CORS rule allows the Plane origin. This runs on every container start,
is idempotent and never fails the boot: on error it only logs a warning.

Environment:
  AWS_S3_BUCKET_NAME, AWS_S3_ENDPOINT_URL, AWS_REGION,
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   bucket credentials (required)
  DOMAIN_NAME, RAILWAY_PUBLIC_DOMAIN         origins to allow (https://...)
  APP_PROTOCOL                               "http" also allows http:// origins
  PLANE_EXTRA_CORS_ORIGINS                   comma-separated extra origins
  PLANE_BUCKET_CORS=0                        skip this step
"""

import os
import sys


def log(msg):
    print(f"[bucket-cors] {msg}", flush=True)


def wanted_origins():
    protocol = os.environ.get("APP_PROTOCOL", "https").strip().lower()
    origins = []
    for host in (os.environ.get("DOMAIN_NAME", ""), os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")):
        host = host.strip().rstrip("/")
        if not host:
            continue
        origins.append(f"https://{host}")
        if protocol == "http":
            origins.append(f"http://{host}")
    for extra in os.environ.get("PLANE_EXTRA_CORS_ORIGINS", "").split(","):
        extra = extra.strip().rstrip("/")
        if extra:
            origins.append(extra)
    return sorted(set(origins))


def main():
    if os.environ.get("PLANE_BUCKET_CORS", "1").strip() == "0":
        log("skipped (PLANE_BUCKET_CORS=0)")
        return 0

    bucket = os.environ.get("AWS_S3_BUCKET_NAME", "").strip()
    if not bucket:
        log("skipped: AWS_S3_BUCKET_NAME is empty")
        return 0

    origins = wanted_origins()
    if not origins:
        log("skipped: no origin to allow (DOMAIN_NAME is empty)")
        return 0

    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:  # pragma: no cover - boto3 ships in the Plane image
        log(f"skipped: boto3 not available ({exc})")
        return 0

    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("AWS_S3_ENDPOINT_URL") or None,
        region_name=os.environ.get("AWS_REGION") or None,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )

    rule = {
        "AllowedOrigins": origins,
        "AllowedMethods": ["GET", "HEAD", "PUT", "POST"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3000,
    }

    try:
        try:
            current = client.get_bucket_cors(Bucket=bucket).get("CORSRules", [])
        except ClientError:
            current = []
        if len(current) == 1 and sorted(current[0].get("AllowedOrigins", [])) == origins and sorted(
            current[0].get("AllowedMethods", [])
        ) == sorted(rule["AllowedMethods"]):
            log(f"already set for {', '.join(origins)}")
            return 0
        try:
            client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [rule]})
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
                raise
            # Fresh S3/MinIO without the bucket yet (Railway buckets always exist):
            # create it now, as Plane's own create_bucket would a moment later.
            log(f"bucket '{bucket}' does not exist yet, creating it")
            client.create_bucket(Bucket=bucket)
            client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [rule]})
        log(f"set for {', '.join(origins)}")
    except (ClientError, BotoCoreError) as exc:
        log(f"WARNING: could not set CORS on bucket '{bucket}': {exc}")
        log("browser uploads may fail; see README > Troubleshooting > Uploads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
