# syntax=docker/dockerfile:1

FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency files and required project files
COPY pyproject.toml uv.lock LICENSE README.md ./

# Install dependencies
RUN uv sync --frozen --no-cache

# Copy application code
COPY iregul_proxy ./iregul_proxy
COPY run_proxy.py ./

# Expose ports
EXPOSE 65001 8080

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["uv", "run", "python", "run_proxy.py"]
