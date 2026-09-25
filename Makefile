.PHONY: setup check outsider

VENV := $(CURDIR)/.venv
PY := $(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]" -e "benchmark[dev]" -e "leaderboard-client[dev]" -e "leaderboard-server[dev]"

check:
	$(PY) -m pytest tests/ -x -q -m "not live" --cov=pm_trader --cov-report=term-missing --cov-fail-under=100
	cd benchmark && $(PY) -m pytest tests/ -x -q -m "not live" --cov --cov-report=term-missing --cov-fail-under=100
	cd leaderboard-client && $(PY) -m pytest tests/ -x -q -m "not live" --cov --cov-report=term-missing --cov-fail-under=100
	cd leaderboard-server && $(PY) -m pytest tests/ -x -q -m "not live" --cov --cov-report=term-missing --cov-fail-under=100

# Build the wheel, install it into a throwaway venv, and use it from outside the
# checkout as a user would (same gate as CI's "Outsider install" job).
outsider:
	rm -rf $(CURDIR)/.outsider && mkdir -p $(CURDIR)/.outsider/work
	python3 -m venv $(CURDIR)/.outsider/user
	$(CURDIR)/.outsider/user/bin/pip wheel --no-deps -w $(CURDIR)/.outsider/dist .
	$(CURDIR)/.outsider/user/bin/pip install $(CURDIR)/.outsider/dist/*.whl
	cd $(CURDIR)/.outsider/work && PATH=$(CURDIR)/.outsider/user/bin:$$PATH \
		$(CURDIR)/.outsider/user/bin/python $(CURDIR)/scripts/outsider_smoke.py
	rm -rf $(CURDIR)/.outsider
