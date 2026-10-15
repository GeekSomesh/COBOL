# User guide

For business analysts, reviewers and auditors. You need a sign-in token (or single
sign-on) and a browser; you do not need to read COBOL.

Open the explorer at `http://<server>:8000/` and sign in. The tabs:

| Tab | What it is for |
|---|---|
| Rules | Search and filter every extracted rule; open one to see its logic next to the source |
| Review queue | Rules waiting for a decision, least confident first; bulk approve or reject |
| Programs | Upload programs and copybooks, start an extraction, edit the business glossary |
| Repository | How programs, copybooks, JCL jobs and datasets connect; impact analysis; rule drift |
| Export | Download rules as JSON, decision trees, PMML 4.4 or DMN 1.3 |
| Score | Run a decision with the approved rules of a program |
| Insights | Which inputs drive the decisions (SHAP); duplicate and conflicting rules |
| Audit log | Every review action; check that the log has not been altered |

Keyboard: arrow keys move between tabs, Enter opens a rule, Escape closes the
notifications panel. A "Skip to content" link appears on the first Tab press.

## 1. Getting rules out of a program

1. **Programs → Upload a program.** Choose the `.cbl` / `.cob` file and its copybooks.
   The file is parsed immediately; a parse error is reported before anything is stored.
2. Extraction starts on its own. The progress bar shows the stages: parse, LLM
   naming (if a model is configured), behavioural test against the compiled program,
   verification. A typical program takes seconds to a few minutes.
3. The rules appear under **Rules** and in the **Review queue**.

Re-uploading a changed program keeps the rule IDs of unchanged decisions and their
review decisions; changed rules get a new version, and an approved predecessor is
marked superseded.

## 2. Reading a rule

A rule says *when* (condition) and *then* (actions), in business names, with the
COBOL names on hover. Next to it is the source with the rule's lines highlighted.

Other sections, when they apply:

| Section | Meaning |
|---|---|
| **Only reached when** | Conditions of the code that calls this paragraph. The rule only applies when these hold too (for example, the scoring section only runs for validated input). |
| **Computed fields** | How a field the rule tests was calculated earlier, in terms of inputs (`WS-SCORE = CUST-AGE * 2 + ACCT-YEARS`). |
| **Lookup table** | The rule is one row of a hard-coded table (tier G → rate 0.015), with the line the row comes from. |
| **Dead code** | No path from the program's start reaches the rule. |
| **Called from** | The paragraphs that PERFORM (or GO TO) this one. |

### Confidence

The bar shows a score from 0 to 1 and its parts:

* **structural**: the rule is logically the parser's reading of the code (proven with
  an SMT solver when the forms differ);
* **differential**: share of test inputs on which the rules and the compiled COBOL
  program give the same outputs (byte for byte for called subprograms);
* **consistency**: the language model gives the same explanation when asked again;
* **naming**: the business names fit the COBOL names and comments.

`p_correct`, when shown, is the calibrated probability that a rule with this score
is correct, learned from thousands of deliberately broken rules (calibration error
0.03 on held-out programs).

A rule is a **candidate** only when everything checks out. It goes to **needs
review** with a reason when, for example: it disagrees with the compiled program,
its condition can never be true, it uses a construct the parser cannot model
(listed), it depends on an external program, its text says "over 740" while the code
tests "740 or more", or its intent cites a number that is not in the code.

## 3. Reviewing

In the rule panel:

* **Approve / Reject** with a reason (required). Reasons are kept in the audit log.
* **Edit title / intent** creates a new version. Whoever edits a rule cannot approve
  that version (four-eyes rule).
* **Assign** the rule to a colleague; they get a notification (bell icon, top right).
* **Comments**: questions and evidence ("matches policy 4.2"). Everyone involved in
  the rule is notified.
* Your organisation may require more than one approval; the panel shows
  "Approvals: 1 of 2" until enough reviewers have approved.

In the **Review queue**, tick several rules, give one reason and use **Approve
selected** or **Reject selected**. Each rule succeeds or fails on its own (for
example, you cannot approve twice or approve your own edit). "Assigned to me" filters
the queue.

## 4. Repository, impact and drift

**Repository** shows every uploaded program, the copybooks it includes, the programs
it calls (also calls through a variable such as `CALL WS-PGM`, resolved from the
variable's value), and, once JCL is uploaded, the jobs and steps that run each
program and the datasets they read and write. Findings list problems worth a look:
the same PROGRAM-ID in two sources, a job running a program that is not uploaded, a
call that cannot be resolved.

**Impact analysis** answers "what changes if...":

* a **field** changes: the rules that read or write it, in every program;
* a **copybook** changes: the programs that include it and their rules;
* a **program** changes: the programs that call it, directly or through others, and the JCL jobs that run them;
* a **dataset** changes: the programs that read or write it and their jobs.

Click a node in the graph to analyse it.

**Rule drift between two uploads** compares two versions of a program: rules added,
removed, moved and changed. For a changed rule it lists the exact differences
(threshold 740 → 760, a condition removed) and an input on which the old and new
rule decide differently. Approved rules that are affected are highlighted.

## 5. Using the rules

* **Export**: rules JSON (complete, with traces and confidence), decision-tree JSON,
  PMML 4.4 (single-output decision blocks) and DMN 1.3 (every decision block, as
  decision tables with first-match hit policy). PMML and DMN are checked against the
  official schemas before download.
* **Score**: pick a program, fill in its inputs, get the decision from the approved
  rules and the rules that fired. Inputs are not stored or logged.
* **Insights**: the inputs that influence a program's decisions most, and pairs of
  rules (also across programs) that can fire together and set a field to different
  values, with an example input.

## 6. Business glossary

Under **Programs → Business glossary**, admins maintain the organisation's words:

```json
{"fields": {"WS-CSCR": "credit_score"},
 "terms":  {"CSCR": "credit score", "AMT": "amount", "DTI": "debt to income"}}
```

`fields` fix a field's business name everywhere (they override the language model).
`terms` expand abbreviations in names and are shown to the model. Changes apply to
extractions started afterwards.

## 7. What the tool does not do

It describes what the code does, not whether the code is right. It does not run
CICS or DB2 (EXEC statements are kept on the rule and flagged), and file-processing
main programs are verified structurally but not run (called subprograms are). See
[limitations.md](limitations.md).
