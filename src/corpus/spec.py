"""Sample a program spec (data/specs/<PROGRAM>.yaml) from a domain template."""

from __future__ import annotations

import random
from typing import Any

from src.corpus.templates import DOMAIN_PREFIX, INPUTS, OUTPUTS, Template

RECORD_NAMES = ["WS-IN-REC", "IN-RECORD", "WS-INPUT", "INPUT-AREA"]
OUT_RECORD_NAMES = ["WS-OUT-REC", "OUT-RECORD", "WS-RESULTS", "RESULT-AREA"]
MAIN_NAMES = ["0000-MAIN", "MAIN-PARA", "MAINLINE", "A000-MAIN"]
REPLACING_PREFIXES = ["IN", "TX", "CU", "AC"]
DATE_NAMES = ["PROC-DATE", "RUN-DATE", "POST-DATE"]


def sample_style(r: random.Random) -> dict[str, Any]:
    copybook = r.random() < 0.6
    return {
        "cryptic_names": r.random() < 0.35,
        "comments": r.choices(["none", "legacy", "misleading", "injection"], [25, 55, 15, 5])[0],
        "copybook": copybook,
        "replacing": copybook and r.random() < 0.35,
        "abbreviated": r.random() < 0.4,
        "use_88": r.random() < 0.35,
        "word_operators": r.random() < 0.3,
        "dead_code": r.random() < 0.2,
        "noise_fields": r.random() < 0.4,
        "decimal_literals": r.random() < 0.3,
        "environment_division": r.random() < 0.5,
    }


def _subject_field(rules: list[dict]) -> str | None:
    """Field usable as EVALUATE subject: every conditional rule is ==, in or between on it."""
    fields = set()
    for rl in rules:
        w = rl["when"]
        if w is None:
            continue
        if "field" not in w or w["op"] not in ("==", "in", "between"):
            return None
        fields.add(w["field"])
    return fields.pop() if len(fields) == 1 else None


def sample_spec(template: Template, number: int, r: random.Random, seed: int) -> dict[str, Any]:
    program = f"{DOMAIN_PREFIX[template.domain]}{number:04d}"
    style = sample_style(r)
    blocks = template.build(r)

    used: set[str] = set()

    def pick(options: list[str]) -> str:
        choices = [o for o in options if o not in used] or [f"{options[0]}-{len(used)}"]
        name = r.choice(choices)
        used.add(name)
        return name

    prefix = r.choice(REPLACING_PREFIXES) if style["replacing"] else None
    inputs = []
    for business in template.inputs:
        fd = INPUTS[business]
        name = pick(fd.cryptic if style["cryptic_names"] else fd.clean)
        if prefix:
            name = f"{prefix}-{name}"
        inputs.append({"name": name, "pic": fd.pic, "business": business, "comment": fd.comment})
    outputs = []
    for business in template.outputs:
        fd = OUTPUTS[business]
        outputs.append({"name": pick(fd.cryptic if style["cryptic_names"] else fd.clean),
                        "pic": fd.pic, "business": business, "init": fd.init, "comment": fd.comment})

    noise = None
    if style["noise_fields"]:
        date = pick(DATE_NAMES)
        noise = {"date": f"{prefix}-{date}" if prefix else date, "filler": r.choice([2, 4, 6])}

    for b in blocks:
        b["paragraph"] = pick(b.pop("paragraphs"))
        if b["structure"] in ("chain", "nested"):
            renders = list(b.pop("renders", ["else_if"]))
            if b["structure"] == "nested":
                renders = [x for x in renders if x != "evaluate_subject"]
            if "evaluate_subject" in renders and _subject_field(b["rules"]) is None:
                renders.remove("evaluate_subject")
            b["render"] = r.choice(renders)
            if b["render"] == "evaluate_subject":
                b["subject"] = _subject_field(b["rules"])
            if b["render"] == "else_if":
                b["compact"] = r.random() < 0.5
        for k, rl in enumerate(b["rules"], start=1):
            rl["id"] = f"R{k}"

    return {
        "program": program,
        "program_file": f"{program}.cob",
        "domain": template.domain,
        "template": template.id,
        "seed": seed,
        "record": pick(RECORD_NAMES),
        "out_record": pick(OUT_RECORD_NAMES),
        "main": pick(MAIN_NAMES),
        "copybook": f"{program}C.cpy" if style["copybook"] else None,
        "prefix": prefix,
        "inputs": inputs,
        "outputs": outputs,
        "noise": noise,
        "blocks": blocks,
        "style": style,
    }
