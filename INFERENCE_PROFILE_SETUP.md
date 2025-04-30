# Setting Up Claude 3.5 Sonnet Inference Profile

This document provides instructions for setting up an inference profile for Claude 3.5 Sonnet, which is required for using the model with AWS Bedrock.

## Background

AWS Bedrock requires an inference profile for invoking the Claude 3.5 Sonnet model. Direct invocation with on-demand throughput is not supported, as indicated by this error:

```
Error invoking model: An error occurred (ValidationException) when calling the InvokeModel operation: Invocation of model ID anthropic.claude-3-5-sonnet-20241022-v2:0 with on-demand throughput isn't supported. Retry your request with the ID or ARN of an inference profile that contains this model.
```

## Solution

We've implemented a two-part solution:

1. Updated the Lambda function to use an inference profile ARN from environment variables
2. Provided a script to create the inference profile and update the Lambda functions

## Deployment Instructions

### Step 1: Deploy the CDK Stack

First, deploy the CDK stack to create the necessary resources:

```bash
npm run build
cdk deploy
```

### Step 2: Create the Inference Profile

After the stack is deployed, run the provided script to create the inference profile and update the Lambda functions:

```bash
./create-inference-profile.sh
```

This script will:
1. Check if an inference profile for Claude 3.5 Sonnet already exists
2. Create a new one if needed
3. Automatically find Lambda functions related to Bedrock
4. Update each Lambda function with the inference profile ARN

The script is designed to be robust and handle various edge cases:
- It properly detects existing inference profiles
- It finds all Lambda functions with "Bedrock" or "bedrock" in their name
- It preserves existing environment variables when updating Lambda functions
- It provides clear error messages if anything goes wrong

### Manual Setup (if needed)

If the script doesn't work, you can create the inference profile manually:

#### Using AWS CLI

```bash
# Get your AWS region
REGION=$(aws configure get region)

# Create the inference profile
aws bedrock create-inference-profile \
    --inference-profile-name "ClaudeInferenceProfile" \
    --model-source "copyFrom=arn:aws:bedrock:$REGION::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0" \
    --tags Key=project,Value=document-processing
```

#### Using AWS Console (Recommended)

If you encounter permission issues with the script or AWS CLI, the AWS Console is the most reliable method:

1. Go to the AWS Bedrock console: https://console.aws.amazon.com/bedrock/
2. Navigate to "Inference profiles" in the left sidebar
3. Click "Create inference profile"
4. For the model, select "Claude 3.5 Sonnet" (anthropic.claude-3-5-sonnet-20241022-v2:0)
5. Name it "ClaudeInferenceProfile"
6. Add a tag with key "project" and value "document-processing" (optional)
7. Click "Create inference profile"
8. Wait for the profile to be created (status changes to "Ready")
9. Copy the ARN of the created profile (it will look like: arn:aws:bedrock:us-east-1:123456789012:inference-profile/ClaudeInferenceProfile)

![AWS Console Inference Profile Creation](https://docs.aws.amazon.com/images/bedrock/latest/userguide/images/inference-profiles/create-inference-profile.png)

#### Update Lambda Functions

After creating the profile manually, you can update the Lambda functions with the ARN using either the AWS CLI or the AWS Console.

##### Using AWS CLI

```bash
# Replace YOUR_PROFILE_ARN with the actual ARN
PROFILE_ARN="YOUR_PROFILE_ARN"

# Find Lambda functions related to Bedrock
LAMBDA_FUNCTIONS=$(aws lambda list-functions --query "Functions[?contains(FunctionName, 'Bedrock') || contains(FunctionName, 'bedrock')].FunctionName" --output json)

# Update each Lambda function
for FUNCTION_NAME in $(echo $LAMBDA_FUNCTIONS | jq -r '.[]'); do
  # Get current environment variables
  ENV_VARS=$(aws lambda get-function-configuration --function-name $FUNCTION_NAME --query "Environment.Variables" --output json)
  
  # Add or update the CLAUDE_INFERENCE_PROFILE_ARN variable
  UPDATED_ENV_VARS=$(echo $ENV_VARS | jq --arg ARN "$PROFILE_ARN" '. + {"CLAUDE_INFERENCE_PROFILE_ARN": $ARN}')
  
  # Update the Lambda function
  aws lambda update-function-configuration \
      --function-name $FUNCTION_NAME \
      --environment "Variables=$UPDATED_ENV_VARS"
done
```

##### Using AWS Console (Easier)

1. Go to the AWS Lambda console: https://console.aws.amazon.com/lambda/
2. Search for Lambda functions with "Bedrock" or "bedrock" in their name
3. For each relevant function:
   a. Click on the function name to open its configuration page
   b. Scroll down to the "Environment variables" section
   c. Click "Edit"
   d. Add a new environment variable:
      - Key: `CLAUDE_INFERENCE_PROFILE_ARN`
      - Value: The ARN of your inference profile (copied from the Bedrock console)
   e. Click "Save"

![AWS Console Lambda Environment Variables](https://docs.aws.amazon.com/images/lambda/latest/dg/images/console-env.png)

4. Test the function to ensure it's working correctly with the inference profile

## Troubleshooting

### Permission Issues

If you encounter permission errors, ensure your IAM user or role has the following permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:CreateInferenceProfile",
        "bedrock:GetInferenceProfile",
        "bedrock:ListInferenceProfiles",
        "bedrock:TagResource",
        "bedrock:InvokeModel",
        "bedrock-runtime:InvokeModel"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "lambda:ListFunctions",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionConfiguration"
      ],
      "Resource": "*"
    }
  ]
}
```

You can attach these permissions to your IAM user or role using the AWS Console:

1. Go to the IAM console: https://console.aws.amazon.com/iam/
2. Navigate to "Users" or "Roles" depending on what you're using
3. Select your user or role
4. Click "Add permissions" or "Attach policies"
5. Create a new inline policy with the JSON above
6. Name it something like "BedrockInferenceProfileManagement"
7. Click "Create policy"

### Validation Errors

If you see validation errors, ensure you're using the correct model ARN format:

```
arn:aws:bedrock:<region>::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0
```

### Lambda Function Errors

If the Lambda function still fails after setting up the inference profile, check CloudWatch logs for detailed error messages. The Lambda function includes error handling that provides clear instructions on how to resolve issues.