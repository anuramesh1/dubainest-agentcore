# DubaiNest AgentCore

The DubaiNest AI real-estate chatbot, rebuilt to cost almost nothing while idle: a LangGraph agent graph on Amazon Bedrock AgentCore Runtime behind a Lambda Function URL, in place of the Fargate, ALB and NAT stack of [`dubainest-ai`](../dubainest-ai).

The design is in [`docs/00-spec.md`](docs/00-spec.md). This repo is built step by step following its §11.

## Build status

| Step | Delivers | Status |
| --- | --- | --- |
| 1 | Copy of `dubainest-ai`, `git init`, VPC / ALB / ECS removed | ✅ done |
| 2 | `api/`: properties, facets, enquiries, property-intro | ✅ done |
| 3 | `agent/` LangGraph graph, run in-process | next |
| 4 | Chat relay (local) + `scripts/build_index.py` | |
| 5 | CDK: api Lambda + Function URL behind CloudFront | |
| 6 | AgentCore Runtime | |
| 7 | Relay wired to AgentCore, caps, budget | |
| 8 | Destroy the old backend stacks, 7-day cost check | |

## Layout (now)

```
dubainest-agentcore/
├── frontend/   # Angular 17 + Tailwind, copied from dubainest-ai (build warnings fixed)
├── api/        # FastAPI: properties, facets, detail, enquiries, property-intro (+ tests)
├── data/       # mock_properties.py: the 20-listing seed (DAMAC 7, Emaar 8, Nakheel 5)
├── infra/      # CDK (TypeScript): FrontendStack, ObservabilityStack
├── scripts/    # seed_properties.py, upload_photos.sh
└── docs/       # 00-spec.md, architecture.png; legacy/ holds the dubainest-ai docs
```

`agent/` and `evals/` arrive in steps 3–4 (spec §9). `api/Dockerfile` comes with the Lambda in step 5.

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

# api on :8000 (what environment.ts points at)
.venv/bin/uvicorn api.app:app --port 8000 --env-file .env --reload

# UI on :4200, in a second terminal
cd frontend && npm ci && npm start
```

| Route | Source |
| --- | --- |
| `GET /api/properties` (+ area, developer, type, bedrooms, minPrice, maxPrice) | Properties table, one Scan per instance per 60 s |
| `GET /api/properties/facets` | same cache |
| `GET /api/properties/{id}` | same cache, 404 if unknown |
| `POST /api/enquiries` | Leads table (`source: enquiry_form`), 201 |
| `GET /api/chat/property-intro/{id}` | templated from the listing, no model call |
| `POST /api/chat` | step 4 |

## Tests and checks

```bash
.venv/bin/pytest -q                         # api against a fake DynamoDB (moto), no AWS
.venv/bin/ruff check api scripts data
cd frontend && npm run build
cd infra && npx cdk synth
```

## Data

```bash
# re-seed the shared Properties table from data/mock_properties.py (writes to AWS)
.venv/bin/python scripts/seed_properties.py --table dubainest-properties-dev
```
