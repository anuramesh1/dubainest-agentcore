import * as cdk from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as actions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subscriptions from "aws-cdk-lib/aws-sns-subscriptions";
import * as budgets from "aws-cdk-lib/aws-budgets";
import { Construct } from "constructs";

// Build step 1 version: the ALB / ECS alarms and widgets went with the Fargate
// backend. What is left bills nothing while idle: an SNS topic, a DynamoDB
// throttle alarm, the dashboard shell and the monthly budget. Step 7 adds the
// api Lambda and AgentCore metrics.

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly isProd?: boolean;
  /** Email to receive alarm and budget notifications */
  readonly alertEmail?: string;
  /** Monthly AWS budget limit in USD (spec §8: $10) */
  readonly monthlyBudgetUsd?: number;
}

export class ObservabilityStack extends cdk.Stack {
  public readonly dashboard: cloudwatch.Dashboard;
  public readonly alertTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);

    const isProd = props.isProd ?? false;
    const env = isProd ? "prod" : "dev";

    // The tables are shared with dubainest-ai, which still owns them (spec §13
    // decision 5), so they are referenced by name, never created here.
    const leadsTable = dynamodb.Table.fromTableName(
      this,
      "LeadsTable",
      `dubainest-leads-${env}`,
    );
    const propertiesTable = dynamodb.Table.fromTableName(
      this,
      "PropertiesTable",
      `dubainest-properties-${env}`,
    );

    this.alertTopic = new sns.Topic(this, "AlertTopic", {
      topicName: `dubainest-agentcore-alerts-${env}`,
      displayName: "DubaiNest AgentCore Alerts",
    });
    if (props.alertEmail) {
      this.alertTopic.addSubscription(
        new subscriptions.EmailSubscription(props.alertEmail),
      );
    }
    const alarmAction = new actions.SnsAction(this.alertTopic);

    // ── Alarms ───────────────────────────────────────────────────────────────

    const ddbThrottleAlarm = new cloudwatch.Alarm(this, "DdbThrottleAlarm", {
      alarmName: `DubaiNestAgentCore-${env}-Leads-Throttles`,
      alarmDescription: "DynamoDB Leads table throttled requests",
      metric: leadsTable.metric("ThrottledRequests", {
        period: cdk.Duration.minutes(5),
        statistic: "Sum",
      }),
      threshold: 5,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    ddbThrottleAlarm.addAlarmAction(alarmAction);

    // ── Dashboard ────────────────────────────────────────────────────────────

    this.dashboard = new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: `DubaiNestAgentCore-${env}`,
      periodOverride: cloudwatch.PeriodOverride.AUTO,
    });
    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: `# DubaiNest AgentCore — ${env.toUpperCase()}\nData layer for now; api Lambda and AgentCore widgets arrive in build step 7.`,
        width: 24,
        height: 2,
      }),
    );
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: "DynamoDB Properties — reads",
        left: [
          propertiesTable.metric("ConsumedReadCapacityUnits", {
            period: cdk.Duration.minutes(5),
            statistic: "Sum",
            label: "RCU",
          }),
        ],
        width: 12,
        height: 6,
      }),
      new cloudwatch.GraphWidget({
        title: "DynamoDB Leads — writes and throttles",
        left: [
          leadsTable.metric("ConsumedWriteCapacityUnits", {
            period: cdk.Duration.minutes(5),
            statistic: "Sum",
            label: "WCU",
          }),
          leadsTable.metric("ThrottledRequests", {
            period: cdk.Duration.minutes(5),
            statistic: "Sum",
            label: "Throttles",
          }),
        ],
        width: 12,
        height: 6,
      }),
    );

    // ── AWS Budget: alerts at 50%, 80% and 100% (spec §8) ───────────────────

    const budgetLimit = props.monthlyBudgetUsd ?? 10;
    const alertAt = (threshold: number) => ({
      notification: {
        notificationType: "ACTUAL",
        comparisonOperator: "GREATER_THAN",
        threshold,
        thresholdType: "PERCENTAGE",
      },
      subscribers: [
        { subscriptionType: "EMAIL", address: props.alertEmail as string },
      ],
    });
    new budgets.CfnBudget(this, "MonthlyBudget", {
      budget: {
        budgetName: `dubainest-agentcore-monthly-${env}`,
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: { amount: budgetLimit, unit: "USD" },
      },
      notificationsWithSubscribers: props.alertEmail
        ? [alertAt(50), alertAt(80), alertAt(100)]
        : [],
    });

    new cdk.CfnOutput(this, "DashboardUrl", {
      value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home#dashboards:name=DubaiNestAgentCore-${env}`,
      exportName: "DubaiNestAgentCoreDashboardUrl",
    });
    new cdk.CfnOutput(this, "AlertTopicArn", {
      value: this.alertTopic.topicArn,
      exportName: "DubaiNestAgentCoreAlertTopicArn",
    });
  }
}
