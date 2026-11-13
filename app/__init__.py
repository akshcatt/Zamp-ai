"""NoCap app package.

Loads a local .env (e.g. GEMINI_API_KEY) so `uvicorn app.main:app` picks up the key without exporting it
by hand. Real environment variables always win, and the file is never read during tests (they must not
call the real model). On Render the key comes from the dashboard; .env is git- and docker-ignored.
"""
import os
import sys
from pathlib import Path

_env = Path(__file__).resolve().parents[1] / ".env"
if _env.exists() and "pytest" not in sys.modules:
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            if _k.strip() and _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
