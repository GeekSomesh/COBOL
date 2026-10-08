# Windows: run from Git Bash. Linux/macOS: set PY=.venv/bin/python, PYT=.venv-train/bin/python.
PY  ?= .venv/Scripts/python
PYT ?= .venv-train/Scripts/python

.PHONY: setup test corpus baseline finetune-data finetune serve-model eval demo serve

setup:            ## runtime environment (GnuCOBOL: apt install gnucobol, or WSL Ubuntu)
	py -3.11 -m venv .venv && $(PY) -m pip install -r requirements.txt

test:             ## full test suite (GnuCOBOL tests skip when cobc is missing)
	$(PY) -m pytest

corpus:           ## 300 synthetic programs + gold rules + splits, compiled with GnuCOBOL
	$(PY) scripts/generate.py --count 300

baseline:         ## AST-only baseline against gold, all splits
	$(PY) scripts/run_corpus.py

finetune-data:    ## chat JSONL from the train split
	$(PY) scripts/build_finetune_data.py

finetune:         ## LoRA fine-tune (needs .venv-train with CUDA torch, see requirements-train.txt)
	$(PYT) scripts/finetune.py --epochs 2

serve-model:      ## GGUF (Q8_0) of the fine-tuned and base models, imported into Ollama
	$(PY) scripts/serve_model.py --also-base models/Qwen2.5-Coder-1.5B-Instruct

eval:             ## every system on the test split, then results/RESULTS.md
	$(PY) scripts/evaluate.py system ast_only
	$(PY) scripts/evaluate.py system llm_only --model qwen2.5-coder:7b --limit 20
	$(PY) scripts/evaluate.py system llm_slices --model qwen-coder-base:1.5b-q8_0 --fewshot 3 --label llm_slices_1.5b
	$(PY) scripts/evaluate.py system llm_slices --model qwen2.5-coder:7b --fewshot 3 --label llm_slices_7b
	$(PY) scripts/evaluate.py system full --model cobol-enrich:1.5b --consistency 2
	$(PY) scripts/evaluate.py faults
	$(PY) scripts/evaluate.py latency
	$(PY) scripts/evaluate.py table
	$(PY) scripts/evaluate.py errors
	$(PY) scripts/update_docs.py

demo:             ## load the demo programs, then start the explorer on http://localhost:8000
	$(PY) scripts/load_demo.py --reset
	$(PY) -m uvicorn src.api.main:app --port 8000

serve:
	$(PY) -m uvicorn src.api.main:app --port 8000
