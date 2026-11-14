# DubaiNest AgentCore

The DubaiNest AI real-estate chatbot, rebuilt to cost almost nothing while idle: a LangGraph agent graph on Amazon Bedrock AgentCore Runtime behind a Lambda Function URL, in place of the Fargate, ALB and NAT stack of [`dubainest-ai`](../dubainest-ai).

The design is in [`docs/00-spec.md`](docs/00-spec.md). This repo is built step by step following its §11.

## Build status

| Step | Delivers | Status |
| --- | --- | --- |
| 1 | Copy of `dubainest-ai`, `git init`, VPC / ALB / ECS removed | ✅ done |
| 2 | `api/`: properties, facets, enquiries, property-intro | next |
| 3 | `agent/` LangGraph graph, run in-process | |
| 4 | Chat relay (local) + `scripts/build_index.py` | |
| 5 | CDK: api Lambda + Function URL behind CloudFront | |
| 6 | AgentCore Runtime | |
| 7 | Relay wired to AgentCore, caps, budget | |
| 8 | Destroy the old backend stacks, 7-day cost check | |

## Layout (now)

```
dubainest-agentcore/
├── frontend/   # Angular 17 + Tailwind, copied unchanged from dubainest-ai
├── data/       # mock_properties.py: the 20-listing seed (DAMAC 7, Emaar 8, Nakheel 5)
├── infra/      # CDK (TypeScript): FrontendStack, ObservabilityStack
├── scripts/    # seed_properties.py, upload_photos.sh
└── docs/       # 00-spec.md, architecture.png; legacy/ holds the dubainest-ai docs
```

`api/`, `agent/` and `evals/` arrive in steps 2–4 (spec §9).

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

## Local commands

```bash
# frontend (unchanged)
cd frontend && npm ci && npm run build      # or: npm start → http://localhost:4200

# infra
cd infra && npm ci && npx cdk synth

# re-seed the shared Properties table from data/mock_properties.py (writes to AWS)
python3 scripts/seed_properties.py --table dubainest-properties-dev
```
