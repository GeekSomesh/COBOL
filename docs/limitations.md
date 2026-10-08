# Known limitations

These are honest limits of the prototype as built. Each was observed or measured.

## Parsing
- The parser covers a documented subset of COBOL ([parser_subset.md](parser_subset.md)). Anything outside it is recorded as a diagnostic or flagged `unsupported`. It is never guessed.
- There is no control-flow graph (FR-05) yet. Rules inside a PERFORMed paragraph do not inherit the caller's conditions, and GO TO flows are flagged rather than followed.
- Subscripts and reference modification stay in the source name (`CUST-MONTH(I)`). Table lookups via `SEARCH` are flagged, not modelled.

## Data and accuracy claims
- The 1.000 rule F1 is measured on synthetic programs. The generator and parser share the slicing conventions, and the generated code stays inside the parser subset. The number shows the parser is correct on that subset; it says nothing about arbitrary legacy code.
- The IBM public samples (SAM1, SAM2) parse without diagnostics, but they have no hand-written gold rules, so their rules are not scored.
- Gold titles, intents and concepts come from templates. A model fine-tuned on them learns that house style; real programs and real auditors may want different wording.

## Verification
- Differential testing needs the batch convention used by the corpus: `ACCEPT` one fixed-width record, then `DISPLAY "NAME=" NAME`. Programs with file or database I/O (like the IBM samples) get symbolic checks only. Their rules are routed to review with the warning "not behaviourally verified".
- COBOL arithmetic is approximated. Results are truncated to the target PIC, high-order digits are dropped, and outputs may differ by one unit in the last decimal place (ROUNDED versus truncation). ON SIZE ERROR and COMP-3 inputs are not modelled by the harness.
- The input grid covers t-1, t and t+1 around every threshold plus random rows, but it is not an exhaustive proof. Equivalence is checked by canonical form plus grid agreement, not by SMT.
- Self-consistency uses two extra sampled LLM runs per rule, so it is a coarse signal.

## LLM
- The fine-tuned model is Qwen2.5-Coder-1.5B-Instruct with a LoRA adapter, trained only on the train split. Validation and test use held-out templates, but the same synthetic domain.
- **The fine-tune did not generalise.** It beat the matched prompted 1.5B on the validation templates and was selected there. On the test templates it scored below both the prompted 1.5B and the prompted 7B for field names and intents (results/RESULTS.md). The training loss went near zero while the validation loss rose, so it learned the house style of 12 training templates. Per the plan's claims rule, we do not claim fine-tuning improves results. The default enrichment model is therefore the prompted 7B; the fine-tuned 1.5B stays available as a roughly 3x faster option. More template diversity, or mixing few-shot prompts into training, are the obvious next steps.
- Ollama 0.35 no longer imports Qwen2 safetensors or LoRA adapters, so the fine-tuned model is served as a Q8_0 GGUF built with llama.cpp's converter. The prompted 1.5B baseline was converted the same way, so the two differ only by the fine-tune.
- The model never sets operators, literals, actions or traces: `merge()` ignores everything except title, intent, business names and concepts. A wrong intent is still possible. The number-grounding check catches invented numbers, but not a paraphrased comparison: one demo intent says "more than 3 late payments" for a condition that is `>= 3`. The structured condition shown next to the intent is authoritative, and human review remains necessary.

## Product
- Development tokens are generated locally. There is no TLS, SSO or RACF integration; these are planned (security.md 4.2).
- PMML is produced only for decision blocks that write a single literal output field. It is checked for well-formed XML, not executed in a PMML engine.
- The API image builds and was smoke-tested (upload, extract, differential test with the image's own GnuCOBOL). The two-service `docker-compose.yml` (API + Ollama on an internal network) was not run end to end. The demo runs natively (Windows, GnuCOBOL in WSL).
