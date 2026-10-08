# Demo Script and Q&A Prep

## 1. Five-minute demo flow
1. **Hook (30 s):** show 80 lines of COBOL with a buried condition. Ask: "Why was this transaction flagged?" Nobody can answer quickly.
2. **Upload (30 s):** upload the program and its copybook to the explorer.
3. **Extraction (60 s):** show progress: parse, slice, extract, verify. Mention it runs fully on-premises.
4. **Result (60 s):** open rule `TXNCHK-R014`: plain-English intent, structured conditions, confidence score. Click the rule and highlight lines 42 to 45 in the source.
5. **Business query (45 s):** type "show all rules about customers under 18". Matching rules appear with source links.
6. **Trust (45 s):** show a low-confidence rule in the review queue; approve one with a reason; show the audit log.
7. **Explain and export (30 s):** show SHAP feature influence for the rule set; export JSON and PMML.
8. **Close (30 s):** results slide with precision, recall, F1 and behavioural agreement on the test set (use measured numbers only).

## 2. Demo checklist
- [ ] One clean synthetic program with 3 to 5 clear rules
- [ ] One public sample program to show it works on code we did not write
- [ ] One deliberately tricky rule (88-level or abbreviated condition) that the tool expands correctly
- [ ] One rule that is flagged `needs_review` or `external_dependency`
- [ ] Offline run tested (no internet)
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
