# Testing Guide for iRegul Proxy

This document describes how to test the iRegul Proxy Server.

## Prerequisites

```bash
pip install -r requirements.txt
```

## Basic Functionality Tests

### 1. Start the Server

```bash
python run_proxy.py
```

Expected output:
```
2026-02-18 21:00:00,000 - iregul_proxy.main - INFO - Starting iRegul Proxy Server
2026-02-18 21:00:00,000 - iregul_proxy.main - INFO - Proxy: 0.0.0.0:65001 -> cloud.iregul.com:65001
2026-02-18 21:00:00,000 - iregul_proxy.main - INFO - API: http://0.0.0.0:8080
2026-02-18 21:00:00,000 - iregul_proxy.proxy - INFO - Proxy server started on 0.0.0.0:65001
2026-02-18 21:00:00,000 - iregul_proxy.proxy - INFO - Forwarding to cloud.iregul.com:65001
2026-02-18 21:00:00,000 - iregul_proxy.api - INFO - API server started on http://0.0.0.0:8080
```

### 2. Test Health Check

```bash
curl http://localhost:8080/api/health
```

Expected response:
```json
{
    "status": "healthy",
    "proxy_running": true
}
```

### 3. Test Data Endpoint (Before Heat Pump Connection)

```bash
curl http://localhost:8080/api/data
```

Expected response (no data yet):
```json
{
    "status": "no_data",
    "message": "No data received yet from heat pump"
}
```

### 4. Test API Documentation

Open your browser to: `http://localhost:8080/`

You should see an HTML page with API documentation.

### 5. Test Proxy Port

Check if the proxy port is listening:
```bash
nc -zv localhost 65001
```

Expected output:
```
Connection to localhost 65001 port [tcp/*] succeeded!
```

## Configuration Tests

### Test Custom Configuration

Create a `.env` file:
```bash
PROXY_PORT=65002
API_PORT=8081
UPSTREAM_HOST=test.example.com
UPSTREAM_PORT=65003
```

Start the server and verify it uses the custom configuration.

## Integration Tests

### Test with Heat Pump

1. Configure your heat pump to connect to the proxy server's IP and port 65001
2. Wait for the heat pump to send data
3. Check the logs to see data being received and forwarded
4. Query the API to see the decoded data:

```bash
curl http://localhost:8080/api/data
```

Expected response (with data):
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

## Shutdown Test

Press Ctrl+C to stop the server.

Expected output:
```
2026-02-18 21:00:00,000 - iregul_proxy.main - INFO - Shutdown signal received
2026-02-18 21:00:00,000 - iregul_proxy.main - INFO - Shutting down servers...
2026-02-18 21:00:00,000 - iregul_proxy.proxy - INFO - Proxy server stopped
2026-02-18 21:00:00,000 - iregul_proxy.main - INFO - Shutdown complete
```

## Troubleshooting

### Port Already in Use

If you see an error about port being in use, check if another instance is running and stop it, or change the port in .env

### Cannot Connect to Upstream

If the proxy cannot connect to the upstream server, verify the upstream server is reachable and check your configuration.

### Decoding Errors

If messages cannot be decoded, this is normal if the message format doesn't match expected format. The raw data is still forwarded to upstream.
