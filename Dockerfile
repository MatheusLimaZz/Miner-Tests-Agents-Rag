# ==============================================================================
# MSR-Kit: Dockerfile based on Ubuntu 24.04 LTS (Noble Numbat)
# ==============================================================================
FROM ubuntu:24.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# Install Python 3.12, pip, venv, and essential certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside container
WORKDIR /app

# In Ubuntu 24.04, PEP 668 protects system packages.
# Use an isolated virtualenv inside /opt/venv for clean, isolated dependencies.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip & build tools inside the container environment
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy project metadata first for fast Docker layer caching
COPY pyproject.toml README.md ./

# Copy source code and default protocols
COPY src/ ./src/
COPY protocols/ ./protocols/

# Install msrkit inside the container in editable mode (-e) for live volume reloading
RUN pip install --no-cache-dir -e .

# Create the data directory mountpoint
RUN mkdir -p /app/data

# Default command opens an interactive bash shell
CMD ["/bin/bash"]
