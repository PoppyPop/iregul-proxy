# iregul-proxy

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
| `UPSTREAM_HOST` | `cloud.iregul.com` | Upstream server host to forward messages to |
| `UPSTREAM_PORT` | `65001` | Upstream server port to forward messages to |
| `API_HOST` | `0.0.0.0` | Host to bind the API server to |
| `API_PORT` | `8080` | Port to bind the API server to |

## Usage

### Starting the Server

```bash
python run_proxy.py
```

Or using the module:
```bash
python -m iregul_proxy.main
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

- Python 3.8+
- aioiregul >= 0.2.5
- aiohttp >= 3.9.0
- python-dotenv >= 1.0.0

### Project Structure

```
iregul-proxy/
├── iregul_proxy/
│   ├── __init__.py       # Package initialization
│   ├── config.py         # Configuration management
│   ├── proxy.py          # Proxy server implementation
│   ├── api.py            # JSON API server
│   └── main.py           # Main entry point
├── run_proxy.py          # Executable script
├── requirements.txt      # Python dependencies
├── .env.example          # Example configuration
├── LICENSE               # License file
└── README.md             # This file
```

## License

See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
