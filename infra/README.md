# Infrastructure — DubaiNest AgentCore

AWS CDK (TypeScript). Deploy with [`../scripts/deploy.sh`](../scripts/deploy.sh); see the main [README](../README.md).

| Stack                                             | Resources                                                                           |
| ------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `DubaiNestAgentCoreAgentStack`                    | AgentCore Runtime (agent image), IAM role, sessions and memory tables, index bucket |
| `DubaiNestAgentCoreApiStack`                      | api Lambda + Function URL (IAM, response streaming), limits table                   |
| `DubaiNestAgentCoreFrontendStack`                 | S3 site bucket, CloudFront with origin access control, SPA rewrite function         |
| `DubaiNestAgentCoreObservabilityStack` (optional) | SNS topic, Leads throttle alarm, dashboard, monthly AWS Budget                      |

Not created here, referenced by name: the Properties and Leads DynamoDB tables (`dubainest-properties-dev`, `dubainest-leads-dev`; see Prerequisites in the main README) and the photos bucket behind `MEDIA_BASE_URL`.

```bash
npm ci
npx tsc --noEmit
npx cdk synth
npx cdk deploy DubaiNestAgentCoreObservabilityStack -c alertEmail=<you>   # optional: budget + alarm emails
```

Context: `-c propertiesTable=… -c leadsTable=… -c mediaBaseUrl=… -c dailyQuestionCap=… -c perIpPerMinute=… -c monthlyBudgetUsd=… -c isProd=true`.
