.PHONY: setup check

VENV := $(CURDIR)/.venv
PY := $(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]" -e "benchmark[dev]" -e "leaderboard-client[dev]"

check:
	$(PY) -m pytest tests/ -x -q -m "not live" --cov=pm_trader --cov-report=term-missing --cov-fail-under=100
	cd benchmark && $(PY) -m pytest tests/ -x -q -m "not live" --cov --cov-report=term-missing --cov-fail-under=100
	cd leaderboard-client && $(PY) -m pytest tests/ -x -q -m "not live" --cov --cov-report=term-missing --cov-fail-under=100
