import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as stepfunctions from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import { createPdfImageLayer } from './create-pdf-image-layer';
import { BedrockInferenceProfile } from './create-inference-profile';

export class DocumentProcessingCdkStack extends cdk.Stack {
  // Public properties to expose resources to other stacks if needed
  public readonly documentBucket: s3.Bucket;
  public readonly processedBucket: s3.Bucket;
  public readonly payloadBucket: s3.Bucket;
  public readonly metadataTable: dynamodb.Table;
  public readonly searchIndexTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // S3 bucket for storing original documents
    this.documentBucket = new s3.Bucket(this, 'DocumentBucket', {
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN, // RETAIN to prevent accidental deletion
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [
        {
          id: 'DeleteOldVersions',
          enabled: true,
          noncurrentVersionExpiration: cdk.Duration.days(30),
        },
      ],
      cors: [
        {
          allowedMethods: [
            s3.HttpMethods.GET,
            s3.HttpMethods.POST,
            s3.HttpMethods.PUT,
          ],
          allowedOrigins: ['*'],
          allowedHeaders: ['*'],
        },
      ],
    });

    // S3 bucket for storing processed documents optimized for Bedrock knowledge base
    this.processedBucket = new s3.Bucket(this, 'ProcessedDocumentBucket', {
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [
        {
          id: 'DeleteOldVersions',
          enabled: true,
          noncurrentVersionExpiration: cdk.Duration.days(30),
        },
      ],
    });

    // S3 bucket for storing large payloads between Step Functions states
    this.payloadBucket = new s3.Bucket(this, 'PayloadBucket', {
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [
        {
          id: 'DeleteOldPayloads',
          enabled: true,
          expiration: cdk.Duration.days(1), // Payloads are temporary, delete after 1 day
          noncurrentVersionExpiration: cdk.Duration.days(1),
        },
      ],
    });

    // DynamoDB table for storing document metadata
    this.metadataTable = new dynamodb.Table(this, 'DocumentMetadataTable', {
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'document_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecovery: true,
    });

    // Add GSI for querying by document_id
    this.metadataTable.addGlobalSecondaryIndex({
      indexName: 'DocumentIdIndex',
      partitionKey: { name: 'document_id', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Add GSI for querying by category
    this.metadataTable.addGlobalSecondaryIndex({
      indexName: 'CategoryIndex',
      partitionKey: { name: 'category', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // DynamoDB table for search indices
    this.searchIndexTable = new dynamodb.Table(this, 'SearchIndexTable', {
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Add GSI for querying by index_type and index_value
    this.searchIndexTable.addGlobalSecondaryIndex({
      indexName: 'IndexTypeValueIndex',
      partitionKey: { name: 'index_type', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'index_value', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Add GSI for querying by document_id
    this.searchIndexTable.addGlobalSecondaryIndex({
      indexName: 'DocumentIdIndex',
      partitionKey: { name: 'document_id', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Lambda role with permissions for Textract, S3, and Bedrock
    const textractProcessorRole = new iam.Role(this, 'TextractProcessorRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonTextractFullAccess'),
      ],
    });

    // Add permissions for S3 and Bedrock
    textractProcessorRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
        resources: [
          this.documentBucket.bucketArn,
          `${this.documentBucket.bucketArn}/*`,
          this.processedBucket.bucketArn,
          `${this.processedBucket.bucketArn}/*`,
        ],
      })
    );

    textractProcessorRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock-runtime:InvokeModel'],
        resources: ['*'], // Scope down to specific models in production
      })
    );

    // We'll create the PDF/Image processing Lambda Layer later using a custom resource
    // and attach it to the Lambda functions after they're created

    // Lambda function for Textract processing
    const textractProcessorLambda = new lambda.Function(this, 'TextractProcessorFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'textract-processor.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(15),
      memorySize: 1024,
      role: textractProcessorRole,
      environment: {
        PROCESSED_BUCKET_NAME: this.processedBucket.bucketName,
        // Add any other necessary environment variables for the layer if needed
        // e.g., if poppler path needs to be explicitly set for some reason
      },
    });

    // Lambda role for metadata extraction and image description generation
    const metadataExtractorRole = new iam.Role(this, 'MetadataExtractorRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Add permissions for S3 and DynamoDB
    metadataExtractorRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket', 's3:PutObject'],
        resources: [
          this.documentBucket.bucketArn, // Add permission for DocumentBucket
          `${this.documentBucket.bucketArn}/*`,
          this.processedBucket.bucketArn,
          `${this.processedBucket.bucketArn}/*`,
          this.payloadBucket.bucketArn,
          `${this.payloadBucket.bucketArn}/*`,
        ],
      })
    );

    metadataExtractorRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'dynamodb:PutItem',
          'dynamodb:GetItem',
          'dynamodb:UpdateItem',
          'dynamodb:Query',
          'dynamodb:Scan',
        ],
        resources: [
          this.metadataTable.tableArn,
          this.searchIndexTable.tableArn,
          `${this.metadataTable.tableArn}/index/*`,
          `${this.searchIndexTable.tableArn}/index/*`,
        ],
      })
    );
    metadataExtractorRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        // Consider scoping this down to the specific model ARN if known and fixed
        // e.g., "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0"
        resources: ['*'],
      })
    );

    // Lambda function for metadata extraction
    const metadataExtractorLambda = new lambda.Function(this, 'MetadataExtractorFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'metadata-extractor.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(10),
      memorySize: 512,
      role: metadataExtractorRole,
      environment: {
        METADATA_TABLE_NAME: this.metadataTable.tableName,
        SEARCH_INDEX_TABLE_NAME: this.searchIndexTable.tableName,
        PAYLOAD_BUCKET_NAME: this.payloadBucket.bucketName,
      },
    });

    // Lambda function for image description generation
    const imageDescriptionGeneratorLambda = new lambda.Function(this, 'ImageDescriptionGeneratorFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'image-description-generator.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(15), // Longer timeout for image processing
      memorySize: 1024, // More memory for image processing
      role: metadataExtractorRole, // Reuse the same role
      environment: {
        METADATA_TABLE_NAME: this.metadataTable.tableName,
        SEARCH_INDEX_TABLE_NAME: this.searchIndexTable.tableName,
        PAYLOAD_BUCKET_NAME: this.payloadBucket.bucketName,
        PROCESSED_BUCKET_NAME: this.processedBucket.bucketName,
        BEDROCK_MODEL_ID_FOR_IMAGE_DESC: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
      },
    });

    // Create IAM role for Bedrock knowledge base
    const bedrockKnowledgeBaseRole = new iam.Role(this, 'BedrockKnowledgeBaseRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonBedrockFullAccess'),
      ],
      inlinePolicies: {
        'BedrockKnowledgeBasePermissions': new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                'bedrock:ListKnowledgeBases',
                'bedrock:ListDataSources',
                'bedrock-agent:ListKnowledgeBases',
                'bedrock-agent:ListDataSources'
              ],
              resources: ['*']
            })
          ]
        })
      }
    });

    // Add permissions for S3
    bedrockKnowledgeBaseRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [
          this.processedBucket.bucketArn,
          `${this.processedBucket.bucketArn}/*`,
        ],
      })
    );

    // Add permissions for Kendra
    bedrockKnowledgeBaseRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'kendra:Retrieve',
          'kendra:DescribeIndex',
          'kendra:Query'
        ],
        resources: ['arn:aws:kendra:us-east-1:361769603480:index/4c9190f6-671c-4508-a524-a180433c2774'],
      })
    );

    // Lambda role for Bedrock knowledge base integration
    const bedrockLambdaRole = new iam.Role(this, 'BedrockLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Add permissions for Bedrock, S3, and DynamoDB
    bedrockLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          // Bedrock agent permissions
          'bedrock-agent:CreateKnowledgeBase',
          'bedrock-agent:CreateDataSource',
          'bedrock-agent:StartIngestionJob',
          'bedrock-agent:GetIngestionJob',
          'bedrock-agent:ListIngestionJobs',
          'bedrock-agent:Retrieve',
          'bedrock-agent:ListKnowledgeBases',
          'bedrock-agent:ListDataSources',
          // Bedrock permissions (older API)
          'bedrock:CreateKnowledgeBase',
          'bedrock:CreateDataSource',
          'bedrock:StartIngestionJob',
          'bedrock:GetIngestionJob',
          'bedrock:ListIngestionJobs',
          'bedrock:Retrieve',
          'bedrock:ListKnowledgeBases',
          'bedrock:ListDataSources',
          // IAM permissions
          'iam:PassRole',
        ],
        resources: ['*'], // Scope down in production
      })
    );

    // Add specific permissions for invoking Bedrock models and managing inference profiles
    bedrockLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock:InvokeModel',
          'bedrock-runtime:InvokeModel'
        ],
        resources: [
          'arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0',
          'arn:aws:bedrock:us-east-1:*:inference-profile/*'
        ]
      })
    );

    // Add permissions for Kendra
    bedrockLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'kendra:Retrieve',
          'kendra:DescribeIndex',
          'kendra:Query',
          'kendra:BatchPutDocument',
          'kendra:BatchDeleteDocument',
          'kendra:BatchGetDocumentStatus',
          'kendra:StartDataSourceSyncJob',
          'kendra:DescribeDataSource',
          'kendra:ListDataSources',
          'kendra:ListDataSourceSyncJobs',
          'kendra:ListDocuments',
          'kendra:DescribeDocument'
        ],
        resources: ['arn:aws:kendra:us-east-1:361769603480:index/4c9190f6-671c-4508-a524-a180433c2774'],
      })
    );

    // Add permissions for Kendra data sources
    bedrockLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'kendra:DescribeDataSource',
          'kendra:ListDataSources',
          'kendra:StartDataSourceSyncJob',
          'kendra:StopDataSourceSyncJob',
          'kendra:ListDataSourceSyncJobs'
        ],
        resources: ['arn:aws:kendra:us-east-1:361769603480:index/4c9190f6-671c-4508-a524-a180433c2774/data-source/*'],
      })
    );

    bedrockLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket', 's3:PutObject'],
        resources: [
          this.processedBucket.bucketArn,
          `${this.processedBucket.bucketArn}/*`,
          this.payloadBucket.bucketArn,
          `${this.payloadBucket.bucketArn}/*`,
        ],
      })
    );

    // Add permissions to access any S3 bucket that might be used as a Kendra data source
    // Note: In a production environment, you should scope this down to specific buckets
    bedrockLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket', 's3:PutObject'],
        resources: ['*'],  // Scope this down in production
      })
    );

    bedrockLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'dynamodb:PutItem',
          'dynamodb:GetItem',
          'dynamodb:UpdateItem',
          'dynamodb:Query',
          'dynamodb:Scan',
        ],
        resources: [
          this.metadataTable.tableArn,
          this.searchIndexTable.tableArn,
          `${this.metadataTable.tableArn}/index/*`,
          `${this.searchIndexTable.tableArn}/index/*`,
        ],
      })
    );

    // Lambda function for Bedrock knowledge base integration
    const bedrockKnowledgeBaseLambda = new lambda.Function(this, 'BedrockKnowledgeBaseFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'bedrock-knowledge-base.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(10),
      memorySize: 512,
      role: bedrockLambdaRole,
      environment: {
        METADATA_TABLE_NAME: this.metadataTable.tableName,
        SEARCH_INDEX_TABLE_NAME: this.searchIndexTable.tableName, // Add this
        PROCESSED_BUCKET_NAME: this.processedBucket.bucketName,
        KNOWLEDGE_BASE_ROLE_ARN: bedrockKnowledgeBaseRole.roleArn,
        PAYLOAD_BUCKET_NAME: this.payloadBucket.bucketName,
        AUTO_CREATE_KNOWLEDGE_BASE: 'true',
        KENDRA_INDEX_ID: '4c9190f6-671c-4508-a524-a180433c2774', // Your Kendra index ID
        KENDRA_S3_BUCKET: 'aseekbot-poc-kb', // Kendra S3 data source bucket
        // CLAUDE_INFERENCE_PROFILE_ARN: '', // Explicitly unset or remove if not using a specific profile for this function
      },
    });

    // Create role for the payload utility Lambda
    const payloadUtilsRole = new iam.Role(this, 'PayloadUtilsRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Add permissions for S3
    payloadUtilsRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
        resources: [
          this.payloadBucket.bucketArn,
          `${this.payloadBucket.bucketArn}/*`,
        ],
      })
    );

    // Lambda function for handling large payloads
    const payloadUtilsLambda = new lambda.Function(this, 'PayloadUtilsFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'payload-utils.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      role: payloadUtilsRole,
      environment: {
        PAYLOAD_BUCKET_NAME: this.payloadBucket.bucketName,
      },
    });

    // Lambda function for retrieving payloads and extracting metadata fields
    const retrievePayloadLambda = new lambda.Function(this, 'RetrievePayloadFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'retrieve-payload.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      role: payloadUtilsRole, // Reuse the same role as payloadUtilsLambda
      environment: {
        PAYLOAD_BUCKET_NAME: this.payloadBucket.bucketName,
      },
    });

    // Step Functions state machine for document processing
    // Define the Textract processing task
    const textractTask = new tasks.LambdaInvoke(this, 'ProcessDocumentWithTextract', {
      lambdaFunction: textractProcessorLambda,
      outputPath: '$.Payload',
    });

    // Define a task to store the Textract result in S3 if it's too large
    const storeTextractResultTask = new tasks.LambdaInvoke(this, 'StoreTextractResult', {
      lambdaFunction: payloadUtilsLambda,
      payload: stepfunctions.TaskInput.fromObject({
        operation: 'store',
        'payload': stepfunctions.JsonPath.entirePayload
      }),
      outputPath: '$.Payload',
    });

    // Define tasks to extract the processed_key from the Textract result
    // One for the large payload path
    const extractProcessedKeyLargeState = new stepfunctions.Pass(this, 'ExtractProcessedKeyLarge', {
      parameters: {
        'processed_bucket': this.processedBucket.bucketName,
        'processed_key': stepfunctions.JsonPath.stringAt('$.payload_reference.key'),
        'original_status_code': stepfunctions.JsonPath.numberAt('$.original_status_code')
      },
    });

    // One for the normal path
    const extractProcessedKeyState = new stepfunctions.Pass(this, 'ExtractProcessedKey', {
      parameters: {
        'processed_bucket': this.processedBucket.bucketName,
        'processed_key': 'dummy-key', // We'll extract this in the Lambda function
        'original_status_code': stepfunctions.JsonPath.numberAt('$.statusCode')
      },
    });

    // Define the metadata extraction task
    const metadataTask = new tasks.LambdaInvoke(this, 'ExtractAndStoreMetadata', {
      lambdaFunction: metadataExtractorLambda,
      payload: stepfunctions.TaskInput.fromObject({
        'processed_bucket': stepfunctions.JsonPath.stringAt('$.processed_bucket'),
        'processed_key': stepfunctions.JsonPath.stringAt('$.processed_key')
      }),
      outputPath: '$.Payload',
    });

    // Define a function to create an image description generator task
    const createImageDescriptionTask = (id: string) => {
      return new tasks.LambdaInvoke(this, id, {
        lambdaFunction: imageDescriptionGeneratorLambda,
        payload: stepfunctions.TaskInput.fromObject({
          'document_id': stepfunctions.JsonPath.stringAt('$.metadata.document_id'),
          'metadata_id': stepfunctions.JsonPath.stringAt('$.metadata.id')
        }),
        outputPath: '$.Payload',
      });
    };

    // Define a task to store the metadata result in S3 if it's too large
    const storeMetadataResultTask = new tasks.LambdaInvoke(this, 'StoreMetadataResult', {
      lambdaFunction: payloadUtilsLambda,
      payload: stepfunctions.TaskInput.fromObject({
        operation: 'store',
        'payload': stepfunctions.JsonPath.entirePayload
      }),
      outputPath: '$.Payload',
    });

    // Define a task to retrieve the payload and extract metadata fields
    const retrievePayloadTask = new tasks.LambdaInvoke(this, 'RetrievePayload', {
      lambdaFunction: retrievePayloadLambda,
      outputPath: '$.Payload',
    });

    // Define tasks to extract metadata for the Bedrock task
    // One for the large payload path
    const extractMetadataForBedrockLargeState = new stepfunctions.Pass(this, 'ExtractMetadataForBedrockLarge', {
      parameters: {
        'processed_bucket': stepfunctions.JsonPath.stringAt('$.processed_bucket'),
        'processed_key': stepfunctions.JsonPath.stringAt('$.processed_key'),
        'document_id': stepfunctions.JsonPath.stringAt('$.document_id'),
        'metadata': {
          'document_id': stepfunctions.JsonPath.stringAt('$.document_id'),
          'id': stepfunctions.JsonPath.stringAt('$.metadata_id')
        }
      },
    });

    // One for the normal path
    const extractMetadataForBedrockState = new stepfunctions.Pass(this, 'ExtractMetadataForBedrock', {
      parameters: {
        'processed_bucket': stepfunctions.JsonPath.stringAt('$.metadata.processed_bucket'),
        'processed_key': stepfunctions.JsonPath.stringAt('$.metadata.processed_key'),
        'document_id': stepfunctions.JsonPath.stringAt('$.metadata.document_id')
      },
    });

    // Define the Bedrock knowledge base task
    const bedrockTask = new tasks.LambdaInvoke(this, 'AddToBedrockKnowledgeBase', {
      lambdaFunction: bedrockKnowledgeBaseLambda,
      // Use the entire input as the payload, but don't set payloadResponseOnly
      // This ensures the response has the standard Lambda Invoke structure with Payload field
    });

    // Define the success and failure states
    const successState = new stepfunctions.Succeed(this, 'ProcessingSucceeded');
    const failureState = new stepfunctions.Fail(this, 'ProcessingFailed', {
      cause: 'Document processing failed',
      error: 'DocumentProcessingError',
    });

    // Define the state machine
    const definition = textractTask
      .next(new stepfunctions.Choice(this, 'CheckTextractProcessing')
        .when(stepfunctions.Condition.numberEquals('$.statusCode', 200),
          // Check if the payload is too large (over 200KB)
          new stepfunctions.Choice(this, 'CheckTextractPayloadSize')
            .when(stepfunctions.Condition.and(
              stepfunctions.Condition.isPresent('$.body'),
              stepfunctions.Condition.stringGreaterThan('$.body', '204800') // 200KB in characters
            ),
              storeTextractResultTask
                .next(extractProcessedKeyLargeState)
                .next(metadataTask)
            )
            .otherwise(
              extractProcessedKeyState
                .next(metadataTask)
            )
            .afterwards()
            .next(new stepfunctions.Choice(this, 'CheckMetadataExtraction')
              .when(stepfunctions.Condition.numberEquals('$.statusCode', 200),
                // Check if the metadata payload is too large
                new stepfunctions.Choice(this, 'CheckMetadataPayloadSize')
                  .when(stepfunctions.Condition.and(
                    stepfunctions.Condition.isPresent('$.body'),
                    stepfunctions.Condition.stringGreaterThan('$.body', '204800') // 200KB in characters
                  ),
                    storeMetadataResultTask
                      .next(retrievePayloadTask)
                      .next(extractMetadataForBedrockLargeState)
                      .next(createImageDescriptionTask('GenerateImageDescriptionsLarge'))
                      .next(new stepfunctions.Pass(this, 'MapImageDescriptionFieldsLarge', {
                        parameters: {
                          'processed_bucket.$': "$.processed_bucket",
                          'processed_key.$': "$.processed_key",
                          'document_id.$': "$.document_id",
                          'statusCode.$': "$.statusCode",
                          'operation': 'add_document_to_knowledge_base'
                        },
                      }))
                      .next(bedrockTask)
                  )
                  .otherwise(
                    extractMetadataForBedrockState
                      .next(createImageDescriptionTask('GenerateImageDescriptionsNormal'))
                      .next(new stepfunctions.Pass(this, 'MapImageDescriptionFieldsNormal', {
                        parameters: {
                          'processed_bucket.$': "$.processed_bucket",
                          'processed_key.$': "$.processed_key",
                          'document_id.$': "$.document_id",
                          'statusCode.$': "$.statusCode",
                          'operation': 'add_document_to_knowledge_base'
                        },
                      }))
                      .next(bedrockTask)
                  )
                  .afterwards()
                  .next(new stepfunctions.Choice(this, 'CheckBedrockIntegration')
                    .when(stepfunctions.Condition.numberEquals('$.Payload.statusCode', 200), successState)
                    .otherwise(failureState)
                  )
              )
              .otherwise(failureState)
            )
        )
        .otherwise(failureState)
      );

    // Create the state machine
    const documentProcessingStateMachine = new stepfunctions.StateMachine(this, 'DocumentProcessingStateMachine', {
      definition,
      timeout: cdk.Duration.minutes(30),
      tracingEnabled: true,
      logs: {
        destination: new logs.LogGroup(this, 'DocumentProcessingStateMachineLogs', {
          retention: logs.RetentionDays.ONE_WEEK,
        }),
        level: stepfunctions.LogLevel.ALL,
      },
    });

    // Create a Lambda function to trigger the state machine
    const stateMachineTriggerRole = new iam.Role(this, 'StateMachineTriggerRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    stateMachineTriggerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['states:StartExecution'],
        resources: [documentProcessingStateMachine.stateMachineArn],
      })
    );

    const stateMachineTriggerLambda = new lambda.Function(this, 'StateMachineTriggerFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'trigger-state-machine.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(1),
      memorySize: 128,
      role: stateMachineTriggerRole,
      environment: {
        STATE_MACHINE_ARN: documentProcessingStateMachine.stateMachineArn,
      },
    });

    // Add S3 event notification to trigger the state machine
    this.documentBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(stateMachineTriggerLambda)
    );

    // Create a Lambda function to initialize the Bedrock knowledge base
    const initializeKnowledgeBaseLambda = new lambda.Function(this, 'InitializeKnowledgeBaseFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'initialize-kb.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      role: bedrockLambdaRole,
      environment: {
        METADATA_TABLE_NAME: this.metadataTable.tableName,
        PROCESSED_BUCKET_NAME: this.processedBucket.bucketName,
        KNOWLEDGE_BASE_ROLE_ARN: bedrockKnowledgeBaseRole.roleArn,
        PAYLOAD_BUCKET_NAME: this.payloadBucket.bucketName,
        AUTO_CREATE_KNOWLEDGE_BASE: 'true',
        KENDRA_INDEX_ID: '4c9190f6-671c-4508-a524-a180433c2774', // Your Kendra index ID
        KENDRA_S3_BUCKET: 'aseekbot-poc-kb', // Kendra S3 data source bucket
        // Note: CLAUDE_INFERENCE_PROFILE_ARN should be set manually after deployment
      },
    });

    // Create the PDF/Image processing Lambda Layer using the custom resource approach
    // This will create the layer and attach it to the Lambda functions
    createPdfImageLayer(
      this,
      'PdfImageProcessingLayer',
      this.processedBucket,
      [
        textractProcessorLambda.functionName,
        metadataExtractorLambda.functionName,
        imageDescriptionGeneratorLambda.functionName,
        bedrockKnowledgeBaseLambda.functionName
      ]
    );

    // Create a custom resource to initialize the knowledge base during deployment
    const initializeKnowledgeBaseProvider = new cdk.custom_resources.Provider(this, 'InitializeKnowledgeBaseProvider', {
      onEventHandler: initializeKnowledgeBaseLambda,
      logRetention: logs.RetentionDays.ONE_WEEK,
    });

    // Create the knowledge base resource and ignore the unused variable warning
    new cdk.CustomResource(this, 'InitializeKnowledgeBaseResource', {
      serviceToken: initializeKnowledgeBaseProvider.serviceToken,
      properties: {
        knowledge_base_name: 'DocumentProcessingKnowledgeBase',
      },
    });



    // Output the state machine ARN
    new cdk.CfnOutput(this, 'DocumentProcessingStateMachineArn', {
      value: documentProcessingStateMachine.stateMachineArn,
      description: 'The ARN of the document processing state machine',
      exportName: 'DocumentProcessingStateMachineArn',
    });

    // Output the bucket names
    new cdk.CfnOutput(this, 'DocumentBucketName', {
      value: this.documentBucket.bucketName,
      description: 'The name of the S3 bucket where original documents are stored',
      exportName: 'DocumentBucketName',
    });

    new cdk.CfnOutput(this, 'ProcessedBucketName', {
      value: this.processedBucket.bucketName,
      description: 'The name of the S3 bucket where processed documents are stored for Bedrock knowledge base',
      exportName: 'ProcessedBucketName',
    });

    new cdk.CfnOutput(this, 'PayloadBucketName', {
      value: this.payloadBucket.bucketName,
      description: 'The name of the S3 bucket where large payloads are stored temporarily',
      exportName: 'PayloadBucketName',
    });

    // Output the DynamoDB table names
    new cdk.CfnOutput(this, 'MetadataTableName', {
      value: this.metadataTable.tableName,
      description: 'The name of the DynamoDB table where document metadata is stored',
      exportName: 'MetadataTableName',
    });

    new cdk.CfnOutput(this, 'SearchIndexTableName', {
      value: this.searchIndexTable.tableName,
      description: 'The name of the DynamoDB table where search indices are stored',
      exportName: 'SearchIndexTableName',
    });

    // Output the Bedrock knowledge base Lambda function name
    new cdk.CfnOutput(this, 'BedrockKnowledgeBaseFunctionName', {
      value: bedrockKnowledgeBaseLambda.functionName,
      description: 'The name of the Lambda function for interacting with the Bedrock knowledge base',
      exportName: 'BedrockKnowledgeBaseFunctionName',
    });

    // Create the inference profile and update the Lambda functions
    const inferenceProfile = new BedrockInferenceProfile(this, 'BedrockInferenceProfile', [
      bedrockKnowledgeBaseLambda,
      imageDescriptionGeneratorLambda
    ]);
  }
}
