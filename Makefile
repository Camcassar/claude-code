.PHONY: install run stop kill

install:
	pip install -e ".[dev]" 2>/dev/null || pip install ccxt pandas numpy aiohttp python-dotenv pyyaml structlog

run:
	python main.py config/live.yaml

stop:
	touch logs/.kill_switch

kill:
	rm -f logs/.kill_switch
