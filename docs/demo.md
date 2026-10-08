# Demo Script and Q&A Prep

## 1. Five-minute demo flow (as built)
Before the demo, run `python scripts/load_demo.py --reset`, then
`python -m uvicorn src.api.main:app --port 8000`. Open http://localhost:8000 and sign in
with the admin token from `data/dev_tokens.json`.

1. **Hook (30 s):** open `data/synthetic/INT0003.cob` or another generated program with cryptic names, 88-levels and a stale comment. Ask: "What rate does a 12-month deposit get, and why?" Nobody can answer quickly.
2. **Upload (30 s):** Programs tab, upload `TXNCHK.cob` (or any corpus program with its `...C.cpy` copybook).
3. **Extraction (60 s):** the progress bar walks through parse, enrich, differential and verify. Everything runs locally: GnuCOBOL in WSL and the fine-tuned model in Ollama. Nothing leaves the machine.
4. **Result (60 s):** open `TXNCHK-R001`. Show the plain-English intent, the conditions with business names (hover a name to see the COBOL name), the confidence breakdown, and source lines 16 to 18 highlighted next to the rule.
5. **Business query (45 s):** search "customers under 18". The minor-transaction rules rank first, each with its source link.
6. **Trust (45 s):** in the Review queue, open a rule flagged `needs_review`. Good examples are the FRD program's `CALL 'FRDALERT'` rule (external dependency) or an IBM SAM1 rule (not behaviourally verified). Approve one with a reason, then show the Audit log.
7. **Explain and export (30 s):** Insights tab: SHAP influence for a rule set. Export tab: JSON, decision tree and PMML.
8. **Close (30 s):** results slide from `results/RESULTS.md`. Use only these measured numbers, and say they come from synthetic held-out templates.

## 2. Demo checklist
- [x] One clean synthetic program with 3 to 5 clear rules (TXNCHK, INT0003, LON0002)
- [x] One public sample program to show it works on code we did not write (IBM SAM1/SAM2)
- [x] One tricky rule that is expanded correctly (INT0003: 88-level bands; CONSTRUCTS fixture: abbreviated `A > 17 AND < 65 AND R = 'A1' OR 'A2'`)
- [x] One rule flagged `needs_review` or `external_dependency` (FRD `CALL 'FRDALERT'`, SAM1 `GO TO` / `CALL 'SAM2'`)
- [ ] Offline run tested (no internet): unplug, run `load_demo.py` and the UI
- [ ] Backup screen recording

## 3. Likely judge questions

**How is this different from tools that translate or explain COBOL?**
Translation tools produce equivalent code. We extract decision logic as structured, verified, queryable rules with line-level traceability, so auditors and AI systems can use them directly.

**How do you stop the LLM from hallucinating rules?**
Operators and literals come from the AST, not the model. Each rule is checked symbolically against its source condition and by differential testing against compiled COBOL. Low-confidence rules go to human review, and only a reviewer can approve.

**Does it touch customer data?**
No. It reads source code only. Datasets are synthetic or public. The optional scoring endpoint is stateless and does not store inputs.

**Why does this fit IBM Z?**
The code lives on the mainframe. Running the pipeline there keeps source code on-platform with zero data egress, and the rules can feed real-time decisioning next to the workload.

**What about code you can't parse?**
Unsupported constructs and external calls are flagged rather than guessed, and routed to review. The grammar covers a documented subset that we extend iteratively.

**How do you know it works?**
We report precision, recall, F1 and behavioural agreement on a held-out test split with gold rules, against baselines (AST-only, LLM-only, no fine-tuning) described in [evaluation.md](evaluation.md).

**What would you do next?**
Wider dialect support, impact analysis when copybooks change, and CI checks that detect rule drift between releases.

## 4. Things not to claim
- Do not quote accuracy numbers you have not measured.
- Do not cite statistics (share of banking running on COBOL, number of COBOL experts) unless you can name the source.
- Do not describe features marked *(planned)* or *(stretch)* as built.
