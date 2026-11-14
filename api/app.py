"""DubaiNest AgentCore api: one FastAPI app for every /api/* route.

Local:  uvicorn api.app:app --port 8000 --env-file .env   (from the repo root)
AWS:    the same app in the api Lambda image (api/Dockerfile), run by Lambda Web
        Adapter behind a Function URL that only CloudFront can call (step 5).

Routes (spec §2): properties, facets, property detail, enquiries, property-intro,
and POST /api/chat (agent in-process with AGENT_MODE=local; AgentCore from step 7).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import chat_relay, enquiries, intro, properties, settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="DubaiNest AgentCore API",
    version="0.5.0",
    description="Listings, enquiries and chat for the DubaiNest UI.",
)

# Only matters locally (UI on :4200, api on :8000). Behind CloudFront both are
# on one domain, so the browser never makes a cross-origin call.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    allow_methods=["GET", "POST"],
    # x-amz-content-sha256: the UI sends a body hash on POST, which the Lambda
    # Function URL needs behind CloudFront (frontend/.../signed-body.ts).
    allow_headers=["Content-Type", "x-amz-content-sha256"],
)

app.include_router(properties.router)
app.include_router(enquiries.router)
app.include_router(intro.router)
app.include_router(chat_relay.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "dubainest-agentcore-api"}
