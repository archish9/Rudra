"""Rudra - Autonomous Coding Agent CLI"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rudra")
except PackageNotFoundError:  # imported from a source tree that was never installed
    __version__ = "0.0.0+unknown"
