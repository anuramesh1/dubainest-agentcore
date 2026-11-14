"""DubaiNest AgentCore api: one FastAPI app for every /api/* route.

Local:  uvicorn api.app:app --port 8000 --env-file .env   (from the repo root)
AWS:    the same app inside the api Lambda, run by Lambda Web Adapter (step 5).

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
    version="0.4.0",
    description="Listings, enquiries and chat for the DubaiNest UI.",
)

# Only matters locally (UI on :4200, api on :8000). Behind CloudFront both are
# on one domain, so the browser never makes a cross-origin call.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(properties.router)
app.include_router(enquiries.router)
app.include_router(intro.router)
app.include_router(chat_relay.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "dubainest-agentcore-api"}
