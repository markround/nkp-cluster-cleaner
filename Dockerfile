FROM python:3.12-slim
LABEL maintainer="mark.dastmalchiround@nutanix.com"
LABEL description="NKP Cluster Cleaner - Delete NKP/CAPI clusters based on label criteria"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN groupadd -r nutanix && useradd -r -g nutanix -s /bin/bash nutanix
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so they stay cached across source changes
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml README.md LICENSE.md ./
# Deliberately NOT an editable install: a regular install exercises the packaging
# config, so a missing __init__.py or undeclared template dir fails the build here
# rather than at runtime in a pod.
RUN pip install --no-cache-dir .

# Create directory for kubeconfig and config files
RUN mkdir -p /app/config && \
    chown -R nutanix:nutanix /app

# Switch to non-root user
USER nutanix

# Set the entrypoint to the nkp-cluster-cleaner command
ENTRYPOINT ["nkp-cluster-cleaner"]

# Default command shows help
CMD ["--help"]
