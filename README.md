# iregul-proxy

[![CI](https://github.com/PoppyPop/iregul-proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/PoppyPop/iregul-proxy/actions/workflows/ci.yml)

Serveur proxy local pour les pompes à chaleur basé sur le système iregul

## Description

iRegul Proxy is a local proxy server for iRegul heat pumps that:
- Receives messages from heat pumps on port 65001
- Forwards messages to the configured upstream server (e.g., cloud.iregul.com)
- Decodes and stores the last received data using the `aioiregul` library
- Exposes a JSON API to access the last received heat pump data

## Features

- **Transparent Proxy**: Acts as a transparent proxy between your heat pump and the upstream server
- **Data Decoding**: Automatically decodes heat pump messages using the aioiregul library
- **JSON API**: Exposes last received data via HTTP REST API
- **Configurable**: Easy configuration via environment variables or .env file
- **Async/Await**: Built with modern Python async/await for high performance

## Installation

### Option 1: Using Docker (Recommended)

1. Clone the repository:
```bash
git clone https://github.com/PoppyPop/iregul-proxy.git
cd iregul-proxy
```

2. Start with Docker Compose:
```bash
docker-compose up -d
```

The proxy will start automatically and be available on ports 65001 (proxy) and 8080 (API).

### Option 2: Using uv (Python Package Manager)

1. Clone the repository:
```bash
git clone https://github.com/PoppyPop/iregul-proxy.git
cd iregul-proxy
```

2. Install uv if not already installed:
```bash
pip install uv
```

3. Sync dependencies:
```bash
uv sync
```

4. Configure the proxy (optional):
```bash
cp .env.example .env
# Edit .env with your settings
```

5. Run the application:
```bash
uv run python run_proxy.py
```

### Option 3: Using pip

1. Clone the repository:
```bash
git clone https://github.com/PoppyPop/iregul-proxy.git
cd iregul-proxy
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure the proxy (optional):
```bash
cp .env.example .env
# Edit .env with your settings
```

## Configuration

Configuration can be done via environment variables or a `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_HOST` | `0.0.0.0` | Host to bind the proxy server to |
| `PROXY_PORT` | `65001` | Port to bind the proxy server to |
| `UPSTREAM_HOST` | `vpn.i-regul.com` | Upstream server host to forward messages to |
| `UPSTREAM_PORT` | `65001` | Upstream server port to forward messages to |
| `API_HOST` | `0.0.0.0` | Host to bind the API server to |
| `API_PORT` | `8080` | Port to bind the API server to |

## Usage

### Starting the Server

#### With Docker Compose
```bash
docker-compose up -d
```

To view logs:
```bash
docker-compose logs -f
```

To stop:
```bash
docker-compose down
```

#### With Docker Build
```bash
docker build -t iregul-proxy .
docker run -d -p 65001:65001 -p 8080:8080 --name iregul-proxy iregul-proxy
```

#### With uv
```bash
uv run python run_proxy.py
```

#### With Python directly
```bash
python run_proxy.py
```

Or using the module:
```bash
python -m iregul_proxy.main
```

Or using the installed script (after `uv sync` or `pip install .`):
```bash
iregul-proxy
```

### Using the API

Once the server is running, you can access the following endpoints:

#### Get Last Data
```bash
curl http://localhost:8080/api/data
```

Returns the last decoded data from the heat pump in JSON format:
```json
{
  "status": "ok",
  "data": {
    "timestamp": "2026-02-18T20:00:00",
    "is_old": false,
    "count": 123,
    "groups": {
      "group1": {
        "0": {
          "field1": "value1"
        }
      }
    },
    "raw": "raw message data"
  }
}
```

#### Health Check
```bash
curl http://localhost:8080/api/health
```

Returns server health status:
```json
{
  "status": "healthy",
  "proxy_running": true
}
```

#### Documentation
Open your browser to `http://localhost:8080/` to see the API documentation.

## Architecture

The proxy consists of three main components:

1. **Proxy Server** (`iregul_proxy/proxy.py`): 
   - Listens on port 65001 for heat pump connections
   - Forwards all data to the upstream server
   - Decodes messages from the heat pump using aioiregul
   - Stores the last decoded data

2. **API Server** (`iregul_proxy/api.py`):
   - HTTP REST API on port 8080 (configurable)
   - Exposes last received data as JSON
   - Provides health check endpoint

3. **Configuration** (`iregul_proxy/config.py`):
   - Manages configuration from environment variables
   - Supports .env files via python-dotenv

## Development

### Requirements

- Python 3.14+
- uv (recommended) or pip
- Docker (optional, for containerized deployment)

### Dependencies

- aioiregul >= 0.2.5
- aiohttp >= 3.13.3
- python-dotenv >= 1.0.0

### Setting Up Development Environment

#### Using uv (Recommended)

```bash
# Install uv if not already installed
pip install uv

# Install dependencies (including dev dependencies)
uv sync --dev

# Install pre-commit hooks (optional but recommended)
uv run pre-commit install

# Run the application
uv run python run_proxy.py
```

### Development Workflow

This project enforces strict code quality standards:

#### Type Checking with Pyright

All code must pass strict type checking:

```bash
# Run type checking
uv run pyright
```

All functions must have type hints for parameters and return values.

#### Linting and Formatting with Ruff

Code must be properly formatted and follow linting rules:

```bash
# Check linting
uv run ruff check .

# Auto-fix linting issues
uv run ruff check --fix .

# Format code
uv run ruff format .

# Check formatting without modifying files
uv run ruff format --check .
```

#### Testing with Pytest

All tests must pass before committing:

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=iregul_proxy --cov-report=term-missing

# Run tests verbosely
uv run pytest -v
```

#### Pre-commit Hooks

Install pre-commit hooks to automatically check code before committing:

```bash
# Install hooks (one time)
uv run pre-commit install

# Run hooks manually on all files
uv run pre-commit run --all-files
```

The pre-commit hooks will automatically:
- Run ruff linting and formatting
- Run pyright type checking
- Check for trailing whitespace
- Validate YAML and TOML files
- Check for security issues

#### Before Committing

Always run these checks before committing:

```bash
# Quick check
uv run ruff check --fix . && uv run ruff format . && uv run pyright && uv run pytest
```

### Adding Dependencies

**Always use uv for package management:**

```bash
# Add a runtime dependency
uv add <package-name>

# Add a development dependency
uv add --dev <package-name>

# Update dependencies
uv lock --upgrade
```

Never use pip directly for managing project dependencies.

#### Using pip

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python run_proxy.py
```

### Building Docker Image

```bash
# Build the image
docker build -t iregul-proxy .

# Run the container
docker run -p 65001:65001 -p 8080:8080 iregul-proxy
```

### Project Structure

```
iregul-proxy/
├── .github/
│   ├── workflows/
│   │   └── ci.yml        # CI/CD workflow
│   ├── copilot-instructions.md  # Copilot development guidelines
│   └── dependabot.yml    # Dependabot configuration
├── iregul_proxy/
│   ├── __init__.py       # Package initialization
│   ├── config.py         # Configuration management
│   ├── proxy.py          # Proxy server implementation
│   ├── api.py            # JSON API server
│   └── main.py           # Main entry point
├── tests/
│   ├── __init__.py       # Test package initialization
│   ├── test_config.py    # Configuration tests
│   └── test_imports.py   # Import tests
├── run_proxy.py          # Executable script
├── pyproject.toml        # Project metadata, dependencies, and tool configs
├── uv.lock               # Locked dependencies (uv)
├── requirements.txt      # Python dependencies (pip fallback)
├── .pre-commit-config.yaml  # Pre-commit hooks configuration
├── .python-version       # Python version specification
├── Dockerfile            # Docker image configuration
├── docker-compose.yml    # Docker Compose configuration
├── .dockerignore         # Docker ignore file
├── .env.example          # Example configuration
├── .gitignore            # Git ignore file
├── LICENSE               # License file
├── README.md             # This file
└── TESTING.md            # Testing guide
```

## License

See LICENSE file for details.

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute to this project.

Before contributing:
- Read the contribution guidelines
- Follow the code quality standards
- Ensure all tests pass
- Add tests for new features

