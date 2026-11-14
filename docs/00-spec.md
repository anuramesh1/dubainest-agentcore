# DubaiNest AgentCore — Backend Spec

Nov 11, 2026 · Anu · Live version: https://claude.ai/artifact/JsPaBpwNNqjxHqi4MxLmrf

## 1. Goal

Rebuild the DubaiNest AI backend as a fully on-demand stack that costs roughly nothing while idle and a fraction of a cent per question. The new project is a clone of `dubainest-ai`. The Angular frontend and the builder photos in S3 stay as they are; everything behind `/api/*` is replaced.

- **Orchestration:** a real LangGraph `StateGraph`. The current `graph.py` routes with a hand-written if/elif, so this is also the first true LangGraph version.
- **Agent hosting:** Amazon Bedrock AgentCore Runtime (serverless microVMs, billed per second of active use).
- **Models:** Amazon Bedrock only, with a cheap model for routing and a mid-tier model for answers.
- **Cost target:** under $1/month idle, about $0.01 per question or less (§8).
- **Working name:** `dubainest-agentcore`, a new folder next to `dubainest-ai` in Agentic AI Learning.

## 2. What stays exactly the same

The Angular app is copied unchanged, so the new backend must serve the same six routes with the same shapes. `environment.prod.ts` keeps `apiUrl: ""`; CloudFront still owns `/api/*`, only its origin changes.

| Route | Used by | Response the UI expects | New home |
| --- | --- | --- | --- |
| `GET /api/properties` (+ area, developer, type, bedrooms, minPrice, maxPrice) | PropertyService | Property[] | api Lambda, DynamoDB |
| `GET /api/properties/facets` | filter dropdowns | areas, developers, types, bedrooms | api Lambda |
| `GET /api/properties/{id}` | detail page | Property | api Lambda |
| `POST /api/enquiries` | detail page form | EnquiryRead, 201 | api Lambda, Leads table |
| `GET /api/chat/property-intro/{id}` | chat opened on a property | intro + suggestions, templated, no LLM | api Lambda |
| `POST /api/chat` (prompt, property_id, history) | ChatService.streamChat | SSE lines of role, event_type, content | api Lambda, then AgentCore |

**SSE contract (must not change):** `event_type` is `status` ("Thinking..."), then `chunk` (text), then optionally `suggestions` (content is an array of up to 3 strings), then `done`; or `error` with a safe message. The current suggestions-marker parsing moves into the agent, so the Lambda only relays events.

**Also kept:**

- The assets bucket `dubainest-assets-ACCOUNT-us-east-1` and its CloudFront distribution for builder photos. The `MEDIA_BASE_URL` rewrite in `properties.py` moves into the api Lambda as is.
- The frontend bucket and distribution `d2b0qbg7jln8ob.cloudfront.net`, or a fresh copy for the new project (§13).
- DynamoDB on-demand tables for Properties, Leads and Conversations. These already cost nothing at rest.
- The 20-property seed (`mock_properties.py`, `seed_properties.py`) and the regex prompt-injection screen in `agent_service.py`.
- **One catalogue change, made in both projects:** listing id `10` was Emaar's "Act One Act Two — Downtown Dubai" (image `Act One Act Two — Downtown Dubai.jpeg`). It is now DAMAC's "DAMAC Islands — Dubailand", a 4-bedroom off-plan townhouse with handover in 2028, using the image `Damac islands.jpeg`, which is already in the assets bucket under `media/photos/`. The id stays the same, so links to `/property/10` still work. The seed is now DAMAC 7, Emaar 8, Nakheel 5. `Dubailand` is a new area, so it appears as a new option in the area filter. The change is made in `dubainest-ai/backend/app/data/mock_properties.py` and carries over when step 1 copies the seed. The deployed Properties table and the RAG index need a re-seed to pick it up.

## 3. Review of the current stack

The current backend bills every hour it is deployed, which is why `demo-up.sh` and `demo-down.sh` exist. Everything always-on goes; only per-request services remain.

| Current piece (dubainest-ai) | Bills while idle? | New design |
| --- | --- | --- |
| VPC + NAT gateway (1 dev, 2 prod) | Yes, hourly + per GB | Removed. Lambda and AgentCore run outside a VPC. |
| Application Load Balancer | Yes, hourly | Removed. CloudFront calls a Lambda Function URL. |
| ECS Fargate service (0.5–1 vCPU, 1–2 tasks) | Yes, per task-hour | Removed. AgentCore Runtime bills only active seconds. |
| FastAPI + in-process ChromaDB warm-up on start | Inside Fargate | Replaced by a pre-built vector file loaded at agent start (§7). |
| OpenSearch Serverless (vector-stack, not deployed) | Yes; the repo notes about $11/day | Stays out. |
| Bedrock Guardrail (CfnGuardrail) | No, per text unit | Optional (§10). |
| DynamoDB on-demand, S3, CloudFront | No | Kept. |
| AWS Budget, alarms | No | Kept, retuned to $10/month. |

Two code issues are fixed on the way: intent routing becomes a real LangGraph graph instead of an if/elif chain, and the hard-coded "Dubai Market Context (June 2026)" in the system prompt moves to a dated data file the market agent reads.

## 4. Architecture

A question wakes three things in order (CloudFront, the api Lambda, then AgentCore), and all three stop billing compute when it is answered.

![DubaiNest AgentCore architecture · request path](architecture.png)

1. The browser POSTs `/api/chat` to the same CloudFront domain as today.
2. CloudFront forwards `/api/*` to the api Lambda's Function URL (origin access control, `RESPONSE_STREAM` mode). Non-chat routes are answered there from DynamoDB.
3. For chat, the Lambda runs guard_in and the caps, then calls `InvokeAgentRuntime` with a fresh `runtimeSessionId` per chat window.
4. AgentCore runs the LangGraph graph: router, one specialist, writer. It reads listings and stats and calls Bedrock.
5. The agent streams the UI's SSE events back; the Lambda relays them unchanged to the browser.

Region: us-east-1. AgentCore runs in public network mode, so no VPC or NAT gateway is needed. Its IAM role allows only Bedrock invoke on the two models, read on the data bucket and the Properties table, and write on the Leads table.

## 5. LangGraph agent graph

One compiled `StateGraph` runs inside the AgentCore container: a cheap router picks one specialist, the specialist gathers facts with plain Python tools, and one writer node streams the answer. Most questions cost two model calls: one router call and one answer call.

**State** (`agent/state.py`, a TypedDict): `prompt`, `property_id`, `property_context`, `history` (last 6 turns from the browser), `locale`, `intent`, `facts` (tool results), `answer`, `suggestions`, `trace`.

**Nodes**

| Node | Model | What it does |
| --- | --- | --- |
| `router` | Nova Micro (or Haiku), structured output | Returns `Route{intent, confidence}`; intents are property_search, market_intel, roi, scheduler, general, off_topic. Low confidence falls back to the existing keyword router. |
| `property_search` | none | Turns the prompt into filters plus a vector lookup over the 20 listings; puts listing IDs and fields in `facts`. |
| `market_intel` | none | Reads area stats from `data/area_stats.json` (dated, with source). |
| `roi` | none | Deterministic yield, mortgage and break-even maths, ported from `roi_calculator.py`. |
| `scheduler` | none | Collects viewing details; writes to the Leads table only after the user confirms. |
| `writer` | Claude Haiku 4.5 | Streams the answer from `facts` only, then a structured suggestions list. |
| `off_topic` | none | Returns the fixed "I specialise in Dubai real estate" reply with no model call. |

**Edges:** START → router → conditional edge on `intent` → one specialist → writer → END. `off_topic` goes straight to END. A two-intent question ("2BR in Marina and its yield") may fan out to two specialists before `writer`, using LangGraph's parallel branches.

**Streaming:** the AgentCore entrypoint is an async generator over `graph.astream(..., stream_mode=["messages", "custom"])`. It yields the UI's events as is: a `status` event when routing starts, `chunk` events from the writer's tokens, `suggestions` after, then `done`.

**No checkpointer.** The browser already sends history, so each invocation is stateless and nothing is stored between questions (§7).

## 6. Strands Agents vs OpenAI Agents SDK

**Strands is the cheaper choice for this stack, but the bigger saving is not nesting a second agent framework inside LangGraph at all.** Both SDKs are free and open source (Apache-2.0 and MIT). What you pay for is model tokens and runtime seconds, and the SDK choice only changes those indirectly. [Strands notes](https://thenewstack.io/agent-framework-container-wars/) · [license and feature comparison](https://www.developersdigest.tech/agent-frameworks/compare/strands-agents-vs-openai-agents-sdk)

| | Strands Agents | OpenAI Agents SDK |
| --- | --- | --- |
| Licence fee | None | None |
| Default model path | Bedrock, through your normal IAM role | OpenAI API: a second bill, an API key in Secrets Manager, and data leaves AWS |
| Bedrock as provider | Native, including prompt caching | Through a LiteLLM-style adapter; not the main path |
| AgentCore fit | Built alongside AgentCore; Memory session manager available | Supported by AgentCore Runtime like any framework |
| Tracing | OpenTelemetry to CloudWatch | OpenAI dashboard by default |
| You already know it | Yes, payroll-ai-strands | No |

**Why "neither" may be cheapest.** Both SDKs run their own model-driven tool loop. Put one inside a LangGraph node and each question can cost 2–4 extra model calls while the inner agent decides which tool to call. In this design the specialists are deterministic Python, so LangGraph nodes calling Bedrock directly through `langchain-aws` (ChatBedrockConverse) do the job with exactly two calls.

**Recommendation:** LangGraph + `langchain-aws` for every node in v1. If you want an agent SDK in the project for learning, use Strands in one place where a free-form tool loop helps, such as a later "compare these three listings" node, and measure its extra calls in the trace. Do not use the OpenAI Agents SDK here unless you want OpenAI models; it adds a second vendor and bill and gives no cost benefit on Bedrock.

## 7. Data, RAG and memory

With 20 listings, every store can be a file or an on-demand table. Nothing needs a server running.

- **Listings:** the DynamoDB Properties table stays the source of truth for the api Lambda. The agent loads the same 20 items once per container start (one Scan) and keeps them in memory.
- **Vector search:** `scripts/build_index.py` embeds each listing's description with Titan Text Embeddings v2 at deploy time and writes `listings_index.npz` (vectors + IDs) to S3. The agent loads it at start and runs cosine similarity in NumPy. Each question costs one query embedding and no vector database. If the corpus grows past a few thousand chunks, switch to S3 Vectors as in payroll-ai-strands.
- **Market data:** `data/area_stats.json` holds per-area price per sq ft, yield range and transaction counts, each with an as-of date and source (DLD open data). This replaces the hard-coded market paragraph. Refreshing it is a manual script for now, with no scheduled job.
- **Conversation memory:** none on the server. The browser already sends the last turns in `history`, and the graph uses the last 6. AgentCore Memory is left out because it bills per event and adds nothing for a single-tab chat.
- **Leads and enquiries:** the existing DynamoDB Leads table, written by the api Lambda (enquiry form) and the scheduler node (confirmed viewings).
- **Photos:** not touched by the backend except for the MEDIA_BASE_URL rewrite; served by the assets CloudFront distribution from S3.

## 8. Cost: pay only when you ask

**Estimate: about $0.004–0.006 per question with Haiku answers, and under $0.30/month idle.** At 100 questions a month that is roughly $1 in total. Model tokens are about 90% of the per-question cost; AgentCore compute is a rounding error. Prices are us-east-1 on-demand as published in 2026; re-check them before the first deploy.

**Per question** (assumes ~600-token router prompt, ~3,000-token writer prompt of which ~2,000 is a cached system prompt, ~400 output tokens)

| Item | Rate used | Est. per question |
| --- | --- | --- |
| Router, Nova Micro | $0.035 in / $0.14 out per 1M tokens ([source](https://avahi.ai/?p=23189)) | $0.00003 |
| Writer, Claude Haiku 4.5 | $1 in / $5 out / $0.10 cache read per 1M ([source](https://llmgateway.io/models/claude-haiku-4-5/aws-bedrock)) | $0.003–0.005 |
| Query embedding, Titan v2 | ~50 tokens | under $0.00001 |
| AgentCore Runtime | $0.0895 per vCPU-hour, $0.00945 per GB-hour ([source](https://venturebeat.com/ai/aws-unveils-bedrock-agentcore-a-new-platform-for-building-enterprise-ai-agents-with-open-source-frameworks-and-tools)); Runtime v2 rates reported at $0.1276 and $0.0169 ([source](https://www.eesel.ai/blog/bedrock-managed-agents-pricing)) | $0.0001–0.0004 |
| api Lambda (streams ~10 s at 512 MB) | free tier | $0 |
| **Total** | | **~$0.004–0.006** |

With Sonnet-tier answers instead of Haiku, expect roughly 3×: about $0.015 per question.

AgentCore Runtime bills per second of actual CPU and peak memory, and I/O wait such as waiting on Bedrock is not charged for CPU ([AWS pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)). That is what makes it suit a chatbot that is idle almost all the time.

**Idle per month (approximate):** ECR image (~1 GB) about $0.10; S3 for photos and the frontend a few cents; DynamoDB storage, CloudFront and Lambda inside free tiers; CloudWatch Logs with 7-day retention a few cents. Total under $0.30.

**Kept out on purpose:** NAT gateway, ALB, Fargate, OpenSearch Serverless, Bedrock Knowledge Bases, AgentCore Memory and Gateway, provisioned concurrency, WAF and Secrets Manager. Each one alone would cost more than the whole stack.

**Guards against a surprise bill** (the chat endpoint is public):

- A daily question cap in the api Lambda, 200/day, using an atomic DynamoDB counter. Worst case is about $1/day.
- Per-IP limit, 20/min, also a DynamoDB counter with TTL. It replaces slowapi, which cannot share state across Lambda instances.
- Reserved concurrency of 3 on the api Lambda, `max_tokens` 600 on the writer, and the existing 2,000-character prompt cap.
- AWS Budget of $10/month, alerts at 50%, 80% and 100%.

## 9. Repository layout

The new folder is a copy of `dubainest-ai` with `backend/`, `agents/` and `docker-compose.yml` replaced. Docs, scripts and the frontend carry over.

```
dubainest-agentcore/
├── frontend/            # copied unchanged (Angular 17 + Tailwind)
├── api/                 # Lambda: properties, facets, enquiries, property-intro, chat relay
│   ├── app.py           # FastAPI app, run by Lambda Web Adapter (streaming)
│   ├── chat_relay.py    # guard_in, caps, InvokeAgentRuntime, SSE pass-through
│   ├── properties.py    # moved from backend/app (incl. MEDIA_BASE_URL rewrite)
│   └── Dockerfile
├── agent/               # AgentCore Runtime container (arm64)
│   ├── main.py          # BedrockAgentCoreApp entrypoint, async generator
│   ├── graph.py         # StateGraph build + compile
│   ├── state.py
│   ├── nodes/           # router, property_search, market_intel, roi, scheduler, writer
│   ├── tools/           # listings, vector_index, area_stats, leads
│   ├── prompts.py
│   └── Dockerfile
├── data/                # area_stats.json, seed properties
├── scripts/             # seed_properties, build_index, deploy, smoke
├── evals/               # routing, contract, answer-grounding cases
├── infra/               # CDK (TypeScript): data, api, agent, frontend, observability stacks
└── docs/                # this spec, ADRs, architecture diagram
```

`agent/graph.py` can also run in-process, with no AgentCore, so local dev and evals need only AWS credentials for Bedrock (`AGENT_MODE=local`).

## 10. Guardrails and evals

Cheap checks run in the Lambda before any agent starts, so blocked or off-topic input never reaches AgentCore.

**Guardrails**

- **guard_in (api Lambda, free):** the existing regex injection screen, the 2,000-character cap, the daily cap and the per-IP limit.
- **off_topic route (graph, one Nova Micro call):** fixed reply, no writer call.
- **Grounding rule (writer prompt):** prices, yields and dates may come only from `facts`. A post-check compares every AED figure in the answer against `facts` and logs mismatches to the trace.
- **Scheduler writes:** a lead is saved only after an explicit "yes" in the next turn. Nothing else writes data.
- **Bedrock Guardrail:** optional. It bills per text unit; keep it off in v1 and enable it if the demo goes public beyond your portfolio.

**Evals** (`evals/`, run locally against the in-process graph)

| Suite | Checks | Gate |
| --- | --- | --- |
| Routing | ~40 labelled prompts, intent matches | 90%+ before deploy |
| SSE contract | event order and shape match what `chatbot.component.ts` parses | must pass |
| Grounding | every AED number in the answer appears in `facts` | must pass on 20 fixed cases |
| Cost | tokens and model calls per question from the trace | 2 calls or fewer on 95% of cases |
| Answer quality (on demand) | LLM-as-judge on ~20 cases, as in payroll-ai-strands | report only |

## 11. Build plan

Eight steps; the first four run entirely on your laptop with only Bedrock calls.

| Step | Delivers | Done when |
| --- | --- | --- |
| 1 | Copy `dubainest-ai` to `dubainest-agentcore`, `git init`, remove backend/agents/infra stacks for VPC, ALB, ECS | frontend builds unchanged |
| 2 | `api/` with properties, facets, enquiries, property-intro (no chat yet) against the existing DynamoDB tables | listings, filters and detail pages work locally |
| 3 | `agent/` graph with router, specialists and writer, run in-process | routing and grounding evals pass |
| 4 | `api/chat_relay.py` streaming in local mode, plus `build_index.py` | chat works end to end in the browser on localhost |
| 5 | CDK: api Lambda + Function URL, CloudFront `/api/*` behaviour switched to it | listings live on CloudFront, no ALB |
| 6 | CDK or starter toolkit: AgentCore Runtime (public network), ECR image, IAM | `InvokeAgentRuntime` streams from the CLI |
| 7 | Wire the relay to AgentCore, caps and budget | chat live; smoke test passes |
| 8 | Destroy the old dubainest-ai backend stacks; cost check after 7 days | Cost Explorer shows no hourly charges |

## 12. Risks to verify at build time

- **Browser cannot call AgentCore directly.** InvokeAgentRuntime needs SigV4 or a JWT, so the api Lambda relay is required. Confirm the Function URL in `RESPONSE_STREAM` mode passes AgentCore's stream through without buffering; payroll-ai-strands already does this, so reuse its setup.
- **Python Lambda streaming** needs Lambda Web Adapter (or a Node.js relay). Check payroll-ai-strands for the version that works.
- **CloudFront to Function URL** with origin access control: POST bodies need the `x-amz-content-sha256` header handled; test the chat POST early.
- **Cold starts:** the first question after idle pays AgentCore microVM start plus loading the index. Measure it; if it is above ~5 s, the `status` event covers it in the UI.
- **CDK coverage for AgentCore:** confirm the CloudFormation resource for AgentCore Runtime in the current aws-cdk-lib; fall back to the AgentCore starter toolkit for that one resource.
- **Runtime v2 pricing:** two published rate sets exist (§8). Re-check before deploy; either way the amount is fractions of a cent.
- **Model access:** Haiku 4.5 and Nova Micro must be enabled in us-east-1, via inference profiles like the current `bedrockModelArn`.
- **Market data freshness:** `area_stats.json` goes stale; the writer must quote its as-of date.

## 13. Decisions needed

| # | Question | Proposed |
| --- | --- | --- |
| 1 | Agent SDK inside LangGraph | None in v1 (langchain-aws); Strands later in one node if you want it |
| 2 | Answer model | Claude Haiku 4.5; Sonnet as a config switch |
| 3 | Router model | Nova Micro; Haiku if routing evals fall below 90% |
| 4 | IaC | CDK (TypeScript), to match dubainest-ai; payroll-ai-strands used Terraform |
| 5 | Share or copy AWS resources | Share the assets bucket and DynamoDB tables; new frontend distribution so both versions can run side by side |
| 6 | Old dubainest-ai stacks | Destroy the backend, network and observability stacks after step 7 |
| 7 | Folder and repo name | `dubainest-agentcore` |

Next: once these are settled, start build step 1.
