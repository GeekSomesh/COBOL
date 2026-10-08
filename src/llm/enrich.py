"""``enrich(slice, rule) -> Rule`` (implementation.md 5.3).

The model may only supply title, intent, business field names and concepts.
``merge`` copies exactly those onto the AST rule and ignores anything else the
model said, so operators, literals, actions and traces always come from the
parser. Invalid output is retried once, then the baseline rule is kept and
routed to review.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.common.models import Rule, Slice
from src.llm.client import LLMError, OllamaClient
from src.llm.prompts import ENRICHMENT_SCHEMA, PROMPT_VERSION, messages


class Enrichment(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    intent: str = Field(min_length=1, max_length=600)
    field_names: dict[str, str]
    concepts: list[str] = Field(min_length=1)

    @field_validator("concepts")
    @classmethod
    def _concepts(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for c in v:
            c = re.sub(r"\s+", " ", str(c).strip().lower())
            if c and c not in out:
                out.append(c)
        if not out:
            raise ValueError("no concepts")
        return out[:5]


def snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def parse_enrichment(content: str) -> Enrichment:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    return Enrichment.model_validate_json(text)


def merge(rule: Rule, e: Enrichment, slice_: Slice, model: str) -> Rule:
    allowed = {k.upper() for k in slice_.fields}
    names = {}
    for cobol, business in e.field_names.items():
        key, value = str(cobol).upper().strip(), snake(business)
        if key in allowed and value and re.fullmatch(r"[a-z][a-z0-9_]*", value):
            names[key] = value
    data = rule.model_dump(by_alias=True)

    def rename(c: dict[str, Any]) -> None:
        for k in ("all", "any"):
            for x in c.get(k) or []:
                rename(x)
        if c.get("not"):
            rename(c["not"])
        if "source_name" in c:
            c["field"] = names.get(c["source_name"].split("(")[0].strip(), c["field"])

    rename(data["conditions"])
    for a in data["actions"] + data["else_actions"]:
        if a["type"] in ("set", "compute") and a.get("source_name"):
            a["target"] = names.get(a["source_name"].split("(")[0].strip(), a["target"])
    data["title"] = e.title.strip()
    data["intent"] = e.intent.strip()
    data["concepts"] = e.concepts
    data["provenance"] = {**data["provenance"], "model": model, "prompt_version": PROMPT_VERSION,
                          "enriched": True}
    return Rule.model_validate(data)


def enrich(slice_: Slice, rule: Rule, client: OllamaClient, examples: Optional[list] = None,
           retries: int = 1, temperature: Optional[float] = None, seed: Optional[int] = None) -> tuple[Rule, Optional[Enrichment]]:
    """Return (rule, enrichment). On failure the rule is the baseline, routed to review."""
    msgs = messages(slice_, examples)
    error = ""
    for attempt in range(retries + 1):
        try:
            content = client.chat(msgs, schema=ENRICHMENT_SCHEMA, temperature=temperature,
                                  seed=None if seed is None else seed + attempt)
            e = parse_enrichment(content)
            return merge(rule, e, slice_, client.model), e
        except (ValidationError, json.JSONDecodeError, LLMError, ValueError) as exc:
            error = str(exc)[:200]
    fallback = rule.model_copy(deep=True)
    fallback.status = "needs_review"
    fallback.provenance = {**fallback.provenance, "model": client.model, "enrichment_error": error}
    return fallback, None
