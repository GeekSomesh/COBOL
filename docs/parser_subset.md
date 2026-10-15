# Parser subset and slicing conventions

The parser is a hand-written recursive-descent parser for a documented subset of
Enterprise/GnuCOBOL syntax (implementation.md 2.1, "subset parser" route). It
replaced the planned Lark grammar because COBOL's separator period closes every
open IF/EVALUATE at once and ELSE binds to the nearest IF. Both are a few lines
in recursive descent and awkward in a context-free grammar.

Anything outside the subset is recorded as a diagnostic or listed in a rule's
`unsupported` field. It is never guessed, and such rules always go to review.

## Source handling
| Feature | Support |
|---|---|
| Free format (`*>` comments, `>>` directives) | Yes |
| Fixed format (sequence area, column-7 `*` `/` `D` `-`, area A/B to column 72) | Yes, detected automatically |
| `COPY name [OF/IN lib] [REPLACING ==x== BY ==y== / word BY word]`, nested | Yes, every line keeps its original file and line number |
| Partial-word pseudo-text (`==:PFX:==`) | Yes |

## DATA DIVISION
Level numbers 01-49, 66, 77 and 88, FILLER, PIC/PICTURE (numeric, alphanumeric,
alphabetic, edited), USAGE (DISPLAY, COMP-x, BINARY, PACKED-DECIMAL, INDEX),
VALUE / VALUES with THRU and figurative constants, REDEFINES, OCCURS. FILE,
WORKING-STORAGE, LOCAL-STORAGE and LINKAGE sections; FD entries are skipped.
Each item records its comment (inline, or the comment lines directly above it).
`PROCEDURE DIVISION USING` parameters are recorded (subprograms).

Byte layout (src/verify/storage.py): offsets from the PICTUREs and USAGEs, REDEFINES
sharing the redefined item's bytes, OCCURS expanded into `NAME(1)`..`NAME(n)`
(one level), zoned decimal with GnuCOBOL's trailing overpunch for signed fields.

## PROCEDURE DIVISION
| Construct | Result |
|---|---|
| IF / ELSE / END-IF, nested, period-terminated, NEXT SENTENCE | Decision rules |
| EVALUATE TRUE, EVALUATE subject, ALSO, ANY, THRU, NOT, stacked WHEN, WHEN OTHER | Decision rules |
| Relations: symbols and words (`GREATER THAN OR EQUAL TO`, `NOT <`), `IS` | Canonical operators |
| Abbreviated conditions (`A > 5 AND < 10`, `A = 1 OR 2`), COBOL precedence | Expanded |
| 88-level condition names (single values, lists, THRU ranges) | Expanded to field comparisons |
| Class (`NUMERIC`, `ALPHABETIC`) and sign (`POSITIVE`, `NEGATIVE`, `ZERO`) conditions | `is_numeric` etc. |
| Arithmetic inside conditions (`IF AMT + FEE > 5000`) | Kept as an expression, evaluated by the checks |
| MOVE, SET cond-name TO TRUE, SET x TO v / UP BY / DOWN BY | `set` / `compute` actions |
| COMPUTE, ADD, SUBTRACT, MULTIPLY, DIVIDE (incl. GIVING) | `compute` actions |
| PERFORM paragraph / SECTION [THRU], UNTIL, VARYING, TIMES, TEST AFTER | `perform` action; control flow followed |
| GO TO paragraph | `goto` action; control flow followed (inside and out of PERFORM ranges) |
| STOP RUN, GOBACK, EXIT PROGRAM | `stop` action |
| SEARCH / SEARCH ALL over a VALUE-initialised table | One rule per table row plus AT END (first match wins) |
| CALL literal / CALL data-item (with ON EXCEPTION) | `call` action, `external_dependency`; target resolved from the item's VALUE for the call graph |
| EXEC SQL / CICS | `call` action with the statement text, flagged |
| AT END / INVALID KEY / ON SIZE ERROR handlers | Parsed (not decision rules) |
| Inline PERFORM loops inside a branch | Walked; flagged `inline PERFORM loop` |
| GO TO DEPENDING ON, ALTER, SEARCH over a table filled at run time | Flagged, never guessed |

## Control and data flow (src/analysis/cfg.py)
Every path from the program entry is walked through PERFORM (THRU, SECTION, loops),
GO TO and fall-through. Each rule records:

* `context`: the conditions under which callers reach it (including GO TO exits taken earlier);
* `derivations`: how computed fields it tests were assigned, in terms of earlier fields and inputs;
* `performed_from`: the paragraphs that PERFORM or GO TO its paragraph;
* `reachable`: false for dead code.

The walk is bounded (a visit budget that grows with the program); when a program
exceeds it, contexts may be incomplete and every rule is treated as reachable.

## Slicing conventions (shared by the slicer and the generator's gold rules)
1. A **top-level IF whose branches contain no further decisions** is one rule:
   THEN actions in `actions`, ELSE actions in `else_actions`. Its trace covers the
   whole IF statement, from `IF` to `END-IF`.
2. Otherwise the decision is **flattened into paths**. Each branch with actions is
   a rule whose condition is `all` of the path. An ELSE branch adds `not` of the IF
   condition. Its trace runs from the branch keyword (`IF`, `ELSE`, `WHEN`) to the
   branch's last statement.
3. **First-match semantics.** In an EVALUATE or an ELSE-IF chain, branch *i* adds
   `not` of every earlier branch condition. `WHEN OTHER` is `all` of those `not`s.
4. **Each part of a path is evaluated at its own decision.** In
   `ELSE PERFORM VALIDATE  IF TRAN-OK = 'Y' ...` the nested test sees TRAN-OK after
   VALIDATE ran. The verification interpreter follows this; the rule's condition is
   a static description of the path.
5. Paths are flattened: an `all` inside an `all` is merged. A single relation is
   wrapped as `{"all": [leaf]}`.
6. An IF whose branches only do I/O (DISPLAY, WRITE, ...) produces no rule.
7. Rule IDs are `<PROGRAM-ID>-R<nnn>` in source order. They stay stable while the
   code is unchanged.
8. A SEARCH over a static table gives one rule per row, row by row and WHEN by
   WHEN, each excluding the rows before it; AT END is the rule for "no row matched".

## Measured coverage
- 354 generated programs (9 domains, 27 templates): all parse with no diagnostics,
  and rule F1 against gold is 1.000 on every split (`scripts/run_corpus.py`).
- IBM Z Open Editor samples (fixed format, copybooks, file I/O, a called subprogram):
  SAM1, SAM2 and SAM1LIB parse with no diagnostics. Against hand-drafted expected
  rules (unverified by a COBOL expert) the parser matches 45 of 45
  (`scripts/eval_public.py`); SAM2's rules agree with the compiled subprogram byte
  for byte on every generated input (`tests/test_subprogram.py`).
- Generated programs up to 51,571 lines (14,621 rules) analyse in linear time,
  about 2,300 rules per second (`scripts/perf.py`, `results/perf.json`).
