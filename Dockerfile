FROM python:3.11-slim

# Prevent Python from writing .pyc files to disk
ENV PYTHONDONTWRITEBYTECODE=1

# Prevent Python from buffering stdout and stderr (helps with Docker logging)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install basic system packages needed for compiling some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies list first for caching
COPY requirements.txt requirements-dev.txt pyproject.toml ./

# Install project and development dependencies
RUN pip install --no-cache-dir -r requirements-dev.txt

# Copy the rest of the workspace code into /app
COPY . .

# Default command when running the container
CMD ["python", "run.py"]
