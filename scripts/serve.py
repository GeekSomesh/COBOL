"""Run the API, with TLS when a certificate is configured.

    python scripts/serve.py                                   # http://127.0.0.1:8000
    COBOL_TLS_CERT=cert.pem COBOL_TLS_KEY=key.pem python scripts/serve.py --host 0.0.0.0 --port 8443

With TLS the API also sends HSTS. For a development certificate:
``python scripts/dev_cert.py`` (self-signed, localhost only). In production put the
real certificate here or terminate TLS at a reverse proxy and set ``COBOL_TLS=1`` so the
API still sends HSTS.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("COBOL_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("COBOL_PORT", "8000")))
    ap.add_argument("--workers", type=int, default=1, help="keep 1: extraction jobs share one GPU and compiler")
    args = ap.parse_args()
    import uvicorn
    cert, key = os.environ.get("COBOL_TLS_CERT"), os.environ.get("COBOL_TLS_KEY")
    tls = {}
    if cert and key:
        os.environ["COBOL_TLS"] = "1"
        tls = {"ssl_certfile": cert, "ssl_keyfile": key, "ssl_ciphers": "ECDHE+AESGCM:ECDHE+CHACHA20"}
    elif args.host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: serving on a network address without TLS; set COBOL_TLS_CERT and COBOL_TLS_KEY", file=sys.stderr)
    uvicorn.run("src.api.main:app", host=args.host, port=args.port, workers=args.workers,
                proxy_headers=True, server_header=False, app_dir=str(ROOT), **tls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
