# COBOL-to-Decision Pipeline: Legacy Code Intent Extractor

> IBM Z Datathon | Domain: Real-Time AI for Critical Decisions

An LLM-powered decision-rule extraction engine. It reads COBOL copybooks and PROCEDURE DIVISION code, surfaces the business logic buried inside, and turns it into structured, explainable, source-traceable decision rules. The rules power a REST API for real-time scoring and an interactive explorer for auditors and developers.

**Core idea:** COBOL source in, decision model out. The pipeline never touches customer transaction data.

## The problem in brief
- Business rules are buried in IF/EVALUATE chains thousands of lines deep, invisible to auditors, risk teams and modern developers.
- COBOL expertise is scarce, and documentation is often outdated or missing.
- Auditing or changing one rule means manual code navigation.
- Modern AI/ML systems cannot reuse rules that exist only as procedural code.

## The solution in brief
1. **Parse** COBOL with an ANTLR4 grammar into an AST, data dictionary and control-flow graph.
2. **Slice** out each decision point and the statements it controls.
3. **Extract** intent and a structured rule from each slice with a COBOL-tuned LLM (LLaMA/DeepSeek).
4. **Verify** each rule against the AST and by differential testing against compiled COBOL.
5. **Publish** rules as JSON, decision trees and PMML, with confidence scores and line-level traceability, through a REST API and a decision explorer.

## Documentation index
| File | Purpose |
|---|---|
| [architecture.md](architecture.md) | Components, data flow, deployment on IBM Z, design decisions |
| [requirement.md](requirement.md) | Functional and non-functional requirements, user stories, scope |
| [rules.md](rules.md) | Canonical rule schema, COBOL-to-rule mapping, normalization, confidence scoring |
| [security.md](security.md) | Security model, threat model, controls |
| [dataset.md](dataset.md) | Datasets, synthetic generator design, gold standard |
| [api.md](api.md) | REST API specification |
| [evaluation.md](evaluation.md) | Metrics, experiments, baselines |
| [roadmap.md](roadmap.md) | Phases, milestones, risks |
| [demo.md](demo.md) | Demo script and likely judge questions |
| [implementation.md](implementation.md) | Phase-by-phase build plan |
| [STATUS.md](STATUS.md) | Requirements status as built, with evidence |
| [parser_subset.md](parser_subset.md) | Supported COBOL subset and slicing conventions |
| [limitations.md](limitations.md) | Known limitations, measured |

## Planned repository layout
```
cobol-to-decision/
  grammar/            ANTLR4 COBOL grammar and generated parser
  src/
    ingest/           source loader, COPY resolver
    analysis/         AST, data dictionary, CFG, decision slicer
    llm/              prompts, fine-tuning scripts, inference wrapper
    rules/            schema, normalizer, verifier, exporters (JSON, PMML)
    api/              REST API (OpenAPI)
    ui/               decision explorer
  data/
    synthetic/        generated COBOL corpus
    gold/             gold-standard rules
  tests/
  docs/               these documents
```

## Tech stack
Python, ANTLR4, LLaMA/DeepSeek (fine-tuned, served locally), GnuCOBOL (differential testing), SHAP, FastAPI (REST), Faker (synthetic data), IBM Z Open Platform / LinuxONE for deployment.

## Status
Datathon prototype, built end to end (see [STATUS.md](STATUS.md)). Targets in the planning documents are goals; measured results are in [evaluation.md](evaluation.md) section 8 and `results/RESULTS.md`.
