"""Upload destinations — adapter pattern for cloud/remote uploads (F68)."""

from .base import UploadDestination
from .ftp import FTPDestination
from .s3 import S3Destination
from .upload_worker import UploadWorker
from .webdav import WebDAVDestination
from .runtime import UploadRuntime, get_runtime, list_profiles, save_profile

__all__ = [
    "UploadDestination",
    "UploadWorker",
    "FTPDestination",
    "S3Destination",
    "WebDAVDestination",
    "UploadRuntime",
    "get_runtime",
    "list_profiles",
    "save_profile",
]
