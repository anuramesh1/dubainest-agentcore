# Infrastructure — DubaiNest AgentCore

AWS CDK (TypeScript). See [`../docs/00-spec.md`](../docs/00-spec.md) §4 and §11.

| Stack | Resources | Build step |
| --- | --- | --- |
| `DubaiNestAgentCoreFrontendStack` | S3 site bucket, CloudFront; `/api/*` origin is a placeholder until step 5 | 1 (trimmed), 5 |
| `DubaiNestAgentCoreObservabilityStack` | SNS topic, Leads throttle alarm, dashboard, $10/month budget | 1 (trimmed), 7 |
| `DubaiNestAgentCoreApiStack` | api Lambda + Function URL | 5 |
| `DubaiNestAgentCoreAgentStack` | AgentCore Runtime, ECR, IAM | 6 |

Shared with `dubainest-ai` and referenced by name only, never created here: the DynamoDB tables (`dubainest-{properties,leads,conversations}-dev`) and the assets bucket `dubainest-assets-<account>-us-east-1`.

```bash
npm ci
npx tsc --noEmit
npx cdk synth
npx cdk deploy DubaiNestAgentCoreObservabilityStack -c alertEmail=<you>   # budget + alarm emails
```
