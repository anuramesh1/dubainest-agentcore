#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { AgentStack } from "../lib/agent-stack";
import { ApiStack } from "../lib/api-stack";
import { FrontendStack } from "../lib/frontend-stack";
import { ObservabilityStack } from "../lib/observability-stack";

// Only stacks that bill nothing (or cents) while idle.
//
// No VPC, NAT, load balancer or provisioned capacity.
//
// Not here on purpose: the Properties and Leads tables and the photos bucket.
// They are created once outside CDK (see the README) and referenced by name,
// so their data outlives any stack. Every stack and resource name here starts
// with DubaiNestAgentCore / dubainest-agentcore.
//
// Deploy with scripts/deploy.sh.

const app = new cdk.App();

// ── Configuration ─────────────────────────────────────────────────────────────

const isProd = app.node.tryGetContext("isProd") === "true";
// Pass at deploy time: -c alertEmail=you@example.com
const alertEmail: string | undefined = app.node.tryGetContext("alertEmail");
const monthlyBudgetUsd = Number(
  app.node.tryGetContext("monthlyBudgetUsd") ?? "10",
);
const envName = isProd ? "prod" : "dev";
// Created outside CDK (see the README); override with -c propertiesTable=… -c leadsTable=…
const propertiesTableName =
  app.node.tryGetContext("propertiesTable") ??
  `dubainest-properties-${envName}`;
const leadsTableName =
  app.node.tryGetContext("leadsTable") ?? `dubainest-leads-${envName}`;
// The assets CloudFront base for photos, e.g. https://<domain>/media/photos.
// scripts/deploy.sh passes MEDIA_BASE_URL (empty = no photos).
const mediaBaseUrl: string = app.node.tryGetContext("mediaBaseUrl") ?? "";

// The CDK CLI fills CDK_DEFAULT_ACCOUNT from your AWS credentials.
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
};
const stackProps: cdk.StackProps = { env };

// ── Stacks ────────────────────────────────────────────────────────────────────

const agent = new AgentStack(app, "DubaiNestAgentCoreAgentStack", {
  ...stackProps,
  isProd,
  propertiesTableName,
  leadsTableName,
  description:
    "DubaiNest AgentCore — LangGraph + Strands agents on AgentCore Runtime, DynamoDB memory",
});

const api = new ApiStack(app, "DubaiNestAgentCoreApiStack", {
  ...stackProps,
  isProd,
  propertiesTableName,
  leadsTableName,
  mediaBaseUrl,
  agentRuntimeArn: agent.runtime.attrAgentRuntimeArn,
  dailyQuestionCap: Number(app.node.tryGetContext("dailyQuestionCap") ?? "200"),
  perIpPerMinute: Number(app.node.tryGetContext("perIpPerMinute") ?? "20"),
  description: "DubaiNest AgentCore — api Lambda (Function URL, streaming)",
});

new FrontendStack(app, "DubaiNestAgentCoreFrontendStack", {
  ...stackProps,
  isProd,
  apiFunctionUrl: api.functionUrl,
  description: "DubaiNest AgentCore — Angular SPA (S3 + CloudFront)",
});

new ObservabilityStack(app, "DubaiNestAgentCoreObservabilityStack", {
  ...stackProps,
  isProd,
  alertEmail,
  monthlyBudgetUsd,
  description: "DubaiNest AgentCore — dashboard, alarms, SNS, AWS Budget",
});

// ── Tags ──────────────────────────────────────────────────────────────────────

cdk.Tags.of(app).add("Project", "DubaiNest-AgentCore");
cdk.Tags.of(app).add("Environment", isProd ? "production" : "development");
cdk.Tags.of(app).add("ManagedBy", "CDK");

app.synth();
