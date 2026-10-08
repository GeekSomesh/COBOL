# Parser subset and slicing conventions

The parser is a hand-written recursive-descent parser for a documented subset of
Enterprise/GnuCOBOL syntax (implementation.md 2.1, "subset parser" route). It
replaced the planned Lark grammar because COBOL's separator period closes every
open IF/EVALUATE at once and ELSE binds to the nearest IF. Both are a few lines
in recursive descent and awkward in a context-free grammar.

## Source handling
| Feature | Support |
|---|---|
| Free format (`*>` comments, `>>` directives) | Yes |
| Fixed format (sequence area, column-7 `*` `/` `D` `-`, area A/B to column 72) | Yes, detected automatically |
| `COPY name [OF lib] [REPLACING ==x== BY ==y== / word BY word]`, nested | Yes, every line keeps its original file and line number |
| Partial-word pseudo-text (`==:PFX:==`) | Yes |

## DATA DIVISION
Level numbers 01-49, 66, 77 and 88, FILLER, PIC/PICTURE (numeric, alphanumeric,
alphabetic, edited), USAGE (DISPLAY, COMP-x, BINARY, PACKED-DECIMAL, INDEX),
VALUE / VALUES with THRU and figurative constants, REDEFINES, OCCURS. FILE,
WORKING-STORAGE, LOCAL-STORAGE and LINKAGE sections; FD entries are skipped.
Each item records its comment (inline, or the comment lines directly above it).

## PROCEDURE DIVISION
| Construct | Result |
|---|---|
| IF / ELSE / END-IF, nested, period-terminated, NEXT SENTENCE | Decision slices |
| EVALUATE TRUE, EVALUATE subject, ALSO, ANY, THRU, NOT, stacked WHEN, WHEN OTHER | Decision slices |
| Relations: symbols and words (`GREATER THAN OR EQUAL TO`, `NOT <`), `IS` | Canonical operators |
| Abbreviated conditions (`A > 5 AND < 10`, `A = 1 OR 2`), COBOL precedence | Expanded |
| 88-level condition names (single values, lists, THRU ranges) | Expanded to field comparisons |
| Class (`NUMERIC`, `ALPHABETIC`) and sign (`POSITIVE`, `NEGATIVE`, `ZERO`) conditions | `is_numeric` etc. |
| MOVE, SET cond-name TO TRUE, SET x TO v | `set` actions |
| COMPUTE, ADD, SUBTRACT, MULTIPLY, DIVIDE (incl. GIVING) | `compute` actions |
| PERFORM paragraph [THRU] | `perform` action |
| CALL (with ON EXCEPTION) | `call` action, `external_dependency` |
| Inline PERFORM loops | Walked; flagged `inline PERFORM loop` |
| GO TO, EXEC SQL/CICS, SEARCH, ALTER, arithmetic inside conditions | Flagged in `unsupported`, never guessed |
| AT END / INVALID KEY / ON SIZE ERROR handlers | Parsed and skipped (not decision rules) |

Not modelled: control flow across paragraphs (no CFG yet, FR-05), so rules in a
PERFORMed paragraph do not inherit the caller's conditions. Reference
modification and subscripts stay in the source name (`CUST-MONTH(I)`).

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
4. Paths are flattened: an `all` inside an `all` is merged. A single relation is
   wrapped as `{"all": [leaf]}`.
5. An IF whose branches only do I/O (DISPLAY, WRITE, ...) produces no rule.
6. Rule IDs are `<PROGRAM-ID>-R<nnn>` in source order. They stay stable while the
   code is unchanged.

## Measured coverage
- 60 generated programs (6 domains, 18 templates): all parse with no diagnostics
  and the slice count equals the gold count.
- IBM Z Open Editor samples `SAM1.cbl` and `SAM2.cbl` (fixed format, copybooks,
  file I/O): both parse with no diagnostics, giving 26 and 19 rules. There are no
  gold rules for them yet, so those rules are unscored.
