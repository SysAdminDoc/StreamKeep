"""S3 / Backblaze B2 / MinIO upload adapter (F68).

Uses boto3 for multipart upload with progress tracking. Works with any
S3-compatible endpoint.

Config keys: endpoint_url, bucket, access_key, secret_key, prefix, region.
"""

import os

from .base import UploadDestination


class S3Destination(UploadDestination):
    NAME = "S3 / B2 / MinIO"

    def upload(self, file_path, metadata=None, progress_cb=None):
        try:
            import boto3
        except ImportError:
            return False, "boto3 not installed. Run: pip install boto3"

        cfg = self.config
        endpoint = cfg.get("endpoint_url", "")
        bucket = cfg.get("bucket", "")
        access_key = cfg.get("access_key", "")
        secret_key = cfg.get("secret_key", "")
        prefix = cfg.get("prefix", "").strip("/")
        region = cfg.get("region", "us-east-1")

        if not bucket or not access_key or not secret_key:
            return False, "Missing S3 config: bucket, access_key, or secret_key"

        kwargs = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
        }
        if endpoint:
            kwargs["endpoint_url"] = endpoint

        try:
            s3 = boto3.client("s3", **kwargs)
            filename = os.path.basename(file_path)
            key = f"{prefix}/{filename}" if prefix else filename
            file_size = os.path.getsize(file_path)

            cb = None
            if progress_cb and file_size > 0:
                sent = [0]
                def _track(bytes_amount):
                    sent[0] += bytes_amount
                    progress_cb(sent[0], file_size)
                cb = _track

            s3.upload_file(file_path, bucket, key, Callback=cb)
            return True, f"Uploaded to s3://{bucket}/{key}"
        except Exception as e:
            return False, f"S3 upload failed: {e}"

    def test_connection(self):
        try:
            import boto3
        except ImportError:
            return False, "boto3 not installed"

        cfg = self.config
        kwargs = {
            "aws_access_key_id": cfg.get("access_key", ""),
            "aws_secret_access_key": cfg.get("secret_key", ""),
            "region_name": cfg.get("region", "us-east-1"),
        }
        if cfg.get("endpoint_url"):
            kwargs["endpoint_url"] = cfg["endpoint_url"]

        try:
            s3 = boto3.client("s3", **kwargs)
            s3.head_bucket(Bucket=cfg.get("bucket", ""))
            return True, "Connection OK"
        except Exception as e:
            return False, f"Connection failed: {e}"
