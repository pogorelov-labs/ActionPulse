# Contributing to ActionPulse

Thank you for your interest in contributing to ActionPulse! This document provides guidelines for contributing to the project.

## Development Setup

### Prerequisites

- Python 3.11+
- Git
- Docker (optional, for containerized development)

### Local Development

1. **Clone the repository:**
   ```bash
   git clone https://github.com/pogorelov-labs/ActionPulse.git
   cd ActionPulse
   ```

2. **Set up the development environment:**
   ```bash
   cd digest-core
   make setup
   ```

3. **Install development dependencies:**
   ```bash
   uv sync --dev
   ```

4. **Run tests:**
   ```bash
   make test
   ```

## Project conventions (read first)

These are load-bearing and not obvious from a generic GitHub flow:

- **Optional extras.** The encrypted store and the MCP server are optional dependencies:
  `uv sync --extra store --extra mcp`. On macOS the `store` extra needs SQLCipher first:
  `brew install sqlcipher`. A plain `uv sync` leaves `HAS_SQLCIPHER`/`HAS_MCP` false and the
  store/MCP/InboxAPI tests **skip** (green but unexercised) — install the extras before
  trusting a local `make test` on that code.
- **Secrets via ENV only — never in YAML.** Secrets live in `~/.config/actionpulse/env`
  (chmod 600), never in `configs/config.yaml`. `DIGEST_STORE_KEY` in particular is never
  written into any client/MCP config; the server self-loads it. DM bodies are redacted at
  rest (guardrail #9, fail-closed).
- **Git preflight (CLAUDE.md).** `git fetch origin --prune` before starting; branch from a
  fresh `origin/main`; never work from detached HEAD. Don't stack a PR on an unmerged
  branch — merge each to `main` before cutting the next. If a PR was opened from stale
  `main`, close and restack rather than salvage.
- **CI test lanes.** Beyond `lint`/`test`, CI runs blocking **`test-store`** and
  **`test-mcp`** lanes (they install the extras) and an offline **`eval-replay`** gate
  (`make ci` = `lint test eval-replay`). New store/MCP code must pass its lane.
- **Offline development.** EWS + the LLM gateway are corp-network only. Develop offline with
  `--dump-ingest` / `--replay-ingest` (record once inside the network, replay outside —
  ADR-012, "code outside, run inside, debug outside").

## Code Style

We use the following tools to maintain code quality:

- **ruff** - Fast Python linter and formatter
- **black** - Code formatter
- **isort** - Import sorter
- **mypy** - Static type checker (for models and interfaces)

### Terminal Output Checklist

Any PR touching user-visible terminal output is reviewed against
[`docs/development/TERMINAL_DESIGN.md`](docs/development/TERMINAL_DESIGN.md):

1. New user-visible strings are English; report-bound strings go through
   `assemble/labels.py`, never inline (post-L1).
2. Colors/glyphs only via `ui` tokens (post-T1); state is always carried by a
   glyph+word pair, never color alone.
3. Anything that can exceed ~1s shows liveness; animations are throttled and
   TTY-gated; an append-only non-TTY path exists.
4. No mouse reporting; Esc cancels the current question (not the program);
   Ctrl+C exits 130 with no traceback; cursor restored on every exit path.
5. Truncation per design §6.2 (end-ellipsis for messages/URLs, tail-preserving
   for paths); output readable at 80 columns.

### Pre-commit Hooks

Install pre-commit hooks to automatically format and lint your code:

```bash
pip install pre-commit
pre-commit install
```

### Manual Formatting

```bash
# Format code
make format

# Lint code
make lint
```

## Repository Hygiene

- **Two documentation trees — know which one owns what.** Repo-root `docs/` holds
  product/process docs (planning, operations, reference, troubleshooting, development,
  `docs/legacy/`). `digest-core/docs/` holds the engineering **source of truth**
  (`ARCHITECTURE.md`, `RUNBOOK.md`, ADRs, audits). Architecture/contract changes go in
  `digest-core/docs/`; roadmaps/process go in repo-root `docs/`. Update the existing file
  rather than creating a duplicate in the repo root.
- **History in `docs/legacy/`.** Move archival material and retrospectives into
  `docs/legacy/` (with a dated banner) to preserve context without cluttering working dirs.
- **Don't commit run artifacts.** `out/`, `.state/`, and `logs/` are gitignored; the data
  home lives under `var/` (see `actionpulse paths`). Verify before committing.
- **Check status.** Run `git status --short` before pushing to confirm no temp/duplicate
  files slipped in.

## Testing

### Running Tests

```bash
# Run all tests
make test

# Run with coverage
pytest --cov=digest_core tests/

# Run specific test file
pytest tests/test_ews_ingest.py

# Run with verbose output
pytest -v
```

### Test Categories

- **Unit tests** - Test individual components
- **Integration tests** - Test component interactions
- **Contract tests** - Test LLM Gateway integration
- **Snapshot tests** - Test output format stability
- **Privacy tests** - Test PII handling

### Writing Tests

- Use descriptive test names
- Follow AAA pattern (Arrange, Act, Assert)
- Mock external dependencies
- Test both success and failure cases
- Include edge cases and error conditions

## Pull Request Process

### Before Submitting

1. **Update documentation** if you've changed functionality
2. **Add tests** for new features or bug fixes
3. **Run the full test suite** to ensure nothing is broken
4. **Update CHANGELOG.md** with your changes

### Pull Request Guidelines

1. **Create a feature branch** from `main`
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** and commit them
   ```bash
   git add .
   git commit -m "feat: add your feature description"
   ```

3. **Push your branch** and create a PR
   ```bash
   git push origin feature/your-feature-name
   ```

4. **Ensure all checks pass** (tests, linting, security scans)

### Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes
- `style:` - Code style changes (formatting, etc.)
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

Examples:
```
feat: add Mattermost integration support
fix: resolve EWS authentication timeout issue
docs: update installation guide
test: add integration tests for LLM Gateway
```

## Security Guidelines

### PII Handling

- **Never commit** sensitive data or credentials
- **Use environment variables** for secrets
- **Follow PII handling** guidelines in code
- **Test for PII leakage** in outputs and logs

### Code Security

- **Use type hints** for better code safety
- **Validate all inputs** with Pydantic models
- **Handle errors gracefully** with proper logging
- **Follow principle of least privilege** in Docker containers

## Documentation

### Writing Documentation

- **Use clear, concise language**
- **Include code examples** where helpful
- **Update both user and developer docs**
- **Follow the existing documentation structure**

### Documentation Structure

- `docs/installation/` - Setup and installation guides
- `docs/operations/` - Deployment, automation, monitoring
- `docs/development/` - Architecture, technical details, code examples
- `docs/planning/` - Roadmaps and future plans
- `docs/reference/` - API docs, KPI, quality metrics
- `docs/troubleshooting/` - Common issues and solutions
- `digest-core/docs/` - **engineering source of truth**: `ARCHITECTURE.md`, `RUNBOOK.md`,
  ADRs, audits (architecture/contract changes go here, not in repo-root `docs/`)

## Getting Help

### Questions and Support

- **Check existing documentation** first
- **Search existing issues** on GitHub
- **Create a new issue** for bugs or feature requests
- **Use discussions** for questions and ideas

### Code Review

- **Be respectful** and constructive in reviews
- **Focus on the code**, not the person
- **Explain your reasoning** for suggestions
- **Be open to feedback** and alternative approaches

## Release Process

### Version Bumping

We use [Semantic Versioning](https://semver.org/):

- **MAJOR** - Breaking changes
- **MINOR** - New features (backward compatible)
- **PATCH** - Bug fixes (backward compatible)

### Release Checklist

1. **Update CHANGELOG.md** with new version
2. **Update version** in pyproject.toml
3. **Run full test suite** and security scans
4. **Create release tag** and GitHub release
5. **Update documentation** if needed

## License

By contributing to ActionPulse, you agree that your contributions will be licensed under the same proprietary license as the project.
