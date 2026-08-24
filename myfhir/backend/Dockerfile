# Backend service for the MyHealth FHIR data-pull job + dashboard API.
# Serves Anthem (insurance) and Epic (clinical, UCLA configured) providers.
# Uses the Astral uv image with Python 3.14 on Debian Trixie.

FROM ghcr.io/astral-sh/uv:python3.13-trixie

WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install dependencies (no dev tools, no install of the project as editable)
RUN uv sync --frozen --no-dev

# Set PYTHONPATH so the src layout resolution works
ENV PYTHONPATH=/app/src

# Expose port for FastAPI server
EXPOSE 8000

# Default command: run the FastAPI server
CMD ["uv", "run", "uvicorn", "myhealth_fhir.main:app", "--host", "0.0.0.0", "--port", "8000"]
