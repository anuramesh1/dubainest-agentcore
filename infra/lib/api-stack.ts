import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";

export interface ApiStackProps extends cdk.StackProps {
  // Created outside CDK (see the README): referenced by name only.
  readonly propertiesTableName: string;
  readonly leadsTableName: string;
  // e.g. https://<assets-cloudfront-domain>/media/photos (empty = no rewrite)
  readonly mediaBaseUrl: string;
  // The AgentCore Runtime the chat relay invokes (AgentStack).
  readonly agentRuntimeArn: string;
  // Spending caps (spec §8)
  readonly dailyQuestionCap?: number;
  readonly perIpPerMinute?: number;
  readonly isProd?: boolean;
}

/**
 * The api Lambda (spec §4, build step 5): the FastAPI app in api/ as an arm64
 * container image, run by Lambda Web Adapter so responses stream.
 *
 * Its Function URL is AWS_IAM: only the CloudFront distribution (origin access
 * control, wired in FrontendStack) may call it, so the public cannot bypass
 * CloudFront. No VPC, no ALB: nothing here bills while idle except the image
 * in ECR (~0.3 GB).
 *
 * Chat (step 7): guard_in, then the caps (a small on-demand counters table),
 * then InvokeAgentRuntime on exactly one runtime, streamed back unchanged.
 */
export class ApiStack extends cdk.Stack {
  public readonly fn: lambda.DockerImageFunction;
  public readonly functionUrl: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    const env = props.isProd ? "prod" : "dev";

    const logGroup = new logs.LogGroup(this, "ApiLogs", {
      logGroupName: `/aws/lambda/dubainest-agentcore-api-${env}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Atomic counters for the per-IP and daily caps (api/limits.py); TTL cleans up.
    const limitsTable = new dynamodb.Table(this, "LimitsTable", {
      tableName: `dubainest-agentcore-limits-${env}`,
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "expires_at",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.fn = new lambda.DockerImageFunction(this, "ApiFunction", {
      functionName: `dubainest-agentcore-api-${env}`,
      description: "DubaiNest AgentCore api: listings, enquiries, chat relay",
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, "..", "..", "api"),
        { platform: ecrAssets.Platform.LINUX_ARM64 },
      ),
      architecture: lambda.Architecture.ARM_64,
      memorySize: 512,
      // A chat answer streams for ~5-15 s; leave room for an AgentCore cold start.
      timeout: cdk.Duration.seconds(60),
      // Spec §8: a hard ceiling on parallel work behind a public endpoint.
      reservedConcurrentExecutions: 3,
      logGroup,
      environment: {
        AWS_LWA_INVOKE_MODE: "response_stream",
        DYNAMODB_PROPERTIES_TABLE: props.propertiesTableName,
        DYNAMODB_LEADS_TABLE: props.leadsTableName,
        MEDIA_BASE_URL: props.mediaBaseUrl,
        // The agent is not in this image: chat is relayed to AgentCore Runtime.
        AGENT_MODE: "agentcore",
        AGENT_RUNTIME_ARN: props.agentRuntimeArn,
        LIMITS_TABLE: limitsTable.tableName,
        DAILY_QUESTION_CAP: String(props.dailyQuestionCap ?? 200),
        PER_IP_PER_MINUTE: String(props.perIpPerMinute ?? 20),
        // Same origin behind CloudFront, so no browser CORS is needed.
        ALLOWED_ORIGINS: "",
      },
    });

    const properties = dynamodb.Table.fromTableName(
      this,
      "PropertiesTable",
      props.propertiesTableName,
    );
    const leads = dynamodb.Table.fromTableName(
      this,
      "LeadsTable",
      props.leadsTableName,
    );
    this.fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:Scan", "dynamodb:GetItem"],
        resources: [properties.tableArn],
      }),
    );
    this.fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:PutItem"],
        resources: [leads.tableArn],
      }),
    );

    this.fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:UpdateItem"],
        resources: [limitsTable.tableArn],
      }),
    );
    // InvokeAgentRuntime on this runtime only (and its endpoints, e.g. DEFAULT).
    this.fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock-agentcore:InvokeAgentRuntime"],
        resources: [props.agentRuntimeArn, `${props.agentRuntimeArn}/*`],
      }),
    );

    this.functionUrl = this.fn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
    });

    new cdk.CfnOutput(this, "ApiFunctionName", { value: this.fn.functionName });
    new cdk.CfnOutput(this, "ApiFunctionUrl", {
      value: this.functionUrl.url,
      description: "IAM-only: call it through CloudFront /api/*",
    });
  }
}
