#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ApiStack } from "../lib/api-stack";
import { FrontendStack } from "../lib/frontend-stack";
import { ObservabilityStack } from "../lib/observability-stack";

// Only stacks that bill nothing (or cents) while idle.
//
// Removed with the Fargate backend: NetworkStack (VPC + NAT), BackendStack
// (ECS + ALB), VectorStack (OpenSearch Serverless, never deployed).
//
// Not here on purpose: a DataStack. The DynamoDB tables and the assets bucket
// are shared with dubainest-ai, which still owns them (spec §13 decision 5);
// this app refers to them by name. Every stack and resource name here starts
// with DubaiNestAgentCore / dubainest-agentcore, so a deploy from this folder
// can never update the old DubaiNest* stacks.
//
// ApiStack (Lambda + Function URL) since step 5; AgentStack (AgentCore
// Runtime) arrives in step 6. Deploy with scripts/deploy.sh.

const app = new cdk.App();

// ── Configuration ─────────────────────────────────────────────────────────────

const isProd = app.node.tryGetContext("isProd") === "true";
// Pass at deploy time: -c alertEmail=you@example.com
const alertEmail: string | undefined = app.node.tryGetContext("alertEmail");
const monthlyBudgetUsd = Number(
  app.node.tryGetContext("monthlyBudgetUsd") ?? "10",
);
const envName = isProd ? "prod" : "dev";
// Shared with dubainest-ai's DataStack, which owns them.
const propertiesTableName =
  app.node.tryGetContext("propertiesTable") ??
  `dubainest-properties-${envName}`;
const leadsTableName =
  app.node.tryGetContext("leadsTable") ?? `dubainest-leads-${envName}`;
// The assets CloudFront base for photos, e.g. https://<domain>/media/photos.
// scripts/deploy.sh reads it from DubaiNestDataStack's AssetsCloudFrontDomain.
const mediaBaseUrl: string = app.node.tryGetContext("mediaBaseUrl") ?? "";

// The CDK CLI fills CDK_DEFAULT_ACCOUNT from your AWS credentials.
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
};
const stackProps: cdk.StackProps = { env };

// ── Stacks ────────────────────────────────────────────────────────────────────

const api = new ApiStack(app, "DubaiNestAgentCoreApiStack", {
  ...stackProps,
  isProd,
  propertiesTableName,
  leadsTableName,
  mediaBaseUrl,
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
