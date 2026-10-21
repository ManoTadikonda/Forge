"""Fail loudly if any workspace package is not importable."""

from __future__ import annotations

import importlib

MODULES = ("forge_core", "forge_api", "forge_worker", "forge_router", "sample_service")


def main() -> int:
    failed = False
    for module in MODULES:
        try:
            resolved = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - report every failure, mask none
            print(f"    FAIL {module}: {type(exc).__name__}: {exc}")
            failed = True
        else:
            print(f"    ok   {module} ({getattr(resolved, '__file__', 'namespace')})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
