# Backend service: FHIR data-pull job + dashboard API.
# Dev / live-edit mode: ./backend is volume-mounted over /app and uvicorn runs
# with --reload, so edits to src/ hot-reload without a rebuild.

FROM ghcr.io/astral-sh/uv:python3.13-trixie

WORKDIR /app

# Keep the venv OUTSIDE /app so the ./backend volume mount (over /app) doesn't
# hide it. The mounted source stays importable via PYTHONPATH + the editable
# install baked below.
ENV UV_PROJECT_ENVIRONMENT=/venv
ENV PYTHONPATH=/app/src

# Bake deps into the image. Needs pyproject + lock + a src tree to resolve the
# editable project; the volume mount replaces /app/src with live host source at
# runtime.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Run uvicorn from the baked venv; --reload watches the mounted source only.
CMD ["/venv/bin/uvicorn", "myhealth_fhir.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]
