.PHONY: setup test lint format run docker docker-run package bundle ci smoke diagnose env-check clean

setup:
	@echo "Installing dependencies with uv..."
	@if uv sync --native-tls; then \
		echo "✓ Dependencies installed with native TLS"; \
	else \
		echo "⚠ Native TLS failed, trying standard sync..."; \
		uv sync; \
	fi
	@echo ""
	@echo "Running interactive setup..."
	@uv run python -m digest_core.cli setup

test:
	PYTHONPATH=src uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/
	uv run black --check src/ tests/

format:
	uv run ruff check --fix src/ tests/
	uv run black src/ tests/

run:
	python -m digest_core.cli run --from-date today --sources ews --out ./out

docker:
	docker build -t digest-core:latest -f docker/Dockerfile .

docker-run:
	docker run --rm \
		-e EWS_PASSWORD=$(EWS_PASSWORD) \
		-e LLM_TOKEN=$(LLM_TOKEN) \
		-v $(PWD)/out:/data/out \
		-v $(PWD)/.state:/data/.state \
		-p 9108:9108 \
		-p 9109:9109 \
		digest-core:latest

package:
	uv build

# Air-gap carry-in bundle: locked + audited + checksummed + manifested, with an
# offline-install proof (EP-6, F12). See scripts/build_bundle.sh header.
bundle:
	bash scripts/build_bundle.sh

eval-replay:
	PYTHONPATH=src uv run python -m digest_core.cli eval-replay

ci: lint test eval-replay

smoke:
	python -m digest_core.cli run --dry-run --window calendar_day

diagnose:
	python -m digest_core.cli diagnose

env-check:
	./scripts/print_env.sh

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info
