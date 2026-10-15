"""Validate exports against the official XML schemas (src/rules/schemas/).

* PMML 4.4 (dmg.org). The schema file dmg.org serves declares the target namespace
  ``https://www.dmg.org/PMML-4_4``, while PMML 4.4 documents and consumers use
  ``http://www.dmg.org/PMML-4_4``; the schema is loaded with its namespace aligned
  to the documents' (only the namespace string differs).
* DMN 1.3 (OMG), with its DMNDI / DC / DI imports.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parent / "schemas"


@lru_cache(maxsize=None)
def _schema(kind: str):
    from lxml import etree
    if kind == "pmml":
        text = (SCHEMAS / "pmml-4-4.xsd").read_bytes().replace(b"https://www.dmg.org/PMML-4_4",
                                                               b"http://www.dmg.org/PMML-4_4")
        return etree.XMLSchema(etree.fromstring(text))
    if kind == "dmn":
        return etree.XMLSchema(etree.parse(str(SCHEMAS / "DMN13.xsd")))
    raise ValueError(kind)


def validate(xml: str, kind: str) -> list[str]:
    """Schema errors (empty when valid)."""
    from lxml import etree
    try:
        doc = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        return [f"not well-formed: {exc}"]
    schema = _schema(kind)
    if schema.validate(doc):
        return []
    return [f"line {e.line}: {e.message}" for e in schema.error_log][:20]
