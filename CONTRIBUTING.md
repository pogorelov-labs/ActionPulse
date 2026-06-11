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

## Code Style

We use the following tools to maintain code quality:

- **ruff** - Fast Python linter and formatter
- **black** - Code formatter
- **isort** - Import sorter
- **mypy** - Static type checker (for models and interfaces)

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

- **Документация только в `docs/`:** новые руководства, описания PR и отчёты добавляйте в соответствующие разделы каталога `docs/`. Актуализируйте существующие файлы вместо создания дубликатов в корне репозитория.
- **История в `docs/legacy/`:** архивные материалы и ретроспективы переносите в `docs/legacy/`, чтобы сохранить контекст и не захламлять рабочие директории.
- **Не коммитьте артефакты выполнения:** каталоги `out/`, `.state/` и `logs/` игнорируются Git. Перед коммитом убедитесь, что временные файлы удалены или заменены на `.gitkeep`.
- **Проверяйте статус:** запускайте `git status --short` перед отправкой изменений, чтобы убедиться в отсутствии временных или дубль-файлов.

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
