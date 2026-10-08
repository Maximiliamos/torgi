from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .core import get_settings, get_logger
from .db import init_db, session_scope

try:
    __version__ = version("bankrotai-finder")
except PackageNotFoundError:
    __version__ = "0.3.5"

__all__ = ["__version__", "get_logger", "get_settings", "init_db", "session_scope"]
