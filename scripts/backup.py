"""Back up the rule store (cron / Task Scheduler friendly) and apply retention.

    python scripts/backup.py                              # data/rules.db -> data/backups/rules-<time>.db, keep 14
    python scripts/backup.py --keep 30 --purge            # also drop old finished jobs / read notifications

Each backup is an online, consistent copy that is checked before older ones are
deleted: SQLite integrity must be "ok" and the review log's hash chain must verify.
Exit code 1 when the check fails (older backups are then kept).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("COBOL_DB", str(ROOT / "data" / "rules.db")))
    ap.add_argument("--out", default=os.environ.get("COBOL_BACKUP_DIR", str(ROOT / "data" / "backups")))
    ap.add_argument("--keep", type=int, default=14)
    ap.add_argument("--purge", action="store_true", help="apply retention to jobs and notifications")
    args = ap.parse_args()
    store = Store(args.db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = store.backup(Path(args.out) / f"rules-{stamp}.db")
    print(json.dumps(result))
    if not result["ok"]:
        return 1
    olds = sorted(Path(args.out).glob("rules-*.db"))
    for old in olds[:-args.keep]:
        old.unlink()
    if args.purge:
        print(json.dumps({"purged": store.purge()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
