"""Upload destinations — adapter pattern for cloud/remote uploads (F68)."""

from .base import UploadDestination
from .upload_worker import UploadWorker

__all__ = ["UploadDestination", "UploadWorker"]
