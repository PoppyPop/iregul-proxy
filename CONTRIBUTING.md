# Contributing to iRegul Proxy

Thank you for your interest in contributing to iRegul Proxy! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.10+ (Python 3.12+ recommended)
- [uv](https://github.com/astral-sh/uv) package manager

### Initial Setup

1. Fork and clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/iregul-proxy.git
cd iregul-proxy
```

2. Install uv if not already installed:
```bash
pip install uv
```

3. Install dependencies (including dev dependencies):
```bash
uv sync --dev
```

4. Install pre-commit hooks:
```bash
uv run pre-commit install
```

## Development Workflow

### Code Quality Standards

This project enforces strict code quality standards. All contributions must pass:

1. **Type Checking** - Strict type checking with pyright
2. **Linting** - Code linting with ruff
3. **Formatting** - Code formatting with ruff
4. **Tests** - All tests must pass

### Before Committing

Run these checks before committing your changes:

```bash
# Run all checks
uv run ruff check --fix .
uv run ruff format .
uv run pyright
uv run pytest
```

Or run them all at once:
```bash
uv run ruff check --fix . && uv run ruff format . && uv run pyright && uv run pytest
```

### Type Hints

**All functions must have type hints:**

```python
def my_function(param1: str, param2: int) -> bool:
    """Function docstring."""
    return True
```

Use `from typing import` for complex types:
```python
from typing import Any

def process_data(data: dict[str, Any]) -> list[str]:
    """Process data and return list of strings."""
    return list(data.keys())
```

### Code Style

- Follow PEP 8 style guidelines
- Use descriptive variable names (snake_case)
- Keep functions focused on a single responsibility
- Add docstrings to functions and classes (Google or NumPy style)
- Maximum line length: 100 characters (handled by formatter)

### Testing

- Write tests for new functionality
- Use pytest for testing
- Aim for high test coverage (>80%)
- Tests should be in the `tests/` directory

Example test:
```python
"""Tests for my new feature."""

import pytest

def test_my_feature() -> None:
    """Test that my feature works correctly."""
    result = my_function("test")
    assert result is True
```

### Adding Dependencies

**Always use uv for package management:**

```bash
# Add a runtime dependency
uv add <package-name>

# Add a development dependency
uv add --dev <package-name>
```

**Never use pip directly** to add project dependencies.

Before adding a new dependency:
1. Check if it's really necessary
2. Ensure it's actively maintained
3. Check for security vulnerabilities
4. Prefer well-known, popular libraries

## Pull Request Process

1. Create a new branch for your feature or bugfix:
   ```bash
   git checkout -b feature/my-new-feature
   ```

2. Make your changes following the guidelines above

3. Run all checks:
   ```bash
   uv run ruff check --fix . && uv run ruff format . && uv run pyright && uv run pytest
   ```

4. Commit your changes with a descriptive message:
   ```bash
   git commit -m "Add feature: description of what you added"
   ```

5. Push to your fork:
   ```bash
   git push origin feature/my-new-feature
   ```

6. Open a Pull Request with:
   - Clear description of changes
   - Reference to any related issues
   - Screenshots for UI changes (if applicable)

### Pull Request Requirements

Your PR must:
- Pass all CI checks (linting, type checking, tests)
- Include tests for new functionality
- Update documentation if needed
- Have a clear, descriptive commit message

## Code Review

- Be responsive to feedback
- Make requested changes promptly
- Ask questions if something is unclear
- Be respectful and constructive

## Reporting Issues

When reporting issues, please include:
- Clear description of the problem
- Steps to reproduce
- Expected behavior
- Actual behavior
- Environment information (OS, Python version, etc.)
- Relevant logs or error messages

## Questions?

If you have questions about contributing, feel free to:
- Open an issue
- Ask in your pull request
- Contact the maintainers

## License

By contributing, you agree that your contributions will be licensed under the GNU General Public License v3.0.

Thank you for contributing to iRegul Proxy! 🎉
