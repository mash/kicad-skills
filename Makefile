.PHONY: test coverage clean-test fixtures

test:
	pytest tests/

coverage:
	rm -f .coverage .coverage.*
	COVERAGE_PROCESS_START=$(CURDIR)/.coveragerc \
	  PYTHONPATH=$(CURDIR)/tests/_covbootstrap:$$PYTHONPATH \
	  coverage run -m pytest tests/
	coverage combine
	coverage report -m
	coverage html

fixtures:
	python tests/fixtures/build_fixtures.py

clean-test:
	rm -rf .pytest_cache .coverage .coverage.* htmlcov
