# Architecture Decisions

A running log of key technical decisions and their rationale.
Updated continuously; finalised version produced in **Phase 8**.

> Format: Architecture Decision Records (ADR-style), most recent first.

---

## ADR-001 · Monorepo structure
**Date:** 2026-05-10 · **Phase:** 1 · **Status:** Accepted

**Context.** Project spans frontend, backend, agent code, and IaC. Could split into multiple repos or one.

**Decision.** Single monorepo with top-level folders: `frontend/`, `backend/`, `agents/`, `infra/`, `docs/`, `scripts/`.

**Rationale.**
- Single source of truth for the entire system.
- One CI pipeline; coordinated PRs across layers.
- Easier for a portfolio reviewer to navigate.
- `agents/` mounted into the backend container during dev — shared package without publishing to a registry.

**Trade-offs.** Larger clone; CI must filter by changed paths to stay fast.

---

## ADR-002 · Frontend framework — Angular 17
**Date:** 2026-05-10 · **Phase:** 1 · **Status:** Accepted

**Context.** Author has 15 years of Java/Spring experience. Frontend framework should reduce learning friction.

**Decision.** Angular 17 with standalone components and TypeScript-first patterns.

**Rationale.** DI, services, and modules map directly to Spring concepts. Standalone components remove NgModule boilerplate. TypeScript matches the project-wide type-safety theme.

**Trade-offs.** Larger bundle than React/Svelte; less ubiquitous in AI-tooling examples (most demos use Next.js).

---

## ADR-003 · Backend language — Python / FastAPI
**Date:** 2026-05-10 · **Phase:** 1 · **Status:** Accepted

**Context.** AI/ML ecosystem is overwhelmingly Python-native (LangGraph, LangChain, boto3, ChromaDB).

**Decision.** FastAPI on Python 3.11.

**Rationale.** Pydantic v2 gives Java-like typing. Async-first. Automatic OpenAPI docs. Mangum adapter wraps it for Lambda with zero code changes.

**Trade-offs.** Different runtime from frontend; deploys via container image to Lambda.

---

## ADR-004 · IaC tool — AWS CDK (TypeScript)
**Date:** 2026-05-10 · **Phase:** 1 · **Status:** Accepted

**Context.** Needed pick between CDK (TS or Python) and Terraform.

**Decision.** AWS CDK in TypeScript.

**Rationale.** Type-safe IaC, same language as the frontend, AWS-recommended for greenfield. Reinforces the project-wide type-safety story.

**Trade-offs.** CDK is AWS-locked (Terraform would be portable). Acceptable: this project is AWS-native by design.

---

## ADR-005 · Vector DB strategy — ChromaDB → OpenSearch
**Date:** 2026-05-10 · **Phase:** 1 · **Status:** Accepted

**Context.** Need vector store for RAG. ChromaDB is free and in-process; OpenSearch Serverless is the AWS-native production choice.

**Decision.** Abstract behind a `VectorStore` interface. Use ChromaDB locally, OpenSearch Serverless in AWS. Swap via env var at startup.

**Rationale.** Zero cost during development; smooth migration to production. The abstraction is a clean "show your architecture thinking" story for interviews.

**Trade-offs.** Slight extra code to maintain two backends; behaviour differences (e.g. metadata filtering) must be tested.

---

<!-- Add new ADRs above this line as decisions are made. -->
