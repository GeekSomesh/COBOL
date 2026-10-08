# Implementation Guide: COBOL-to-Decision Pipeline

A phase-by-phase, step-by-step guide to build the project between **Tue 6 Oct and Fri 16 Oct 2026** with a team of 4.
Related docs: [architecture.md](architecture.md), [rules.md](rules.md), [requirement.md](requirement.md), [dataset.md](dataset.md), [api.md](api.md), [evaluation.md](evaluation.md), [security.md](security.md), [demo.md](demo.md).

---

## 0. Plan at a glance

### Roles
| Role | Owner | Owns |
|---|---|---|
| **A** | Parser and analysis | COBOL parsing, data dictionary, decision slicer |
| **B** | Data and testing | Synthetic generator, GnuCOBOL, gold rules, differential testing |
| **C** | LLM and rules | Prompts, schema output, normalizer, confidence, evaluation |
| **D** | API, UI, delivery | FastAPI, rule store, explorer UI, docs, demo, submission |

### Phases and gates
| Phase | Dates | Gate (done when) |
|---|---|---|
| 0. Setup and contracts | 6 to 7 Oct | Repo, environment, schema frozen, one hand-made program compiles and has a gold rule |
| 1. Vertical slice v0 | 7 to 9 Oct | One program goes upload, parse, slice, baseline rule, API response |
| 2. Data corpus | 7 to 11 Oct | About 60 programs, 3 domains, all compile, gold rules and splits |
| 3. LLM enrichment | 9 to 12 Oct | Schema-valid rules with intent, names, concepts |
| 4. Verification and confidence | 10 to 13 Oct | Symbolic check and differential testing running, confidence on every rule |
| 5. API and UI | 9 to 13 Oct | End-to-end flow in the explorer: search, trace, review, export |
| **Feature freeze** | **Tue 13 Oct night** | Only bug fixes after this |
| 6. Evaluation | 13 to 14 Oct | Results table filled with measured numbers |
| 7. Packaging and submission | 14 to 15 Oct | Demo rehearsed, repo clean, video recorded, submitted |
| Buffer | Fri 16 Oct | Emergencies only |

### Working rules
- 15-minute stand-up daily. Merge to `main` at least once a day. Never leave integration to the last day.
- Everyone codes against the frozen interfaces in Section 2, so nobody blocks anybody.
- If a phase slips by more than half a day, apply the cut list in Section 12.

---

## 1. Phase 0: Setup and contracts (6 to 7 Oct, everyone)

### 1.1 Repository
```bash
git init cobol-to-decision && cd cobol-to-decision
mkdir -p grammar src/{common,ingest,analysis,llm,rules,verify,api,ui} data/{synthetic,gold} tests docs scripts
python3 -m venv .venv && source .venv/bin/activate
pip install pydantic fastapi uvicorn jinja2 pyyaml pytest requests ollama streamlit scikit-learn shap lark
pip freeze > requirements.txt
```
Copy the ten docs into `docs/`. Add `.gitignore` (`.venv`, `__pycache__`, `data/synthetic/bin/`, model weights).

### 1.2 GnuCOBOL
```bash
sudo apt install gnucobol        # Ubuntu/Debian; on Windows use WSL2, on macOS: brew install gnucobol
cobc --version
```
Compile any program with: `cobc -x -free prog.cob -o prog` (`-free` = free-format source; real legacy code is fixed-format, so pick one format for the MVP and stay consistent).

### 1.3 Hand-made reference program (the "hello world" of the project)
Create `data/synthetic/TXNCHK.cob`:
```cobol
IDENTIFICATION DIVISION.
PROGRAM-ID. TXNCHK.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-IN-REC.
   05 CUST-AGE      PIC 9(3).
   05 TXN-AMOUNT    PIC 9(7)V99.
01 FLAG-REVIEW      PIC X VALUE 'N'.
PROCEDURE DIVISION.
MAIN-PARA.
    ACCEPT WS-IN-REC
    PERFORM CHECK-TXN
    DISPLAY "FLAG-REVIEW=" FLAG-REVIEW
    STOP RUN.
CHECK-TXN.
    IF CUST-AGE < 18 AND TXN-AMOUNT > 5000
        MOVE 'Y' TO FLAG-REVIEW
    END-IF.
```
Test it: `cobc -x -free TXNCHK.cob -o TXNCHK && echo "017000750000" | ./TXNCHK`
(Input is fixed-width: 3 digits for age, then 9 digits for the amount with two implied decimals, so `000750000` is 7500.00.) Expected output: `FLAG-REVIEW=Y`.

Create its gold rule `data/gold/TXNCHK.json` by hand, following [rules.md](rules.md). **This one example is the test case every later step must pass.**

### 1.4 Freeze the interfaces
Write `src/common/models.py` and agree on it before anyone codes further:
```python
from pydantic import BaseModel
from typing import Literal, Optional, Union

class Leaf(BaseModel):
    field: str                  # business name
    source_name: str            # COBOL name
    op: Literal["==","!=","<","<=",">",">=","in","not_in","between",
                "is_numeric","is_alphabetic","is_positive","is_negative","is_zero"]
    value: Optional[Union[int, float, str, list]] = None

class Cond(BaseModel):
    all: Optional[list["Cond | Leaf"]] = None
    any: Optional[list["Cond | Leaf"]] = None
    not_: Optional["Cond | Leaf"] = None

class Action(BaseModel):
    type: Literal["set","compute","perform","call"]
    target: Optional[str] = None
    source_name: Optional[str] = None
    value: Optional[Union[int, float, str]] = None
    expr: Optional[str] = None

class Trace(BaseModel):
    program: str
    paragraph: Optional[str]
    line_start: int
    line_end: int
    copybooks: list[str] = []

class Rule(BaseModel):
    rule_id: str
    title: str = ""
    intent: str = ""
    domain: str = ""
    conditions: Cond
    actions: list[Action]
    else_actions: list[Action] = []
    trace: Trace
    concepts: list[str] = []
    external_dependency: bool = False
    confidence: dict = {}
    status: Literal["candidate","needs_review","approved","rejected","superseded"] = "needs_review"
    provenance: dict = {}

class Slice(BaseModel):
    slice_id: str
    program: str
    paragraph: Optional[str]
    line_start: int
    line_end: int
    source_text: str
    condition: dict             # parsed condition tree (same shape as Cond, raw COBOL names)
    actions: list[dict]
    else_actions: list[dict] = []
    fields: dict                # source_name -> {"pic":..., "usage":..., "values88":...}
```
Function contracts (each is a plain Python function so people can work in parallel):
```python
analyze(program_path, copybook_dirs) -> list[Slice]            # A
baseline_rule(slice) -> Rule                                   # A + C
enrich(slice, rule) -> Rule                                    # C (LLM)
verify(rule, slice, exe_path) -> Rule                          # B + C
store.save(rule) / store.search(q, filters) -> list[Rule]      # D
```
**Done when:** all four people can run `pytest` and `cobc` on their machines, and `models.py` is merged.

---

## 2. Phase 1: Parsing and analysis (Role A, 6 to 13 Oct)

### 2.1 Choose the parsing route (decide by end of 7 Oct)
| Route | When to choose it | Notes |
|---|---|---|
| **Existing COBOL grammar** (ANTLR grammars-v4 COBOL grammar, or the ProLeap COBOL parser) | Best fidelity for real public programs | Check licence and that it handles your samples. ProLeap is Java; export the AST to JSON and read it from Python. The ANTLR Python target can be slow to generate on large grammars |
| **Subset parser** (Lark grammar or a structured line parser) | If integration of a full grammar takes more than about 1.5 days | You control the generated programs, so a parser for your subset is enough for the demo. Be honest in the demo that the grammar covers a documented subset |

Test the chosen route immediately on `TXNCHK.cob`. If you cannot get an AST for it by the end of 7 Oct, switch to the subset parser.

### 2.2 Steps
1. **Ingest and COPY resolution** (`src/ingest`): read the file, find `COPY NAME [REPLACING ...]`, inline the copybook, keep a line map `resolved_line -> (file, original_line)`. All traces use original lines.
2. **AST**: parse DATA DIVISION and PROCEDURE DIVISION into a tree. Minimum node types: paragraph, IF (condition, then, else), EVALUATE (subjects, WHEN branches), MOVE, COMPUTE, PERFORM, CALL, other statement.
3. **Data dictionary** (`src/analysis/dictionary.py`): for each data item record level, name, PIC, USAGE, `VALUE`, REDEFINES, OCCURS, and 88-level children with their values. Compute numeric scale from PIC (`9(7)V99` gives 7 integer digits, 2 decimals).
4. **Condition parser**: turn a COBOL condition into the `Cond` tree (raw names):
   - `A < 18 AND B > 5000` becomes `all[A<18, B>5000]`
   - abbreviated `A > 5 AND < 10` becomes `all[A>5, A<10]`
   - `IF IS-MINOR` (88-level) becomes the underlying comparison from the dictionary
   - `NOT =` becomes `!=`; `NOT <` becomes `>=`
5. **Decision slicer** (`src/analysis/slicer.py`): walk the AST. For each IF or EVALUATE branch emit a `Slice` with:
   - the condition tree (nested IFs: combine conditions along the path with `all`)
   - the actions inside the branch (MOVE literal gives `set`, COMPUTE gives `compute`, CALL gives `call` and `external_dependency`)
   - line range, paragraph name, source text, and the dictionary entries for fields it uses
6. **EVALUATE** support: `EVALUATE TRUE` with `WHEN cond` gives one slice per WHEN, ordered; `WHEN OTHER` gives a default slice; for `EVALUATE x WHEN 1 WHEN 2 THRU 5` use `==` and `between`. Add `not` of earlier conditions to enforce first-match semantics (see [rules.md](rules.md), section 3).
7. **Unsupported constructs** (`GO TO`, `SEARCH`, CICS/SQL): produce a slice with `unsupported=True` instead of crashing.

### 2.3 Tests
- `tests/test_analysis.py`: `TXNCHK.cob` gives exactly 1 slice with lines matching the file, condition `all[CUST-AGE < 18, TXN-AMOUNT > 5000]`, action `set FLAG-REVIEW = 'Y'`.
- Add one test per construct (nested IF, ELSE chain, EVALUATE TRUE, 88-level, abbreviated condition).

**Done when:** every program in the corpus parses and the slice count equals the generator's expected count.

---

## 3. Phase 2: Synthetic data corpus (Role B, 7 to 11 Oct)

### 3.1 Rule spec format (`data/specs/*.yaml`)
```yaml
program: TXNCHK
domain: fraud_scoring
inputs:
  - {name: CUST-AGE,   pic: "9(3)",    business: customer_age}
  - {name: TXN-AMOUNT, pic: "9(7)V99", business: transaction_amount}
outputs:
  - {name: FLAG-REVIEW, pic: "X", init: "N", business: review_flag}
rules:
  - id: R1
    when: {all: [{field: customer_age, op: "<", value: 18},
                 {field: transaction_amount, op: ">", value: 5000}]}
    then: [{set: review_flag, value: "Y"}]
style:
  structure: if            # if | if_else_chain | evaluate | nested
  cryptic_names: false     # replace names with legacy-style abbreviations
  comments: legacy         # none | legacy | misleading_old_comment
```

### 3.2 Generator (`scripts/generate.py`)
1. Load a spec; choose a **structure template** (Jinja2) based on `style.structure`.
2. Render DATA DIVISION from `inputs` and `outputs`.
3. Render the PROCEDURE DIVISION: read the record with `ACCEPT`, perform the rule paragraph, `DISPLAY "NAME=" NAME` for every output, `STOP RUN`.
4. Randomise: names (map to cryptic abbreviations), nesting vs `ELSE IF`, abbreviated conditions, a copybook for the data items (so `COPY` is exercised), legacy-style comments.
5. Write `data/synthetic/<program>.cob` (+ `.cpy`) and the **gold rule** to `data/gold/<program>.json` converted from the spec (this is the answer key, using the same schema as `Rule`).
6. Compile each program: `cobc -x -free prog.cob -o data/synthetic/bin/prog`. Fail the generation if any program does not compile.

### 3.3 Start small, then widen
| Step | Count | Domains |
|---|---|---|
| First | 5 programs | fraud scoring only (get the loop working) |
| By 9 Oct | 25 programs | + interest tiers, fee exemptions |
| By 11 Oct | about 60 programs | vary structure and imperfections per domain |
| Stretch | 12 domains | credit, KYC, loan limits, eligibility, discounts, currency, tax, penalties, insurance |

For each domain write 3 to 5 spec templates with parameter ranges (thresholds, field names, outputs), then sample specs from them.

### 3.4 Splits
Split by **template and domain variant**, not randomly by file, so near-duplicates do not leak: 70% train, 15% validation, 15% test. Save as `data/splits.json`.

### 3.5 Public samples for the demo
Download 1 or 2 public COBOL programs (GitHub open-licence repos; the IBM Z Open Editor sample programs; check licences). Hand-write gold rules for them only if time allows. Use them in the demo to show the tool works on code you did not write.

**Done when:** `python scripts/generate.py --count 60` produces compiled programs, gold JSONs and `splits.json` with no errors.

---

## 4. Phase 3: Baseline rule extractor (Roles A + C, 8 to 9 Oct)

`baseline_rule(slice) -> Rule`:
1. Convert raw condition leaves to `Leaf`: `field = source_name.lower().replace("-", "_")`, `source_name` unchanged.
2. Convert COBOL literals to numbers or strings using PIC scale.
3. Convert actions to `Action` objects; set `intent=""`, `concepts=[]`.
4. Fill `trace` (program, paragraph, line range, copybooks) and `provenance` (parser version, source hash).
5. Assign rule IDs: `<PROGRAM>-R<nnn>` in source order.

**Score it at once** against the gold rules (Section 9). This is your **AST-only baseline** and your fallback if the LLM work slips. Having this by 9 Oct also gives Role D real data for the API and UI.

---

## 5. Phase 4: LLM enrichment (Role C, 9 to 12 Oct)

### 5.1 What the model may and may not do
- **May:** write `intent`, `title`, business `field` names, `concepts`.
- **May not:** change operators, literal values, action values or trace. These always come from the AST.

### 5.2 Prompt template (`src/llm/prompts.py`)
```
You document legacy COBOL business rules.
Given a code slice and data dictionary, return ONLY JSON:
{"title": str, "intent": str, "field_names": {"<COBOL-NAME>": "<snake_case_business_name>"}, "concepts": [str]}
Rules: do not invent fields; one sentence for intent; 1 to 5 concepts.

DATA DICTIONARY:
{dictionary}

CODE (lines {start}-{end}):
{source_text}
```
Strip or delimit COBOL comments to resist prompt injection (see [security.md](security.md), T2). Treat the code as data, never as instructions.

### 5.3 Call and validate
```python
import ollama, json
from pydantic import BaseModel, ValidationError

class Enrichment(BaseModel):
    title: str
    intent: str
    field_names: dict[str, str]
    concepts: list[str]

def enrich(slice, rule, model="qwen2.5-coder:7b", retries=1):   # any local code model that fits your hardware
    prompt = build_prompt(slice)
    for _ in range(retries + 1):
        out = ollama.chat(model=model, messages=[{"role":"user","content":prompt}], format="json")
        try:
            e = Enrichment.model_validate_json(out["message"]["content"])
            return merge(rule, e)          # only title, intent, names, concepts
        except ValidationError:
            continue
    rule.status = "needs_review"           # model failed: keep baseline rule
    return rule
```
`merge` renames `field` values from `field_names` (only for names that exist in the dictionary) and ignores everything else the model returned.

### 5.4 Prompting first, fine-tuning only if on schedule
1. **9 to 11 Oct:** get few-shot prompting working (put 2 to 3 examples from the training split in the prompt). Measure on validation.
2. **Decision gate, evening of 11 Oct:** if the corpus is ready and the pipeline works end to end, spend **at most one day** on a LoRA fine-tune. Otherwise skip it and describe the model as "prompted code LLM".
3. Fine-tune recipe (if go): build JSONL of `{prompt, completion}` from the training split using the Enrichment format; use Hugging Face PEFT/TRL (or Unsloth) with LoRA on a small code model (1 to 7B) on a free Colab/Kaggle GPU; evaluate on validation against the prompting-only run. Serving a fine-tuned model locally (for example via transformers or llama.cpp) can take extra time, so budget for it.
4. Report honestly: the fine-tune only counts if it beats prompting-only on your validation numbers.

**Done when:** every slice in the validation split returns a schema-valid `Rule` with an intent, and failures fall back to the baseline rule.

---

## 6. Phase 5: Verification and confidence (Roles B + C, 10 to 13 Oct)

### 6.1 Rule evaluator (`src/verify/engine.py`)
```python
import operator
OPS = {"<":operator.lt,"<=":operator.le,">":operator.gt,">=":operator.ge,"==":operator.eq,"!=":operator.ne}

def eval_cond(c, row):
    if "all" in c and c["all"] is not None: return all(eval_cond(x,row) for x in c["all"])
    if "any" in c and c["any"] is not None: return any(eval_cond(x,row) for x in c["any"])
    if c.get("not_") is not None:           return not eval_cond(c["not_"],row)
    v = row[c["field"]]
    if c["op"] == "between": return c["value"][0] <= v <= c["value"][1]
    if c["op"] == "in":      return v in c["value"]
    return OPS[c["op"]](v, c["value"])

def apply_rule(rule, row, state):
    acts = rule["actions"] if eval_cond(rule["conditions"], row) else rule["else_actions"]
    for a in acts:
        if a["type"] == "set": state[a["target"]] = a["value"]
    return state
```
For ordered rule sets (ELSE IF, EVALUATE) apply in order and stop at the first match.

### 6.2 Symbolic check
Evaluate the **AST condition** and the **extracted rule condition** on a grid of inputs (boundary values around every threshold: `t-1, t, t+1` in the field's smallest unit, plus min, max and random values). Structural score = share of inputs where both agree (1.0 means equivalent on the grid).

### 6.3 Differential test against compiled COBOL (`src/verify/differential.py`)
1. Build input rows (same grid as above) per program.
2. Format each row as a fixed-width record from the PIC clauses (digits only, implied decimals removed, zero-padded).
3. Run the compiled program: `subprocess.run([exe], input=record+"\n", capture_output=True, text=True, timeout=5)`.
4. Parse output lines `NAME=VALUE` into a dict.
5. Compute the same outputs with `apply_rule` on the extracted rules.
6. Agreement = share of rows with equal outputs. Report it per rule and per program.

### 6.4 Confidence
Use the weights in [rules.md](rules.md) section 5:
```python
score = 0.40*structural + 0.30*differential + 0.20*consistency + 0.10*naming
```
- `consistency`: run the enrichment 3 times (temperature above 0) and measure agreement of field names and intent keywords. Start at 1.0 until you implement it.
- `naming`: 1.0 if every field name maps to a dictionary entry with comment support, lower otherwise.
- Status: unsupported construct or external dependency means `needs_review`; score below 0.70 means `needs_review`; otherwise `candidate`. Only a reviewer sets `approved`.

**Done when:** every rule in the test split has `confidence.score` and component breakdown, and a deliberately broken rule is caught.

---

## 7. Phase 6: Store, API and UI (Role D, 6 to 13 Oct)

### 7.1 Rule store (`src/api/store.py`)
SQLite is enough:
```sql
CREATE TABLE programs(id TEXT PRIMARY KEY, name TEXT, source TEXT, status TEXT);
CREATE TABLE rules(rule_id TEXT, version INT, program_id TEXT, json TEXT, status TEXT,
                   confidence REAL, concepts TEXT, PRIMARY KEY(rule_id, version));
CREATE TABLE reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT, version INT,
                     action TEXT, reason TEXT, user TEXT, ts TEXT);
```

### 7.2 API skeleton (`src/api/main.py`)
```python
from fastapi import FastAPI, UploadFile
app = FastAPI(title="COBOL-to-Decision")

@app.post("/v1/programs")                 # upload program + copybooks
@app.post("/v1/programs/{pid}/extract")   # run pipeline (background task), store rules
@app.get("/v1/jobs/{job_id}")
@app.get("/v1/rules")                     # q, concept, program, status, min_confidence
@app.get("/v1/rules/{rule_id}")
@app.get("/v1/rules/{rule_id}/trace")
@app.patch("/v1/rules/{rule_id}")         # approve / reject / edit, writes to reviews
@app.get("/v1/export")                    # format=json|tree|pmml
```
Follow [api.md](api.md). Run with `uvicorn src.api.main:app --reload`. FastAPI serves `/openapi.json` and `/docs` for free.

**Order of work for D:** 6 to 8 Oct skeleton with fake data; 9 Oct plug in the baseline extractor (the v0 vertical slice); 10 to 12 Oct UI; 13 Oct export and polish.

### 7.3 Concept search
MVP: keyword match over `concepts`, `title`, `intent` and field names, with a small synonym map (`minor` to `age under 18`, `amount` to `value`). Stretch: sentence embeddings.

### 7.4 Explorer UI (Streamlit, `src/ui/app.py`)
Screens:
1. **Upload and extract:** upload a program, press Extract, show progress.
2. **Rules list:** search box, filters (status, confidence), table of rules.
3. **Rule detail:** left column COBOL source with the rule's lines highlighted (render as HTML `<pre>` with a background on lines `line_start..line_end`), right column intent, conditions, actions, confidence breakdown.
4. **Review queue:** list of `needs_review` rules with Approve / Reject / Edit and a reason box.
5. **Export:** buttons for JSON and tree JSON.

Run: `streamlit run src/ui/app.py`.

### 7.5 Exports
- **JSON:** dump rules.
- **Decision-tree JSON:** group rules by a shared first test (for example account type), then nest.
- **PMML (stretch):** hand-generate a `TreeModel` XML for tree-shaped sets; avoid heavy dependencies.

### 7.6 Stretch: `/score` and SHAP
- `/score`: load approved rules, apply `apply_rule`, return decision and fired rules; **do not store or log inputs**.
- SHAP: sample inputs, label them by the rules, train a scikit-learn tree or gradient boosting model, use `shap.TreeExplainer`, plot feature influence. Say in the demo that this is a surrogate of the rules, which are already explicit.

**Done when:** a user can upload a program, extract, search "customers under 18", click a rule and see the highlighted source, approve it, and export it.

---

## 8. Phase 7: Evaluation (Role C with B, 13 to 14 Oct)

Follow [evaluation.md](evaluation.md). Steps:
1. Freeze code at feature freeze. Tag the commit used for results (`git tag eval-v1`).
2. `scripts/evaluate.py`: for each system (AST-only, LLM-only, LLM on slices, full pipeline) run on the **test split** and write `results/<system>.json`.
3. **Rule matching:** normalize both rules (sort `all`/`any` children, canonical operators, numeric types) and compare conditions and actions for equality; for harder cases compare on the boundary grid.
```python
def key(rule):   # canonical form for matching
    return (canon_cond(rule["conditions"]), canon_actions(rule["actions"]), canon_actions(rule["else_actions"]))
tp = len({key(r) for r in pred} & {key(g) for g in gold})
precision = tp / max(len(pred), 1); recall = tp / max(len(gold), 1)
f1 = 2*precision*recall / max(precision+recall, 1e-9)
```
4. Compute behavioural agreement (Section 6.3), traceability accuracy (line range overlap at least 80%), latency.
5. Error analysis: list the 10 worst failures by category and keep 3 as honest examples for the demo.
6. Fill the table in `evaluation.md` with **measured** numbers only. If a target was missed, report the real number.

---

## 9. Phase 8: Packaging, demo, submission (Role D leads, 14 to 15 Oct)

### Checklist
- [ ] `README.md` with: what it is, setup in 5 commands, how to run extract and UI, results table, limitations
- [ ] `docs/` with the ten documents (update any claim that is not true of the final build; mark unbuilt features as planned)
- [ ] `data/synthetic` (programs, gold rules, splits) included or reproducible with one command
- [ ] `requirements.txt` pinned; a `Makefile` or `run.sh` with `make demo`
- [ ] Demo walkthrough from [demo.md](demo.md) rehearsed twice, offline
- [ ] 2 to 3 minute backup screen recording
- [ ] Results slide using only measured numbers
- [ ] Submission uploaded and confirmation screenshot saved (do this by the evening of **Thu 15 Oct**)

### Claims check before submitting
- Say "fine-tuned" only if you fine-tuned and it beat prompting-only.
- Do not cite statistics you cannot source.
- Mark `/score`, PMML, SHAP and conflict detection as built only if they work in the demo.

---

## 10. Day-by-day schedule

| Date | A (parser) | B (data/testing) | C (LLM/rules) | D (API/UI/delivery) |
|---|---|---|---|---|
| Tue 6 | Repo, venv, evaluate parsing routes | Install GnuCOBOL, compile `TXNCHK` | Install Ollama, pull a code model | Repo, docs into `docs/`, `models.py` draft |
| Wed 7 | Pick route, AST for `TXNCHK` | Spec format, first template | First prompt, test on `TXNCHK` | FastAPI skeleton, SQLite store |
| Thu 8 | Dictionary, condition parser | Generator v1: 5 programs compile | Enrichment merge, pydantic validation | `/rules` with fake data |
| Fri 9 | Slicer, EVALUATE basics. **Gate: v0 slice** | 25 programs, gold rules | Baseline rule builder with A | Plug baseline into API. **Gate: v0 slice** |
| Sat 10 | 88-levels, nested IF, abbreviated conds | 60 programs, splits | Few-shot prompting, validation runs | UI: list and detail with highlighting |
| Sun 11 | Unsupported-construct handling | Differential harness v1. **Gate: data ready** | Confidence score. **Fine-tune go/no-go** | UI: search, review queue |
| Mon 12 | Bug fixes from corpus | Differential on all programs | Fine-tune (if go) or prompt polish | Review actions, audit log |
| Tue 13 | Bug fixes. **Feature freeze tonight** | Grid tests, symbolic check | Run full pipeline on test split | Export JSON/tree. Stretch: `/score` |
| Wed 14 | Support fixes only | Evaluation runs, error analysis | Evaluation table, ablations | README, results slide |
| Thu 15 | Demo rehearsal | Demo rehearsal, backup data | Demo rehearsal | Video, final packaging, **submit** |
| Fri 16 | Buffer | Buffer | Buffer | Buffer |

---

## 11. Testing and git workflow
- **Unit tests** for the condition parser, normalizer, evaluator and confidence function (`pytest -q`).
- **Golden test:** `TXNCHK` must always produce its gold rule. Run it in CI or a pre-push hook.
- **Corpus test:** `scripts/run_corpus.py` runs the pipeline on all programs and prints counts: programs parsed, slices found, rules valid, rules matching gold.
- **Branching:** `main` always runs. Short feature branches (`feat/parser-evaluate`), merge daily, pull before you start.
- **Commit messages:** `component: what changed` (for example `slicer: handle ELSE IF chains`).

## 12. Cut list and troubleshooting

### Cut list (cut in this order if behind)
1. PMML export and `/score`
2. SHAP view and conflict detection
3. Extra domains beyond the first three
4. Fine-tuning (use prompting)
5. Embedding-based search (use keywords)

**Do not cut:** AST-based extraction, verification and confidence, source-line tracing, the review step. These are the main differences from "just use an LLM".

### Common problems
| Problem | Fix |
|---|---|
| Grammar generation is slow or the parser fails on your samples | Switch to the subset parser; document the subset |
| LLM returns invalid JSON | Use `format="json"`, validate with pydantic, retry once, fall back to the baseline rule |
| LLM changes a threshold or operator | `merge` ignores all fields except title, intent, names, concepts; the AST wins |
| COBOL numeric formatting mismatches in differential tests | Format inputs from PIC (zero-padded digits, implied decimals); compare as decimals |
| Rule agreement is low on EVALUATE | Check first-match ordering: add `not` of earlier conditions |
| Generated programs do not compile | Fail generation early; fix the template, not the program |
| Time is running out | Apply the cut list and protect the demo path in [demo.md](demo.md) |

## 13. Definition of done (project level)
- Upload a COBOL program (synthetic or public sample) and get structured rules with intent, confidence and exact source lines.
- Search by business concept and open the rule next to its COBOL.
- Review queue works, with an audit log.
- Export as JSON (and tree JSON).
- Evaluation table with measured numbers on a held-out test split, including the AST-only baseline.
- Everything runs offline on one machine.
