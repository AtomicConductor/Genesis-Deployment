"""Genesis backend launcher (un-frozen, namespace-parametrized).

Replaces the upstream PyInstaller `/src.app` entry. Same behavior as the frozen
__main__ (uvicorn.run(backend.app, host="0.0.0.0", reload=False, workers=1)),
but imports ns_patch first so the hardcoded "argocd" platform namespace is
redirected to this deployment's namespace before any backend code runs.
"""

import os
import sys

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_APP_DIR, os.path.join(_APP_DIR, "base_library.zip")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ns_patch  # noqa: E402  (must run before backend import)

ns_patch.apply()

import uvicorn  # noqa: E402
from backend import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", reload=False, workers=1)
