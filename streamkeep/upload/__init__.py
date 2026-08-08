"""Upload destinations — adapter pattern for cloud/remote uploads (F68)."""

from .base import UploadDestination
from .ftp import FTPDestination
from .s3 import S3Destination
from .webdav import WebDAVDestination
from .runtime import (
    UploadRuntime, delete_profile, get_runtime, list_profiles, profile_view,
    resolve_profile, save_profile, test_profile,
)

__all__ = [
    "UploadDestination",
    "FTPDestination",
    "S3Destination",
    "WebDAVDestination",
    "UploadRuntime",
    "delete_profile",
    "get_runtime",
    "list_profiles",
    "profile_view",
    "resolve_profile",
    "save_profile",
    "test_profile",
]
