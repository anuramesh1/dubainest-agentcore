# DubaiNest AgentCore

The DubaiNest AI real-estate chatbot, rebuilt to cost almost nothing while idle. **LangGraph orchestrates Strands agents** on Amazon Bedrock AgentCore Runtime, with conversation and long-term memory in DynamoDB, behind a Lambda Function URL. It replaces the Fargate, ALB and NAT stack of [`dubainest-ai`](../dubainest-ai).

| Layer | Technology |
| --- | --- |
| Compute (API, guardrails, relay) | AWS Lambda (Function URL, response streaming) behind CloudFront |
| Orchestration: routing, state, guardrails, memory | LangGraph |
| Agents | Strands Agents (one per specialist, Claude Haiku 4.5 on Bedrock) |
| Agent hosting | Amazon Bedrock AgentCore Runtime |
| Memory | DynamoDB: LangGraph checkpointer (conversation) + store (visitor profile) |

The design is in [`docs/00-spec.md`](docs/00-spec.md). Architecture diagram, interview prep and a demo script: [`docs/interview/`](docs/interview/). This repo is built step by step following its §11.

## Build status

| Step | Delivers | Status |
| --- | --- | --- |
| 1 | Copy of `dubainest-ai`, `git init`, VPC / ALB / ECS removed | ✅ done |
| 2 | `api/`: properties, facets, enquiries, property-intro | ✅ done |
| 3 | `agent/`: LangGraph graph with Strands agents, output gate, memory (rebuilt after step 5) | ✅ done |
| 4 | Chat relay (local) + `scripts/build_index.py` | ✅ done |
| 5 | CDK: api Lambda + Function URL behind CloudFront | ✅ done |
| 6 | AgentCore Runtime (Strands agents + DynamoDB memory) | ✅ done |
| 7 | Relay wired to AgentCore, caps, budget | ✅ done |
| 8 | Destroy the old backend stacks, 7-day cost check | next |

## Layout (now)

```
dubainest-agentcore/
├── frontend/   # Angular 17 + Tailwind, copied from dubainest-ai (build warnings fixed)
├── api/        # FastAPI: listings, enquiries, property-intro, chat relay (+ tests, Dockerfile)
├── agent/      # LangGraph graph + Strands agents (specialists/), memory, AgentCore entrypoint (+ tests)
├── evals/      # Bedrock evals: routing, grounding, cost, memory (spec §10)
├── data/       # mock_properties.py (20 listings: DAMAC 7, Emaar 8, Nakheel 5), area_stats.json
├── infra/      # CDK (TypeScript): AgentStack, ApiStack, FrontendStack, ObservabilityStack
├── scripts/    # seed_properties.py, build_index.py, upload_photos.sh, deploy.sh, smoke.sh, invoke_agent.py
└── docs/       # 00-spec.md, architecture.png; legacy/ holds the dubainest-ai docs
```

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

## The agent: LangGraph orchestrates, Strands agents answer

```
START → load_memory → router ─┬→ property_agent ─┐
          (store)   (Nova Micro)├→ market_agent ───┤   Strands agents (Claude Haiku 4.5):
                              ├→ roi_agent ──────┤   tools + output gate
                              ├→ viewing_agent ──┼→ remember → END
                              ├→ general_agent ──┤     (store + checkpointer)
                              ├→ confirm_viewing ┤   no model: "yes" saves the lead
                              └→ off_topic ──────┘   no model: fixed reply
```

- **LangGraph** ([agent/graph.py](agent/graph.py)) owns the control flow: memory in and out, routing, which agent answers, the viewing confirmation and the off-topic reply.
- **router** ([agent/nodes/router.py](agent/nodes/router.py)): one Nova Micro call with structured output (`intent`, `second_intent`, `confidence`), the keyword router below 0.6 confidence or on error. A yes/no to a pending viewing goes straight to `confirm_viewing` with no model call. Search+ROI, search+market and market+ROI questions give one agent both toolsets.
- **Strands agents** ([agent/specialists/](agent/specialists/)): a fresh `strands.Agent` per question with the conversation as its messages and only its specialist's tools ([tools.py](agent/specialists/tools.py)):

  | Agent | Tools |
  | --- | --- |
  | property | `search_listings` (filters + hybrid vector/keyword ranking), `get_listing`, `compare_listings` |
  | market | `area_market`, `get_listing` |
  | roi | `calculate_roi` (deterministic yield, mortgage, cash flow, payback), `search_listings`, `get_listing` |
  | viewing | `request_viewing` (checks details against what the visitor typed; holds the request until "yes"), `get_listing` |
  | general | `catalogue_overview`, `get_listing` |

- **Output gate** ([agent/specialists/gate.py](agent/specialists/gate.py)): narration before tool calls is dropped and replaced by `status` events ("Searching listings…"); the answer streams sentence by sentence, each checked first: every AED figure must come from this turn's tool results, earlier (already checked) answers or the visitor's words. A failing sentence is never sent; the stream stops with a safe line and the agent is cancelled.
- **Memory** ([agent/memory.py](agent/memory.py), [nodes/memory_nodes.py](agent/nodes/memory_nodes.py)): conversation per chat session through LangGraph's checkpointer (`DynamoDBSaver`, 24 h), visitor profile through LangGraph's store (`DynamoDBStore`, 90 days): budget, areas, bedrooms, types, purpose, first name, listings seen. Extracted by code, never email or phone. In-process `InMemorySaver` / `InMemoryStore` when `SESSIONS_TABLE` / `MEMORY_TABLE` are unset.
- **Events** ([agent/stream.py](agent/stream.py)): `status` → `chunk`… → `suggestions` → `done`, or `error` with a safe message: unchanged for the UI.

Market figures in [data/area_stats.json](data/area_stats.json) are the notes carried over from dubainest-ai (June 2026) and are marked unverified; agents quote their date.

```bash
# in-process (Bedrock calls; listings from DynamoDB, or LISTINGS_SOURCE=seed)
.venv/bin/python -m agent.cli "2 bed apartments under 3M"
.venv/bin/python -m agent.cli --property 10 --trace "What's the expected ROI?"
.venv/bin/python -m agent.cli --chat          # a conversation; /new starts a session, same visitor

# evals against Bedrock (about 130 model calls, a few cents; leads are not written)
.venv/bin/python -m evals            # routing ≥ 90%, grounding 20/20, ≤ 4 calls on 95%, memory 5/5
.venv/bin/python -m evals memory -v
```

Last run: routing 44/44, grounding 20/20 (output gate blocked nothing), every question in 4 model calls or fewer (median 3), about 5,850 input / 340 output tokens and **$0.0066 per question**, 3.3 s average; memory 5/5 (a follow-up with no browser history, a viewing confirmed by "yes", the budget recalled in a new session).

## Tests and checks

```bash
.venv/bin/pytest -q                         # api, chat SSE contract, agent with a fake Strands agent, memory on moto DynamoDB; no AWS
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
- **Chat on AWS** (step 7): guard_in, then the caps, then `InvokeAgentRuntime` on the one runtime the Lambda may call. AgentCore's SSE bytes are passed through unchanged as they arrive (read with `read1()`), with a fresh AgentCore session per chat window (`dubainest-<session_id>`).
- **Caps** ([api/limits.py](api/limits.py)): 20 questions a minute per visitor IP and 200 a day for the whole site (about $1.30/day worst case), as atomic counters in `dubainest-agentcore-limits-<env>`. IPs are stored only as a salted hash, taken from `CloudFront-Viewer-Address` (set by CloudFront; `X-Forwarded-For` is never trusted). A capped question gets a polite reply and never wakes AgentCore. Change with `-c dailyQuestionCap=… -c perIpPerMinute=…`.
- **Budget**: the account already has a $10/month cost budget with email alerts; the optional project budget is in ObservabilityStack.
- **Idle cost:** the api image in ECR (~0.3 GB, about $0.03/month); Lambda, CloudFront and S3 sit in free tiers at this traffic.

```bash
scripts/deploy.sh            # needs Docker running (REQUIRE_APPROVAL=never skips CDK's IAM prompt when there's no terminal): cdk deploy Api + Frontend stacks, upload UI, invalidate, smoke test
scripts/deploy.sh --site     # UI only
scripts/smoke.sh             # checks the live site; its URL is in .deploy/outputs.env (gitignored)
```

## The agent on AgentCore Runtime (step 6)

- **Entrypoint** ([agent/main.py](agent/main.py)): `BedrockAgentCoreApp` with an async-generator entrypoint, so the SDK streams each event as a `data: {json}` SSE line, the same events the UI parses. AgentCore calls `POST /invocations` and `GET /ping` on port 8080.
- **Image** ([agent/Dockerfile](agent/Dockerfile)): arm64, Python 3.12, LangGraph + Strands, just `agent/` and `data/area_stats.json` (~510 MB). CDK builds it and pushes it to the CDK assets ECR repository.
- **Stack** ([infra/lib/agent-stack.ts](infra/lib/agent-stack.ts)): `AWS::BedrockAgentCore::Runtime` in PUBLIC network mode (no VPC, no NAT). Sessions end after 5 idle minutes (1 hour max). Listings come from the Properties table (one Scan per microVM); the vector index from a private S3 bucket (`INDEX_URI`).
- **Memory tables**: `dubainest-agentcore-sessions-<env>` (checkpointer, TTL `ttl`) and `dubainest-agentcore-memory-<env>` (store, TTL `expires_at`), on demand.
- **Role**: invoke Nova Micro, Claude Haiku 4.5 and Titan v2 only; Scan Properties; PutItem Leads (confirmed viewings); read the index object; read/write its two memory tables; logs and traces.
- **Measured** (warm): `status` at ~0.7 s, the tool's status at ~2 s, first answer text at ~3 s, done at ~4–5 s. The first call after idle (new microVM, imports, Scan, index load) took ~10 s to first text.
- **Read the stream unbuffered.** boto3's `StreamingBody.iter_lines()` reads 1 KB blocks and makes a short answer arrive all at once; [invoke_agent.py](scripts/invoke_agent.py) reads with `read1()`, and the step 7 relay must too.
- **Not yet wired to the site.** The api Lambda starts relaying to the runtime in step 7.

```bash
scripts/deploy.sh --agent                       # deploy, upload the index, stream one test question
.venv/bin/python scripts/invoke_agent.py "2 bed apartments under 3M"           # streams from AgentCore
.venv/bin/python scripts/invoke_agent.py --events --property 10 "Expected ROI?"   # raw events + timings

# the same container locally (port 8080)
docker buildx build --platform linux/arm64 -f agent/Dockerfile -t dubainest-agent .
.venv/bin/python -m agent.main                 # or run the entrypoint without Docker
```

## Data

```bash
# re-seed the shared Properties table from data/mock_properties.py (writes to AWS)
.venv/bin/python scripts/seed_properties.py --table dubainest-properties-dev
```
