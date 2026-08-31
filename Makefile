# FORSETI reproducible pipeline.
# Every target executes real code; nothing reports success on code existence.

SEED ?= 42
SAMPLES ?= 24000
PY ?= python

.PHONY: help install train evaluate ablation fidelity benchmark pqc-test test demo experiment all anchors backend frontend

help:
	@echo "FORSETI targets"
	@echo "  make install     install python dependencies"
	@echo "  make train       train the detector (temporal split + attack-family holdout)"
	@echo "  make evaluate    run the four-architecture baseline benchmark"
	@echo "  make ablation    run the feature-group ablation"
	@echo "  make fidelity    run statistical fidelity against public anchors"
	@echo "  make benchmark   measure inline latency over 10,000 transactions"
	@echo "  make pqc-test    prove ML-DSA-44 signing and tamper detection"
	@echo "  make test        run the full test suite"
	@echo "  make demo        run the deterministic flagship demo"
	@echo "  make all         full reproducible pipeline -> artifacts/final_report.json"
	@echo "  make backend     serve the API on :8000"
	@echo "  make frontend    serve the dashboard on :3005"

install:
	cd backend && $(PY) -m pip install -r requirements.txt

train:
	cd backend && SEED=$(SEED) $(PY) -m app.detector.train --seed $(SEED) --samples $(SAMPLES)

evaluate:
	cd backend && $(PY) -m app.detector.baselines --seed $(SEED) --samples $(SAMPLES)

ablation:
	cd backend && $(PY) -m app.detector.ablation --seed $(SEED) --samples $(SAMPLES)

fidelity:
	cd backend && $(PY) -m app.fidelity.report

benchmark:
	cd backend && $(PY) -m app.benchmark.latency --iterations 10000 --seed $(SEED)

pqc-test:
	cd backend && $(PY) -m pytest tests/test_forseti.py -k TestPQC -v

test:
	cd backend && $(PY) -m pytest tests/ -q

demo:
	cd backend && $(PY) -m app.demo_runner --seed $(SEED)

experiment all:
	cd backend && $(PY) -m app.experiment_runner --seed $(SEED) --samples $(SAMPLES)

anchors:
	$(PY) scripts/download_anchors.py

backend:
	cd backend && $(PY) -m uvicorn app.main:app --port 8000 --host 0.0.0.0

frontend:
	cd frontend && npm run dev -- -p 3005
