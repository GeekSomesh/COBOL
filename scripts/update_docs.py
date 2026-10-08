"""Copy results/RESULTS.md into README.md and docs/evaluation.md, so docs only ever show measured numbers.

    python scripts/update_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def inject(path: Path, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    block = f"{START}\n{body.strip()}\n{END}"
    if START in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _m: block, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n## 8. Measured results\n" + block + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"updated {path.relative_to(ROOT)}")


def main() -> int:
    results = (ROOT / "results" / "RESULTS.md").read_text(encoding="utf-8")
    body = "\n".join(line for line in results.splitlines() if not line.startswith("# "))
    body = body.replace("## ", "#### ")
    inject(ROOT / "README.md", body)
    inject(ROOT / "docs" / "evaluation.md", body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
