# Known limitations

Honest limits of the system as built (2026-10-16). Each was observed or measured.

## Parsing and analysis
- The parser covers a documented subset of COBOL ([parser_subset.md](parser_subset.md)). Anything outside it is recorded as a diagnostic or flagged `unsupported`, and such rules go to review. It is never guessed.
- Control flow is followed across paragraphs (PERFORM, THRU, SECTION, loops, GO TO, fall-through), but GO TO DEPENDING ON and ALTER are only flagged, and the walk has a budget that grows with program size. Beyond it, contexts may be incomplete and every rule is treated as reachable (not reached by 51,571-line generated programs).
- A rule flattened from nested decisions is a static description of the path. When a branch changes a field before a nested test of it, the condition shown reads as if all parts were tested at once. The verification interpreter evaluates each part at its own decision, and the "Only reached when" and "Computed fields" sections help, but a reviewer must read such rules carefully.
- SEARCH is expanded into per-row rules only for tables initialised with VALUE clauses that the program never modifies. Tables loaded at run time stay review items.
- Nested OCCURS, reference modification (`X(1:3)`) and signed or packed fields inside REDEFINES overlays are approximated in the byte-level model.
- CICS and DB2: EXEC statements are kept on the rule (text) and flagged as external; their semantics are not modelled.

## Data and accuracy claims
- The 1.000 rule F1 on the corpus is measured on synthetic programs. The generator and parser share the slicing conventions and the generated code stays inside the parser subset. It shows the parser is correct on that subset, not on arbitrary legacy code.
- Real code: the expected rules for IBM's SAM1/SAM2 (45 rules) were **drafted by an AI from the source and the documented conventions, not reviewed by a COBOL expert**. The parser now matches all 45 (first run: 41; one parser fix, one draft fix, both recorded in the files). Treat it as indicative evidence.
- Gold titles, intents and concepts come from templates, so models are scored against one house style.

## Verification
- Behavioural testing runs two kinds of program: batch programs that `ACCEPT` input and `DISPLAY` results, and CALLed subprograms (a driver is generated). File-processing main programs (like IBM's SAM1) are checked symbolically only; their rules carry "not behaviourally verified".
- Inputs that make COBOL compare non-digit bytes as a number are undefined behaviour; those rows are excluded from agreement and reported (35 of 462 for SAM2).
- COBOL arithmetic is approximated: results are truncated to the target PICTURE, outputs may differ by one unit in the last decimal place (ROUNDED versus truncation), ON SIZE ERROR is not modelled.
- Differential inputs are boundary values, targeted searches, random rows and, for subprograms, a coverage-guided search. That is strong evidence, not proof. The structural check, impossible-rule detection and conflict detection are exact (SMT) within their fragment; expressions they cannot encode fall back to sampling.
- In the fault-injection study, 11 of 151 injected faults are not routed to review because the SMT check proves the faulty rule logically identical to the source (the fault cannot change any decision). Every behaviour-changing fault is routed to review.
- The confidence calibration (`p_correct`) is fitted on synthetic fault injection. It is well calibrated there (ECE 0.032 on held-out programs), but the error mix on real code may differ.
- Self-consistency uses two extra sampled LLM runs per rule, so it is a coarse signal.

## LLM
- Models are scored on synthetic templates. Validation and test templates are held out, but the domain is the same.
- **Fine-tuning:** the first adapter (12 training templates) overfit and lost to prompting on test. The second adapter (21 templates, three more domains) beats prompting the same 1.5B base on unseen test templates for field names (0.590 vs 0.563) and ties on intent (0.839 vs 0.837). It is still below the prompted 7B (0.632 / 0.856), which stays the default. One training run per configuration; differences of a point or two are within run-to-run noise.
- The model never sets operators, literals, actions or traces (`merge()` ignores everything except title, intent, names and concepts). A wrong intent is still possible. Two checks route suspicious intents to review: numbers that are not in the code, and comparison words that contradict the rule ("over 740" for `>= 740`; 0 false alarms on 965 gold intents, 97% of deliberately flipped wordings caught). Paraphrases outside those patterns are not caught; the structured condition shown next to the intent is authoritative.
- Ollama 0.35 does not import Qwen2 safetensors or LoRA adapters, so fine-tuned models are served as Q8_0 GGUF files built with llama.cpp's converter; the prompted 1.5B baseline is converted the same way.

## Product and operations
- Authentication: static tokens and OpenID Connect. No RACF integration.
- PMML covers decision blocks that write one literal output (114 of 354 corpus programs); DMN covers all blocks. Both are schema-validated, not executed in a third-party engine.
- PostgreSQL is supported through a small SQL adapter (no ORM) and tested on PostgreSQL 16; migrations beyond "create if missing" and "add column if missing" are manual.
- The Docker toolchain backend mounts only the project and temp folders; programs elsewhere need `COBOL_DOCKER_MOUNTS`.
- CI (`.github/workflows/ci.yml`) runs on every push: tests with native GnuCOBOL and PostgreSQL, corpus and IBM regressions, and the container build. It does not run the LLM evaluations (no GPU).
