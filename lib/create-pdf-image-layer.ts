import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

/**
 * This function creates a Lambda layer for pdf2image and poppler using a custom resource
 * @param scope The CDK construct scope
 * @param id The ID for the layer
 * @param processedBucket The S3 bucket to store the layer
 * @param lambdaFunctions Array of Lambda function names to attach the layer to
 * @returns The Lambda layer ARN
 */
export function createPdfImageLayer(
  scope: Construct,
  id: string,
  processedBucket: s3.Bucket,
  lambdaFunctions: string[]
): string {
  // Create a role for the PDF image layer Lambda
  const pdfImageLayerLambdaRole = new iam.Role(scope, `${id}Role`, {
    assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    managedPolicies: [
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
    ],
  });

  // Add permissions for S3 and Lambda
  pdfImageLayerLambdaRole.addToPolicy(
    new iam.PolicyStatement({
      actions: ['s3:PutObject', 's3:GetObject', 's3:ListBucket'],
      resources: [
        processedBucket.bucketArn,
        `${processedBucket.bucketArn}/*`,
      ],
    })
  );

  pdfImageLayerLambdaRole.addToPolicy(
    new iam.PolicyStatement({
      actions: [
        'lambda:PublishLayerVersion',
        'lambda:GetLayerVersion',
        'lambda:GetFunction',
        'lambda:GetFunctionConfiguration',
        'lambda:UpdateFunctionConfiguration',
      ],
      resources: ['*'], // Scope down in production
    })
  );

  // Create a Lambda function to create the PDF image layer
  const createPdfImageLayerLambda = new lambda.Function(scope, `${id}Function`, {
    runtime: lambda.Runtime.PYTHON_3_9,
    handler: 'create-pdf-image-layer.lambda_handler',
    code: lambda.Code.fromAsset('lambda'),
    timeout: cdk.Duration.minutes(10),
    memorySize: 1024,
    role: pdfImageLayerLambdaRole,
    environment: {
      PROCESSED_BUCKET_NAME: processedBucket.bucketName,
    },
  });

  // Create a custom resource provider for the PDF image layer
  const createPdfImageLayerProvider = new cdk.custom_resources.Provider(scope, `${id}Provider`, {
    onEventHandler: createPdfImageLayerLambda,
    logRetention: logs.RetentionDays.ONE_WEEK,
  });

  // Create the PDF image layer resource
  const layerResource = new cdk.CustomResource(scope, `${id}Resource`, {
    serviceToken: createPdfImageLayerProvider.serviceToken,
    properties: {
      bucket_name: processedBucket.bucketName,
      layer_name: 'pdf-image-layer',
      lambda_functions: lambdaFunctions
    },
  });

  // Return the layer ARN
  return layerResource.getAttString('LayerArn');
}