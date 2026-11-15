import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

export interface AgentStackProps extends cdk.StackProps {
  // Created outside CDK (see the README): referenced by name only.
  readonly propertiesTableName: string;
  readonly leadsTableName: string;
  readonly isProd?: boolean;
}

// Models the graph calls (agent/config.py). Cross-region inference profiles
// route to any US region, so the foundation-model ARNs use a region wildcard.
const ROUTER_PROFILE = "us.amazon.nova-micro-v1:0";
const WRITER_PROFILE = "us.anthropic.claude-haiku-4-5-20251001-v1:0";
const EMBED_MODEL = "amazon.titan-embed-text-v2:0";
const INDEX_KEY = "listings_index.npz";

/**
 * The agent on Amazon Bedrock AgentCore Runtime (spec §4, build step 6).
 *
 * - The agent/ image (arm64, BedrockAgentCoreApp) goes to the CDK assets ECR
 *   repository; AgentCore runs it in a microVM per session and bills only
 *   while a session is alive. PUBLIC network mode: no VPC, so no NAT gateway.
 * - The listings vector index lives in a small private bucket (INDEX_URI);
 *   scripts/deploy.sh uploads it after the stack deploys.
 * - Memory (spec §7): two on-demand tables used through LangGraph's own
 *   interfaces (langgraph-checkpoint-aws). Sessions = the checkpointer
 *   (DynamoDBSaver, conversation per chat session, 24 h TTL); Memory = the
 *   store (DynamoDBStore, visitor profile, 90-day TTL refreshed on use).
 * - The role can call exactly three models, read Properties, add to Leads
 *   (confirmed viewings), read the index and use its two memory tables.
 * - In step 7 the api Lambda gets bedrock-agentcore:InvokeAgentRuntime on
 *   this runtime and starts relaying chat to it.
 */
export class AgentStack extends cdk.Stack {
  public readonly runtime: agentcore.CfnRuntime;

  constructor(scope: Construct, id: string, props: AgentStackProps) {
    super(scope, id, props);
    const env = props.isProd ? "prod" : "dev";

    const dataBucket = new s3.Bucket(this, "AgentData", {
      bucketName: `dubainest-agentcore-data-${this.account}-${env}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: props.isProd
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: !props.isProd,
    });

    // PK/SK strings: the schema langgraph-checkpoint-aws expects (tested with
    // moto in agent/tests/test_memory_dynamodb.py). Each library uses its own
    // TTL attribute, so each gets its own table.
    const memoryTable = (id: string, name: string, ttlAttr: string) =>
      new dynamodb.Table(this, id, {
        tableName: `dubainest-agentcore-${name}-${env}`,
        partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        timeToLiveAttribute: ttlAttr,
        encryption: dynamodb.TableEncryption.AWS_MANAGED,
        removalPolicy: props.isProd
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
      });
    const sessions = memoryTable("SessionsTable", "sessions", "ttl");
    const visitorMemory = memoryTable("MemoryTable", "memory", "expires_at");

    const image = new ecrAssets.DockerImageAsset(this, "AgentImage", {
      directory: path.join(__dirname, "..", ".."),
      file: "agent/Dockerfile",
      platform: ecrAssets.Platform.LINUX_ARM64,
      ignoreMode: cdk.IgnoreMode.DOCKER,
      // Only what the image copies, so unrelated changes don't rebuild it.
      exclude: [
        "*",
        "!agent",
        "!data/area_stats.json",
        "**/__pycache__",
        "agent/tests",
      ],
    });

    const role = new iam.Role(this, "AgentRole", {
      roleName: `dubainest-agentcore-agent-${env}`,
      description: "Assumed by the DubaiNest AgentCore Runtime",
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": this.account },
          ArnLike: {
            "aws:SourceArn": `arn:aws:bedrock-agentcore:${this.region}:${this.account}:*`,
          },
        },
      }),
    });

    image.repository.grantPull(role);

    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "Logs",
        actions: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        ],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "Observability",
        actions: ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
        resources: ["*"], // X-Ray has no resource-level permissions
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "Metrics",
        actions: ["cloudwatch:PutMetricData"],
        resources: ["*"],
        conditions: {
          StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" },
        },
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "ChatModels",
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${ROUTER_PROFILE}`,
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${WRITER_PROFILE}`,
          `arn:aws:bedrock:*::foundation-model/${ROUTER_PROFILE.replace("us.", "")}`,
          `arn:aws:bedrock:*::foundation-model/${WRITER_PROFILE.replace("us.", "")}`,
        ],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "QueryEmbedding",
        actions: ["bedrock:InvokeModel"],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/${EMBED_MODEL}`,
        ],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "Listings",
        actions: ["dynamodb:Scan"],
        resources: [
          `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.propertiesTableName}`,
        ],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "ConfirmedViewings",
        actions: ["dynamodb:PutItem"],
        resources: [
          `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.leadsTableName}`,
        ],
      }),
    );
    dataBucket.grantRead(role, INDEX_KEY);
    sessions.grantReadWriteData(role);
    visitorMemory.grantReadWriteData(role);

    this.runtime = new agentcore.CfnRuntime(this, "AgentRuntime", {
      agentRuntimeName: `dubainest_agentcore_agent_${env}`,
      description:
        "DubaiNest: LangGraph orchestrates Strands agents; DynamoDB memory",
      roleArn: role.roleArn,
      agentRuntimeArtifact: {
        containerConfiguration: { containerUri: image.imageUri },
      },
      networkConfiguration: { networkMode: "PUBLIC" },
      // A session's microVM is billed while alive: end it after 5 idle
      // minutes (a chat window's next question usually comes sooner).
      lifecycleConfiguration: {
        idleRuntimeSessionTimeout: 300,
        maxLifetime: 3600,
      },
      environmentVariables: {
        AWS_REGION: this.region,
        LISTINGS_SOURCE: "dynamodb",
        DYNAMODB_PROPERTIES_TABLE: props.propertiesTableName,
        DYNAMODB_LEADS_TABLE: props.leadsTableName,
        INDEX_URI: dataBucket.s3UrlForObject(INDEX_KEY),
        ROUTER_MODEL_ID: ROUTER_PROFILE,
        WRITER_MODEL_ID: WRITER_PROFILE,
        SESSIONS_TABLE: sessions.tableName,
        MEMORY_TABLE: visitorMemory.tableName,
      },
    });
    // The role's policies must exist before AgentCore validates the image pull.
    this.runtime.node.addDependency(role);

    new cdk.CfnOutput(this, "AgentRuntimeArn", {
      value: this.runtime.attrAgentRuntimeArn,
      description: "scripts/invoke_agent.py --arn <this>",
    });
    new cdk.CfnOutput(this, "AgentRuntimeId", {
      value: this.runtime.attrAgentRuntimeId,
    });
    new cdk.CfnOutput(this, "SessionsTableName", {
      value: sessions.tableName,
    });
    new cdk.CfnOutput(this, "MemoryTableName", {
      value: visitorMemory.tableName,
    });
    new cdk.CfnOutput(this, "IndexUri", {
      value: dataBucket.s3UrlForObject(INDEX_KEY),
      description: "scripts/build_index.py --upload <this>",
    });
  }
}
