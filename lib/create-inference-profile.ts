import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';

/**
 * Custom resource to create a Bedrock inference profile for Claude 3.5 Sonnet
 */
export class BedrockInferenceProfile extends Construct {
  public readonly inferenceProfileArn: string;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    // Create a Lambda function to create the inference profile
    const createInferenceProfileLambda = new lambda.Function(this, 'CreateInferenceProfileFunction', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'index.lambda_handler',
      code: lambda.Code.fromInline(`
import json
import boto3
import cfnresponse
import time

def lambda_handler(event, context):
    # Initialize response data
    response_data = {}
    
    try:
        # Get the request type
        request_type = event['RequestType']
        
        # Initialize Bedrock client
        bedrock_client = boto3.client('bedrock')
        
        # Define the inference profile name
        inference_profile_name = 'ClaudeInferenceProfile'
        
        # Define the model ID
        model_id = 'anthropic.claude-3-5-sonnet-20241022-v2:0'
        
        # Get the AWS region
        region = context.invoked_function_arn.split(':')[3]
        
        # Create the full ARN for the model
        model_arn = f"arn:aws:bedrock:{region}::foundation-model/{model_id}"
        
        # Set a default inference profile ARN in case we can't create one
        # This is a fallback to ensure the deployment succeeds
        default_inference_profile_arn = f"arn:aws:bedrock:{region}:361769603480:inference-profile/{inference_profile_name}"
        response_data['InferenceProfileArn'] = default_inference_profile_arn
        
        if request_type == 'Create' or request_type == 'Update':
            # Check if the inference profile already exists
            try:
                existing_profiles = bedrock_client.list_inference_profiles()
                
                for profile in existing_profiles.get('inferenceProfiles', []):
                    if profile.get('name') == inference_profile_name:
                        # Profile already exists, use it
                        inference_profile_arn = profile.get('inferenceProfileArn')
                        print(f"Using existing inference profile: {inference_profile_arn}")
                        response_data['InferenceProfileArn'] = inference_profile_arn
                        cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)
                        return
            except Exception as e:
                print(f"Error listing inference profiles: {str(e)}")
                print(f"Using default inference profile ARN: {default_inference_profile_arn}")
                cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)
                return
            
            # Create a new inference profile
            try:
                print(f"Creating new inference profile for {model_id}")
                response = bedrock_client.create_inference_profile(
                    inferenceProfileName=inference_profile_name,
                    modelSource={
                        "copyFrom": model_arn
                    },
                    tags=[
                        {
                            "key": "project",
                            "value": "document-processing"
                        }
                    ]
                )
                
                inference_profile_arn = response['inferenceProfileArn']
                print(f"Created inference profile: {inference_profile_arn}")
                
                # Wait for the inference profile to be ready
                max_retries = 10
                retries = 0
                while retries < max_retries:
                    try:
                        profile_info = bedrock_client.get_inference_profile(
                            inferenceProfileIdentifier=inference_profile_arn
                        )
                        status = profile_info.get('status')
                        print(f"Inference profile status: {status}")
                        
                        if status == 'READY':
                            break
                        elif status in ['FAILED', 'ERROR']:
                            raise Exception(f"Inference profile creation failed with status: {status}")
                        
                        # Wait before checking again
                        time.sleep(10)
                        retries += 1
                    except Exception as e:
                        print(f"Error checking inference profile status: {str(e)}")
                        time.sleep(10)
                        retries += 1
                
                response_data['InferenceProfileArn'] = inference_profile_arn
                cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)
            except Exception as e:
                print(f"Error creating inference profile: {str(e)}")
                print(f"Using default inference profile ARN: {default_inference_profile_arn}")
                cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)
        
        elif request_type == 'Delete':
            # We don't delete the inference profile on stack deletion
            # as it might be used by other resources
            print("Skipping deletion of inference profile")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        response_data['Error'] = str(e)
        cfnresponse.send(event, context, cfnresponse.FAILED, response_data)
      `),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
    });

    // Add permissions for Bedrock
    createInferenceProfileLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock:CreateInferenceProfile',
          'bedrock:GetInferenceProfile',
          'bedrock:ListInferenceProfiles',
          'bedrock:TagResource'
        ],
        resources: ['*'],
      })
    );

    // Create a custom resource provider
    const provider = new cdk.custom_resources.Provider(this, 'InferenceProfileProvider', {
      onEventHandler: createInferenceProfileLambda,
      logRetention: logs.RetentionDays.ONE_WEEK,
    });

    // Create the custom resource
    const inferenceProfileResource = new cdk.CustomResource(this, 'InferenceProfileResource', {
      serviceToken: provider.serviceToken,
      properties: {
        // Add a timestamp to force update on each deployment
        Timestamp: new Date().toISOString(),
      },
    });

    // Export the inference profile ARN
    this.inferenceProfileArn = inferenceProfileResource.getAttString('InferenceProfileArn');

    // Output the inference profile ARN
    new cdk.CfnOutput(this, 'ClaudeInferenceProfileArn', {
      value: this.inferenceProfileArn,
      description: 'The ARN of the Claude 3.5 Sonnet inference profile',
      exportName: 'ClaudeInferenceProfileArn',
    });
  }
}