"""FastAPI application for the MyHealth FHIR dashboard + job daemon.

Serves only the focused dashboard API (status, reauth, OAuth round-trip) and
spawns the data-pull job daemon in the background on startup.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from myhealth_fhir.api.dashboard import router
from myhealth_fhir.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan hook: initialize DBs and start the background job daemon."""
    init_db()

    if os.environ.get("JOB_DAEMON", "1") != "0":
        from myhealth_fhir.job import start_job_thread

        interval = int(os.environ.get("JOB_INTERVAL_MINUTES", "360"))
        try:
            start_job_thread(interval_minutes=interval)
            logging.getLogger("myhealth_fhir").info(
                "Job daemon started (interval=%dm)", interval
            )
        except Exception:
            logging.getLogger("myhealth_fhir").exception("Job daemon failed to start")

    yield


app = FastAPI(
    title="MyHealth FHIR Dashboard",
    description="Job-run status, record totals, and provider re-authentication API",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(router)
logging.basicConfig(level=logging.INFO)


@app.get("/")
def root() -> HTMLResponse:
    """Serve a minimal landing page enumerating the dashboard endpoints."""
    return HTMLResponse(
        content="""
<!DOCTYPE html>
<html>
<head><title>MyHealth FHIR Dashboard</title></head>
<body style="font-family:sans-serif;max-width:720px;margin:40px auto;padding:0 20px">
<h1>MyHealth FHIR Dashboard</h1>
<p>Backend API is running.</p>
<h3>Endpoints</h3>
<ul>
<li><code>GET /api/status</code> — last run, totals, reauth status per provider</li>
<li><code>GET /api/providers</code> — configured providers</li>
<li><code>GET /api/auth/{provider}/start</code> — authorize URL for a provider</li>
<li><code>POST /api/auth/{provider}/exchange</code> — exchange a pasted redirect URL</li>
</ul>
<h3>CLI Alternative</h3>
<pre><code>myhealth job                      # Run one data pull
myhealth job --daemon             # Continuous pulls
myhealth anthem auth status       # Check token state</code></pre>
</body>
</html>
""",
        status_code=200,
    )
