.PHONY: install run test lint

install:
	pip install -r requirements.txt

run:
	python -m src.cli run $(ARGS)

test:
	python -m pytest -q

lint:
	python -m compileall src
