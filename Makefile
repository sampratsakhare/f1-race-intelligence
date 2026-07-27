.PHONY: help install install-dev lint test check demo-data backfill load transform train dashboard live replay-dry docker-build

PYTHON ?= python
YEARS ?= 2023 2024 2025 2026
SESSION_KEY ?=

help:
	@echo "install       Install runtime dependencies"
	@echo "install-dev   Install test and lint dependencies"
	@echo "check         Run lint and tests"
	@echo "demo-data     Rebuild the bundled OpenF1 demo snapshot"
	@echo "backfill      Fetch YEARS='2023 2024 ...' from OpenF1"
	@echo "load          Load raw JSON into BigQuery"
	@echo "transform     Build staging, marts, and quality checks"
	@echo "train         Train and gate the pre-race model"
	@echo "dashboard     Run the Streamlit dashboard"
	@echo "live          Poll the latest authenticated OpenF1 session"
	@echo "replay-dry    Validate replay for SESSION_KEY without cloud writes"
	@echo "docker-build  Build dashboard and consumer images"

install:
	$(PYTHON) -m pip install -r requirements.txt

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

test:
	$(PYTHON) -m pytest \
		--cov=src \
		--cov=dashboard.data_access \
		--cov-report=term-missing \
		--cov-fail-under=70

check: lint test

demo-data:
	$(PYTHON) -m scripts.build_demo_snapshot --years 2024 2025

backfill:
	$(PYTHON) -m src.ingest.historical_backfill --years $(YEARS)

load:
	$(PYTHON) -m src.load.load_historical_to_bq

transform:
	$(PYTHON) -m src.load.run_transformations

train:
	$(PYTHON) -m src.ml.train_model

dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py

live:
	$(PYTHON) -m src.ingest.live_poller --session-key latest

replay-dry:
	@test -n "$(SESSION_KEY)" || (echo "Set SESSION_KEY=<OpenF1 session key>" && exit 2)
	$(PYTHON) -m src.stream.replay \
		--session-key $(SESSION_KEY) \
		--sink stdout \
		--max-events 100

docker-build:
	docker build -f Dockerfile.dashboard -t f1-race-dashboard:local .
	docker build -f Dockerfile.consumer -t f1-event-consumer:local .
