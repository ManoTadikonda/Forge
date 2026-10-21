"""Root pytest configuration.

Puts the workspace package roots on sys.path so the test suite runs with a plain
`pytest` invocation and does not depend on an editable install or on PYTHONPATH
being exported by the caller.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
WORKSPACE_PACKAGES = (
    "packages/forge-core",
    "apps/api",
    "apps/worker",
    "apps/router",
    "apps/sample-service",
)

for _package in WORKSPACE_PACKAGES:
    _path = str(ROOT / _package)
    if _path not in sys.path:
        sys.path.insert(0, _path)
