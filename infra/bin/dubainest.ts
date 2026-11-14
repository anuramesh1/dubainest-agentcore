#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { FrontendStack } from "../lib/frontend-stack";
import { ObservabilityStack } from "../lib/observability-stack";

// Build step 1: only the stacks that bill nothing while idle remain.
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
// Coming next: ApiStack (Lambda + Function URL) in step 5, AgentStack
// (AgentCore Runtime) in step 6.

const app = new cdk.App();

// ── Configuration ─────────────────────────────────────────────────────────────

const isProd = app.node.tryGetContext("isProd") === "true";
// Pass at deploy time: -c alertEmail=you@example.com
const alertEmail: string | undefined = app.node.tryGetContext("alertEmail");
const monthlyBudgetUsd = Number(
  app.node.tryGetContext("monthlyBudgetUsd") ?? "10",
);
// Placeholder until step 5 replaces the /api/* origin with the Function URL.
const apiOriginDomain =
  app.node.tryGetContext("apiOriginDomain") ?? "api-not-deployed.invalid";

// The CDK CLI fills CDK_DEFAULT_ACCOUNT from your AWS credentials.
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
};
const stackProps: cdk.StackProps = { env };

// ── Stacks ────────────────────────────────────────────────────────────────────

new FrontendStack(app, "DubaiNestAgentCoreFrontendStack", {
  ...stackProps,
  isProd,
  apiOriginDomain,
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
