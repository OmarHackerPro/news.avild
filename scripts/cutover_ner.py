"""Local NER cutover orchestrator.

Steps:
  1. Snapshot ner_cache to data/ner_cache_snapshot_<timestamp>.json (rollback insurance).
  2. Run scripts/eval_ner.py to populate securebert-v1 cache rows AND seed ner_eval_judgments.
  3. PROMPT user to open /admin/ner-eval and adjudicate. Wait for typed 'yes' to continue.
  4. Print reminder to set NER_ACTIVE_MODEL=securebert-v1 in .env and restart ingestion.
  5. PROMPT for confirmation that .env was updated and ingestion restarted.
  6. Run scripts/cluster_articles.py --reset (13-min full rebuild).

This script does NOT modify .env or restart containers automatically — operator must
do those steps manually so the cutover stays observable and rollback stays one
config change away.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
import datetime
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data"


async def _snapshot_cache() -> Path:
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out = SNAPSHOT_DIR / f"ner_cache_snapshot_{ts}.json"
    rows = []
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT slug, model_version, entities_json, extracted_at FROM ner_cache")
        )
        for slug, mv, ents, ts_col in result.fetchall():
            rows.append({
                "slug": slug,
                "model_version": mv,
                "entities_json": ents,
                "extracted_at": ts_col.isoformat() if ts_col else None,
            })
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    return out


def _prompt(msg: str) -> None:
    print(f"\n{msg}")
    ans = input("Type 'yes' to continue, anything else to abort: ").strip().lower()
    if ans != "yes":
        print("Aborted.")
        sys.exit(1)


def _run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"FAILED with exit code {proc.returncode}")
        sys.exit(proc.returncode)


async def main() -> None:
    print("=== NER cutover orchestrator ===")
    print("Step 1/4: snapshot ner_cache ...")
    snap = await _snapshot_cache()
    print(f"  -> {snap}")

    print("\nStep 2/4: running eval_ner.py (this populates securebert-v1 cache rows)")
    print("This can take several minutes for ~1000 articles. Logs will stream below.\n")
    _run([sys.executable, "scripts/eval_ner.py"])

    print("\nStep 3/4: open the admin UI and adjudicate disagreements:")
    print("    http://localhost/api/admin/ner-eval?admin_secret=$ADMIN_SECRET")
    print("Check /metrics — proceed only when only-haiku rates are within thresholds:")
    print("    product / malware / actor / tool   <= 10%")
    print("    campaign                            <= 20%")
    _prompt("Have you adjudicated and confirmed the stopping criterion is met?")

    print("\nStep 4/4: manual operator actions required:")
    print("  a) Set NER_ACTIVE_MODEL=securebert-v1 in .env")
    print("  b) Restart ingestion:  docker compose restart ingestion")
    _prompt("Have you completed (a) and (b)?")

    print("\nFinal: rebuilding clusters (~13 min) ...")
    _run(["docker", "compose", "exec", "ingestion", "python", "scripts/cluster_articles.py", "--reset"])

    print("\nCutover complete. Spot-check the site.")


if __name__ == "__main__":
    asyncio.run(main())
