import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";

export interface FrontendStackProps extends cdk.StackProps {
  // The api Lambda's Function URL (ApiStack). CloudFront forwards /api/* to it
  // and signs each request with origin access control.
  readonly apiFunctionUrl: lambda.IFunctionUrl;
  readonly isProd?: boolean;
}

export class FrontendStack extends cdk.Stack {
  public readonly siteBucket: s3.Bucket;
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    const isProd = props.isProd ?? false;
    const env = isProd ? "prod" : "dev";

    // S3 bucket for Angular build output
    this.siteBucket = new s3.Bucket(this, "SiteBucket", {
      bucketName: `dubainest-agentcore-frontend-${this.account}-${env}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: isProd
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: !isProd,
    });

    // Origin Access Control — CloudFront-only access to S3
    const oac = new cloudfront.S3OriginAccessControl(this, "Oac", {
      description: "DubaiNest AgentCore frontend OAC",
    });

    // Response headers — security headers for Angular SPA
    const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(
      this,
      "SecurityHeaders",
      {
        responseHeadersPolicyName: `DubaiNestAgentCore-Security-${env}`,
        securityHeadersBehavior: {
          contentTypeOptions: { override: true },
          frameOptions: {
            frameOption: cloudfront.HeadersFrameOption.DENY,
            override: true,
          },
          xssProtection: {
            protection: true,
            modeBlock: true,
            override: true,
          },
          strictTransportSecurity: {
            accessControlMaxAge: cdk.Duration.days(365),
            includeSubdomains: true,
            override: true,
          },
          contentSecurityPolicy: {
            contentSecurityPolicy:
              "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https:;",
            override: false,
          },
        },
        customHeadersBehavior: {
          customHeaders: [
            {
              header: "Cache-Control",
              value: "no-cache",
              override: false,
            },
          ],
        },
      },
    );

    // SPA routing on the S3 behaviour only: /properties/10 → /index.html.
    // Not distribution-wide errorResponses, which would also turn the api's
    // 403/404 JSON into index.html with a 200.
    const spaRewrite = new cloudfront.Function(this, "SpaRewrite", {
      functionName: `DubaiNestAgentCore-SpaRewrite-${env}`,
      comment:
        "Serve index.html for app routes (paths without a file extension)",
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var req = event.request;
  var last = req.uri.split("/").pop();
  if (last.indexOf(".") === -1) {
    req.uri = "/index.html";
  }
  return req;
}`),
    });

    // CloudFront distribution
    this.distribution = new cloudfront.Distribution(this, "Distribution", {
      comment: `DubaiNest AgentCore frontend — ${env}`,
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(
          this.siteBucket,
          { originAccessControl: oac },
        ),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        responseHeadersPolicy,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        compress: true,
        functionAssociations: [
          {
            function: spaRewrite,
            eventType: cloudfront.FunctionEventType.VIEWER_REQUEST,
          },
        ],
      },
      additionalBehaviors: {
        // API calls (no caching) to the api Lambda. The Function URL is
        // AWS_IAM, so CloudFront signs requests (OAC). Lambda needs a signed
        // payload hash on POST: the UI sends x-amz-content-sha256 itself
        // (frontend/src/app/services/signed-body.ts). The response streams.
        "/api/*": {
          origin: origins.FunctionUrlOrigin.withOriginAccessControl(
            props.apiFunctionUrl,
            { readTimeout: cdk.Duration.seconds(60) },
          ),
          viewerProtocolPolicy:
            cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          originRequestPolicy:
            cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      enableLogging: isProd,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
    });

    // CDK grants lambda:InvokeFunctionUrl for the OAC origin. Function URLs
    // created since Oct 2025 also need lambda:InvokeFunction, limited to calls
    // that arrive through the URL, from this distribution only.
    new lambda.CfnPermission(this, "ApiInvokeViaUrl", {
      action: "lambda:InvokeFunction",
      functionName: props.apiFunctionUrl.functionArn,
      principal: "cloudfront.amazonaws.com",
      sourceArn: `arn:aws:cloudfront::${this.account}:distribution/${this.distribution.distributionId}`,
      invokedViaFunctionUrl: true,
    });

    // Allow CloudFront to read from S3
    this.siteBucket.addToResourcePolicy(
      new cdk.aws_iam.PolicyStatement({
        actions: ["s3:GetObject"],
        principals: [
          new cdk.aws_iam.ServicePrincipal("cloudfront.amazonaws.com"),
        ],
        resources: [this.siteBucket.arnForObjects("*")],
        conditions: {
          StringEquals: {
            "AWS:SourceArn": `arn:aws:cloudfront::${this.account}:distribution/${this.distribution.distributionId}`,
          },
        },
      }),
    );

    // Outputs
    new cdk.CfnOutput(this, "SiteBucketName", {
      value: this.siteBucket.bucketName,
      exportName: "DubaiNestAgentCoreSiteBucket",
      description:
        "Deploy: aws s3 sync frontend/dist/dubainest-frontend/browser s3://<bucket>",
    });
    new cdk.CfnOutput(this, "CloudFrontDomain", {
      value: `https://${this.distribution.distributionDomainName}`,
      exportName: "DubaiNestAgentCoreCloudFrontUrl",
    });
    new cdk.CfnOutput(this, "DistributionId", {
      value: this.distribution.distributionId,
      exportName: "DubaiNestAgentCoreDistributionId",
      description:
        "Invalidate after deploy: aws cloudfront create-invalidation --distribution-id <id> --paths '/*'",
    });
  }
}
