# IBM Z Open Editor sample programs

Copied unmodified from https://github.com/IBM/zopeneditor-sample
(commit 8f9835308de6159eb54f226041d5f81a780cb8eb, Apache License 2.0, see LICENSE):

| File | Original path |
|---|---|
| SAM1.cbl, SAM2.cbl, SAM1LIB.cbl | COBOL/ |
| CUSTCOPY.cpy, TRANREC.cpy | COPYBOOK/ |
| DATETIME.cpy | COPYLIB/ |
| REPTTOTL.cpy | COPYLIB-MVS/ |
| RUN.jcl, ALLOCATE.jcl, RUNASAM1.jcl | JCL/ |

Used for robustness checks, repository analysis (call graph, JCL, datasets) and
the demo ("works on code we did not write"). Hand-drafted expected rules
(unverified by a COBOL expert) are in `data/gold_public/`;
`python scripts/eval_public.py` scores the parser against them. These programs are
not part of any train/val/test split.
