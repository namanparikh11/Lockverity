"""Lockverity product version.

This module is intentionally dependency-free so that any other module
can import ``__version__`` without triggering the application's
import graph. The application package (``app/__init__.py``) and
the runtime configuration (``app/core/config.py``) both pull from
here, so a single change here propagates to:

- the FastAPI ``/health`` and ``/system/info`` responses,
- the application startup log,
- the SBOM / SARIF / CSV export tool metadata.

There is no separate "frontend version" - the frontend reads the
backend's version through ``/system/info`` so the product stays
consistent by construction.
"""

__version__ = "2.1.3"
