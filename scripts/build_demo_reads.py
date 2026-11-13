"""Record real Gemini reads of every sample into app/seed/demo_reads.json.

With DEMO_AUTOLOAD=1 a fresh (empty) deployment replays the samples from these stored reads, so it
never opens blank and spends no AI quota doing it. Re-run this after changing the samples.

    .venv/bin/python scripts/build_demo_reads.py      # needs GEMINI_API_KEY in .env or the shell
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["DEMO_PACING_MS"] = "0"
sys.path.insert(0, str(ROOT))

from app import db, pipeline  # noqa: E402
from app.extract import llm_available  # noqa: E402

if not llm_available():
    sys.exit("Set GEMINI_API_KEY first (in .env or your shell).")
db.init(reset=True)
out = []
for p in sorted(x for x in (ROOT / "samples").iterdir() if x.suffix in (".pdf", ".jpg")):
    time.sleep(4)  # stay under free-tier requests-per-minute
    rid = pipeline.start_run(p.read_bytes(), p.name, "build")
    while db.get_run(rid)["status"] == "running":
        time.sleep(0.2)
    r = db.get_run(rid)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    cached = db.cache_get(sha)
    if r["extractor"] != "gemini" or not cached:
        sys.exit(f"{p.name}: Gemini read failed ({r['extractor']}); try again in a minute.")
    out.append({"file": p.name, "sha256": sha, "extractor": "gemini", "data": cached["data"]})
    print(f"ok  {p.name:52} {r['decision']}")
(ROOT / "app" / "seed" / "demo_reads.json").write_text(json.dumps(out, indent=1))
print(f"\nWrote {len(out)} reads to app/seed/demo_reads.json")
