# DubaiNest AgentCore

The DubaiNest AI real-estate chatbot, rebuilt to cost almost nothing while idle: a LangGraph agent graph on Amazon Bedrock AgentCore Runtime behind a Lambda Function URL, in place of the Fargate, ALB and NAT stack of [`dubainest-ai`](../dubainest-ai).

The design is in [`docs/00-spec.md`](docs/00-spec.md). This repo is built step by step following its §11.

## Build status

| Step | Delivers | Status |
| --- | --- | --- |
| 1 | Copy of `dubainest-ai`, `git init`, VPC / ALB / ECS removed | ✅ done |
| 2 | `api/`: properties, facets, enquiries, property-intro | ✅ done |
| 3 | `agent/` LangGraph graph, run in-process | ✅ done |
| 4 | Chat relay (local) + `scripts/build_index.py` | ✅ done |
| 5 | CDK: api Lambda + Function URL behind CloudFront | ✅ done |
| 6 | AgentCore Runtime | next |
| 7 | Relay wired to AgentCore, caps, budget | |
| 8 | Destroy the old backend stacks, 7-day cost check | |

## Layout (now)

```
dubainest-agentcore/
├── frontend/   # Angular 17 + Tailwind, copied from dubainest-ai (build warnings fixed)
├── api/        # FastAPI: listings, enquiries, property-intro, chat relay (+ tests, Dockerfile)
├── agent/      # LangGraph StateGraph: router → specialist(s) → writer (+ tests)
├── evals/      # Bedrock evals: routing, grounding, cost (spec §10)
├── data/       # mock_properties.py (20 listings: DAMAC 7, Emaar 8, Nakheel 5), area_stats.json
├── infra/      # CDK (TypeScript): ApiStack, FrontendStack, ObservabilityStack
├── scripts/    # seed_properties.py, build_index.py, upload_photos.sh, deploy.sh, smoke.sh
└── docs/       # 00-spec.md, architecture.png; legacy/ holds the dubainest-ai docs
```

`agent/main.py` and `agent/Dockerfile` come with AgentCore in step 6.

## What step 1 removed, and why

| Removed | Was | Why |
| --- | --- | --- |
| `backend/` | FastAPI on Fargate, in-process ChromaDB | Replaced by `api/` (Lambda) in step 2. Seed moved to `data/`. |
| `agents/` | if/elif router + specialists | Replaced by a real LangGraph `StateGraph` in `agent/` in step 3. |
| `docker-compose.yml` | backend + ChromaDB + dynamodb-local | No containers needed for local dev in the new design. |
| `infra/lib/network-stack.ts` | VPC + NAT gateway | Billed hourly; nothing runs in a VPC now. |
| `infra/lib/backend-stack.ts` | ECS Fargate + ALB + ECR | Billed hourly; replaced by Lambda + AgentCore. |
| `infra/lib/vector-stack.ts` | OpenSearch Serverless (never deployed) | ~$11/day idle; replaced by a NumPy index file. |
| `infra/lib/data-stack.ts` | DynamoDB tables, assets bucket | Still owned by `dubainest-ai` and shared (spec §13, decision 5). |
| `scripts/deploy.sh`, `demo-up.sh`, `demo-down.sh`, `bootstrap.sh` | Fargate deploy and start/stop | Nothing to start or stop any more. New deploy script in step 5. |

## Safety: separate from the old project

Every stack and resource this app creates is named `DubaiNestAgentCore…` or `dubainest-agentcore-…`, so `cdk deploy` here cannot change the old `DubaiNest*` stacks. The shared DynamoDB tables (`dubainest-properties-dev`, `dubainest-leads-dev`, …) and the photos bucket are only ever referenced by name.

## Run locally

Needs AWS credentials that can read the shared DynamoDB tables (and write `dubainest-leads-dev` for enquiries).

```bash
# once
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                        # then set MEDIA_BASE_URL
.venv/bin/python scripts/build_index.py     # vector index for search (20 Titan calls; rerun when listings change)

# api on :8000 (what environment.ts points at)
.venv/bin/uvicorn api.app:app --port 8000 --env-file .env --reload

# UI on :4200, in a second terminal
cd frontend && npm ci && npm start
```

Open http://localhost:4200 and use the chat: it calls `POST /api/chat`, which runs the agent in the api process (`AGENT_MODE=local`) with your AWS credentials for Bedrock.

| Route | Source |
| --- | --- |
| `GET /api/properties` (+ area, developer, type, bedrooms, minPrice, maxPrice) | Properties table, one Scan per instance per 60 s |
| `GET /api/properties/facets` | same cache |
| `GET /api/properties/{id}` | same cache, 404 if unknown |
| `POST /api/enquiries` | Leads table (`source: enquiry_form`), 201 |
| `GET /api/chat/property-intro/{id}` | templated from the listing, no model call |
| `POST /api/chat` (prompt, property_id, history) | guard_in (injection screen, 2,000-char cap), then the agent's SSE events, relayed unchanged |

## The agent (step 3)

```
START → router ─┬→ property_search ─┐
   (Nova Micro) ├→ market_intel ────┤
                ├→ roi ─────────────┼→ writer → END
                ├→ scheduler ───────┤  (Claude Haiku 4.5, streams)
                ├→ general ─────────┘
                └→ off_topic → END     (fixed reply, no model call)
```

- **router** ([agent/nodes/router.py](agent/nodes/router.py)): one Nova Micro call with structured output (`intent`, `second_intent`, `confidence`). Below 0.6 confidence, or if the call fails, the keyword router from dubainest-ai decides. A "yes" right after a viewing confirmation goes straight to the scheduler with no model call. Two intents fan out in parallel only for search + ROI, search + market or market + ROI.
- **specialists** ([agent/nodes/specialists.py](agent/nodes/specialists.py)): plain Python, no model calls. Search parses filters from the question (area, developer, type, bedrooms, price) and widens them, saying so, when nothing matches. ROI computes yield, service charges, mortgage, cash flow and payback from the listing and stated assumptions (75% LTV, 4.5% over 25 years, AED 18/sqft service charge, 4% DLD fee). The scheduler collects property, time, name and contact across turns and writes to Leads only after the user replies yes to the confirmation.
- **writer** ([agent/nodes/writer.py](agent/nodes/writer.py)): one Claude Haiku 4.5 call that sees only `facts`, streams the answer, and ends with suggestions that become the UI's `suggestions` event. A post-check flags any AED figure not in `facts` ([agent/grounding.py](agent/grounding.py)).
- **search ranking**: with filters (area, price, bedrooms…) results are filtered and sorted by price. A descriptive question with no filters ("beach and lagoons for my family") is ranked by hybrid score: Titan v2 vector similarity from `data/listings_index.npz` plus 0.3 × keyword overlap. Without the index it falls back to keywords only. Built by [scripts/build_index.py](scripts/build_index.py); one query embedding per such question.
- **events** ([agent/stream.py](agent/stream.py)): `status` → `chunk`… → `suggestions` → `done`, or `error` with a safe message, the exact shapes `chat.service.ts` parses.

Market figures in [data/area_stats.json](data/area_stats.json) are the notes carried over from dubainest-ai (June 2026) and are marked unverified; the writer quotes their date. Refreshing them from DLD open data is still to do.

```bash
# ask a question in-process (Bedrock calls; listings from DynamoDB, or LISTINGS_SOURCE=seed)
.venv/bin/python -m agent.cli "2 bed apartments under 3M"
.venv/bin/python -m agent.cli --property 10 --trace "What's the expected ROI?"

# evals against Bedrock (about 80 model calls, a few cents; leads are not written)
.venv/bin/python -m evals            # routing ≥ 90%, grounding 20/20, ≤ 2 model calls on 95%
.venv/bin/python -m evals grounding -v
```

Last run (3 consecutive): routing 40/40, grounding 20/20, every question in 2 model calls or fewer, about 2,900 input / 270 output tokens and **$0.0033 per question**, 2.5 s average.

## Tests and checks

```bash
.venv/bin/pytest -q                         # api (fake DynamoDB), chat SSE contract, agent (fake models); no AWS
.venv/bin/ruff check api agent evals scripts data
cd frontend && npm run build
cd infra && npx cdk synth
```

## Deploy (step 5)

```
browser ─▶ CloudFront ─┬─ /*      ─▶ S3 (Angular build)
                       └─ /api/*  ─▶ Lambda Function URL (AWS_IAM, RESPONSE_STREAM)
                                       └─ api/ image: FastAPI under Lambda Web Adapter
                                            └─ DynamoDB (shared tables)
```

- **api Lambda** ([infra/lib/api-stack.ts](infra/lib/api-stack.ts)): the same FastAPI app as local, packaged by [api/Dockerfile](api/Dockerfile) (arm64, Python 3.12, Lambda Web Adapter so `POST /api/chat` streams). 512 MB, 60 s timeout, reserved concurrency 3, logs kept 7 days. Its role can scan Properties and put items in Leads, nothing else.
- **Function URL is IAM-only.** CloudFront signs every `/api/*` request (origin access control), so the URL can't be called directly. Lambda needs a body hash on signed POSTs, which CloudFront can't compute, so the UI sends `x-amz-content-sha256` itself ([signed-body.ts](frontend/src/app/services/signed-body.ts)).
- **SPA routing** is a CloudFront Function on the S3 behaviour (paths without a file extension → `index.html`), not distribution-wide error pages, so the api's 403/404 reach the browser as JSON.
- **Chat on AWS** answers with a polite `error` event until step 7 wires the relay to AgentCore (the agent isn't in the api image). Local chat is unchanged.
- **Idle cost:** the api image in ECR (~0.3 GB, about $0.03/month); Lambda, CloudFront and S3 sit in free tiers at this traffic.

```bash
scripts/deploy.sh            # needs Docker running (REQUIRE_APPROVAL=never skips CDK's IAM prompt when there's no terminal): cdk deploy Api + Frontend stacks, upload UI, invalidate, smoke test
scripts/deploy.sh --site     # UI only
scripts/smoke.sh https://<distribution>.cloudfront.net   # read-only checks
```

## Data

```bash
# re-seed the shared Properties table from data/mock_properties.py (writes to AWS)
.venv/bin/python scripts/seed_properties.py --table dubainest-properties-dev
```
