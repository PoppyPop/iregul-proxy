# Copilot Instructions for iregul-proxy

## Repository Overview

**iregul-proxy** is a local proxy server for heat pumps based on the iregul system. The repository is currently in its early development stage.

**License:** GNU General Public License v3.0

## Project Information

### Current State
- This repository is in early development and does not yet have source code implemented
- The project will be a proxy server for interfacing with heat pump systems using the iregul protocol
- **Technology stack:** Python with strict type checking

### Expected Development
When code is added to this repository, it will likely include:
- A Python-based proxy server implementation
- Configuration files for the heat pump system integration
- API endpoints or communication protocols for the iregul system
- Documentation for setup and usage
- Type hints throughout the codebase for strict type checking

## Build and Development Instructions

### Python Environment Setup
- Use Python 3.11 or later for best type checking support
- Create a virtual environment: `python -m venv venv`
- Activate the environment: `source venv/bin/activate` (Linux/Mac) or `venv\Scripts\activate` (Windows)
- Install dependencies: `pip install -r requirements.txt` (when requirements.txt is added)
- Install development dependencies: `pip install -r requirements-dev.txt` (when added)

### Type Checking
- **Always use strict type checking** with mypy
- Run type checker before committing: `mypy .` or `mypy src/`
- Configure mypy in `pyproject.toml` or `mypy.ini` with strict mode enabled
- All functions must have type hints for parameters and return values
- Use `from typing import` for complex types (List, Dict, Optional, Union, etc.)
- For Python 3.10+, prefer built-in generic types (list, dict) over typing module equivalents

### Testing
- Use pytest as the testing framework
- Run tests: `pytest` or `pytest tests/`
- Run tests with coverage: `pytest --cov=src --cov-report=term-missing`
- Aim for high test coverage (>80%)
- Always run tests before committing changes
- Write tests for new functionality before or alongside implementation

### Linting and Code Quality
- **Black** for code formatting: `black .` (line length: 88 or 100)
- **Ruff** or **Flake8** for linting: `ruff check .` or `flake8 .`
- **isort** for import sorting: `isort .`
- Run all formatters and linters before committing
- Configure pre-commit hooks to automate checks (when .pre-commit-config.yaml is added)
- Follow PEP 8 style guidelines
- Use type hints consistently throughout the codebase

## Important Guidelines

### For Code Changes
- Keep changes minimal and focused on the specific issue being addressed
- Avoid making unrelated changes or "improvements" outside the scope of the task
- Ensure all changes are compatible with the GPL v3.0 license
- Test changes thoroughly before committing
- Always include type hints for new functions and methods
- Run mypy, black, and pytest before committing

### For New Features
- Ensure new code follows Python best practices and PEP 8
- Add comprehensive type hints with strict type checking
- Add appropriate error handling and logging
- Document functions and classes with docstrings (Google or NumPy style)
- Document any new APIs or interfaces
- Consider security implications, especially for proxy functionality
- Write unit tests for new functionality

### Python Best Practices
- Use descriptive variable names (snake_case for variables/functions, PascalCase for classes)
- Keep functions small and focused on a single responsibility
- Use list comprehensions and generator expressions where appropriate
- Leverage Python's context managers (with statements) for resource management
- Use dataclasses or Pydantic models for data structures
- Prefer composition over inheritance
- Use async/await for I/O-bound operations (if applicable)
- Handle exceptions explicitly and avoid bare except clauses
- Use logging instead of print statements

### Type Hints Best Practices
- Always provide type hints for function parameters and return values
- Use Optional[T] for values that can be None
- Use Union types sparingly; consider refactoring if unions become complex
- Use Protocol for duck typing and interface definitions
- Use TypedDict for dictionary structures with known keys
- Consider using NewType for semantic type distinctions
- Run mypy in strict mode to catch type issues early

### For Dependencies
- Always check for security vulnerabilities before adding new dependencies
- Prefer well-maintained, popular libraries with active communities
- Document why each dependency is needed in requirements.txt comments
- Pin dependency versions in requirements.txt
- Keep dependencies up to date with dependabot or similar tools
- Use requirements-dev.txt for development-only dependencies

## Project Structure

Currently, the repository contains:
- `README.md` - Project description
- `LICENSE` - GPL v3.0 license file
- `.github/copilot-instructions.md` - This file

### Expected Python Project Structure
As the project develops, follow this structure:
```
iregul-proxy/
├── src/                    # Source code
│   └── iregul_proxy/       # Main package
│       ├── __init__.py
│       ├── main.py         # Entry point
│       ├── proxy.py        # Proxy server logic
│       └── config.py       # Configuration handling
├── tests/                  # Test files
│   ├── __init__.py
│   ├── test_proxy.py
│   └── test_config.py
├── docs/                   # Documentation
├── .github/                # GitHub configurations
├── pyproject.toml          # Project metadata and tool configurations
├── requirements.txt        # Production dependencies
├── requirements-dev.txt    # Development dependencies
├── mypy.ini or pyproject.toml  # Type checking configuration
├── .pre-commit-config.yaml # Pre-commit hooks
└── README.md
```

## Security Considerations

Since this is a proxy server that will interface with physical devices (heat pumps):
- Always validate and sanitize inputs from external sources
- Implement proper authentication and authorization
- Secure communication channels (use HTTPS/TLS where appropriate)
- Log security-relevant events
- Follow security best practices for the chosen technology stack
- Be cautious with device control commands to prevent damage to physical equipment

## CI/CD

No continuous integration is set up yet. When adding CI/CD, include these checks:
- Run pytest with coverage reporting
- Run mypy for type checking
- Run black --check to verify formatting
- Run ruff or flake8 for linting
- Run isort --check-only for import sorting
- Run security scanning on dependencies (e.g., pip-audit, safety)
- Ensure all checks pass before allowing merges
- Consider adding automated deployment for releases

## Additional Notes

- This is a new project - be prepared to establish conventions and patterns
- Document decisions and patterns as they are established
- The README should be updated as major features are added
- Consider adding CONTRIBUTING.md when the project structure is more established
