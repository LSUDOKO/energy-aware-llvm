PY ?= ./venv/bin/python

.DEFAULT_GOAL := help
.PHONY: help venv test bench report dataset train evaluate web

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv:  ## create the virtual environment
	python3 -m venv venv && ./venv/bin/pip install -r requirements.txt

test:  ## run the pytest suite
	$(PY) -m pytest tests -q -p no:cacheprovider
	cd web-app && node --test src/lib/lifecycle.test.js

bench:  ## run all kernels x builds natively (do not run other heavy work)
	$(PY) benchmarks/run_benchmarks.py

report: ## charts + RESULTS.md from the last bench run
	$(PY) benchmarks/report_benchmarks.py

dataset: ## measure pass sequences for the -Mbalanced ranker
	$(PY) -m ml_models.collect_dataset --random-sequences 12

train:  ## train the ranker on the measured dataset
	$(PY) -m ml_models.train

evaluate: ## leave-one-benchmark-out evaluation of the ranker
	$(PY) -m ml_models.evaluate

web:    ## production build of the UI
	cd web-app && npm install && npm run build
