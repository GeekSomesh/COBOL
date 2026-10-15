"""Create a self-signed TLS certificate for local development (localhost / 127.0.0.1 only).

    python scripts/dev_cert.py            # -> data/tls/dev-cert.pem, data/tls/dev-key.pem

Browsers will warn: this certificate is for trying TLS locally, never for production.
"""

from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=90))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    out = ROOT / "data" / "tls"
    out.mkdir(parents=True, exist_ok=True)
    (out / "dev-cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (out / "dev-key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
    print(f"wrote {out / 'dev-cert.pem'} and {out / 'dev-key.pem'} (valid 90 days, localhost only)")
    print("run: COBOL_TLS_CERT=data/tls/dev-cert.pem COBOL_TLS_KEY=data/tls/dev-key.pem python scripts/serve.py --port 8443")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
