.PHONY: setup check

setup:
	pip install -e ".[dev]" -e "benchmark[dev]" -e "leaderboard-client[dev]"

check:
	python3 -m pytest tests/ -x -q -m "not live" --cov=pm_trader --cov-report=term-missing --cov-fail-under=100
	cd benchmark && python3 -m pytest tests/ -x -q -m "not live" --cov --cov-report=term-missing --cov-fail-under=100
	cd leaderboard-client && python3 -m pytest tests/ -x -q -m "not live" --cov --cov-report=term-missing --cov-fail-under=100
