"""Pre-demo smoke test against the REAL Gemini API.

Runs every sample in the inbox through the full pipeline (fresh, isolated database) and
checks each decision against the expected outcome. Run it before recording the video
and before the interview.

    export GEMINI_API_KEY=...
    .venv/bin/python scripts/smoke_llm.py
"""
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
os.environ.setdefault("AP_TODAY", "2026-11-12")
sys.path.insert(0, str(ROOT))

from app import db, pipeline  # noqa: E402
from app.extract import MODEL, llm_available  # noqa: E402

EXPECTED = {
    "01": ("APPROVE", None), "02": ("HOLD", "Procurement / Buyer"), "03": ("APPROVE", None),
    "04": ("REJECT", "Rejected: duplicate"), "05": ("HOLD", "Treasury & Fraud"),
    "06": ("APPROVE", None), "07": ("REJECT", "Returned to vendor"),
}

if not llm_available():
    sys.exit("Set GEMINI_API_KEY first (in .env or your shell).")
db.init(reset=True)
print(f"Model: {MODEL}\n")
ok_all = True
for pdf in sorted((ROOT / "samples").glob("*.pdf")):
    time.sleep(4)  # stay under free-tier requests-per-minute
    t0 = time.time()
    rid = pipeline.start_run(pdf.read_bytes(), pdf.name, "smoke")
    while db.get_run(rid)["status"] == "running":
        time.sleep(0.2)
    r = db.get_run(rid)
    exp_d, exp_r = EXPECTED[pdf.name[:2]]
    ok = r["decision"] == exp_d and (exp_r is None or r["route_to"] == exp_r) and r["extractor"] == "gemini"
    ok_all &= ok
    print(f"{'PASS' if ok else 'FAIL'}  {pdf.name[:48]:48} {r['decision']:7} {r['route_to'] or '':28} {time.time() - t0:5.1f}s  [{r['extractor']}]")
    if not ok:
        print("      headline:", r["headline"])
        for s in r["trace"].get("stages", {}).values():
            for c in s.get("checks", []):
                if c["status"] in ("fail", "warn"):
                    print(f"      {c['status']}: {c['label']}: {c['detail'][:160]}")
print("\nALL GOOD. Ready to demo." if ok_all else "\nSome cases differ. Inspect above before demoing.")
