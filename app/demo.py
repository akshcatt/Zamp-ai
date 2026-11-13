"""Keeping a free-tier deployment usable.

Render's free plan has no persistent disk: the container's files (and so the SQLite database) are
wiped whenever the instance sleeps after ~15 idle minutes, and on every redeploy. Two mitigations,
both off unless the matching environment variable is set (render.yaml sets them):

* KEEP_AWAKE=1     ping our own public URL every 10 minutes so the instance never idles into sleep.
* DEMO_AUTOLOAD=1  if the database starts empty, reload the sample invoices from stored AI reads
                   (app/seed/demo_reads.json), so a fresh instance never opens on a blank dashboard.
                   No AI calls are made; every rule still runs on each sample.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from . import db, pipeline

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
READS = Path(__file__).resolve().parent / "seed" / "demo_reads.json"


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def autoload_samples(wait: bool = False) -> int:
    """Reload every sample through the full pipeline when there are no runs yet. Returns how many started."""
    if db.q("SELECT COUNT(*) AS n FROM runs", fetch="one")["n"]:
        return 0
    if READS.exists():
        for r in json.loads(READS.read_text()):
            db.cache_put(r["sha256"], r["extractor"], r["data"])
    files = sorted(p for p in SAMPLES.iterdir() if p.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png"))

    def go():
        for p in files:  # one at a time, in order, like an inbox filling up
            rid = pipeline.start_run(p.read_bytes(), p.name, "sample (auto-loaded)", saved_read=True)
            while (db.get_run(rid) or {}).get("status") == "running":
                time.sleep(0.1)
        print(f"ClearAP: auto-loaded {len(files)} sample invoices", flush=True)

    if wait:
        go()
    else:
        threading.Thread(target=go, daemon=True).start()
    return len(files)


def keep_awake(url: str, every_s: int = 600) -> None:
    def loop():
        while True:
            time.sleep(every_s)
            try:
                urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=10).read()
            except Exception as e:  # never let the pinger take the app down
                print(f"ClearAP: keep-awake ping failed: {e}", flush=True)

    threading.Thread(target=loop, daemon=True).start()


def start() -> dict:
    loaded = autoload_samples() if _on("DEMO_AUTOLOAD") else 0
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_URL")
    awake = bool(_on("KEEP_AWAKE") and url)
    if awake:
        keep_awake(url)
    return {"autoloaded": loaded, "keep_awake": awake}
