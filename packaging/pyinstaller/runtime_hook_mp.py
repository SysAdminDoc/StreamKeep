"""PyInstaller runtime guard for multiprocessing on Windows."""

import multiprocessing


multiprocessing.freeze_support()
