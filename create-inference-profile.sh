#!/bin/bash

# Script to create an inference profile for Claude 3.5 Sonnet and update Lambda functions

# Get the AWS region
REGION=$(aws configure get region)
if [ -z "$REGION" ]; then
    REGION="us-east-1"
    echo "No region found in AWS config, using default: $REGION"
else
    echo "Using region from AWS config: $REGION"
fi

# Define the model ID
MODEL_ID="anthropic.claude-3-5-sonnet-20241022-v2:0"

# Create the full ARN for the model
MODEL_ARN="arn:aws:bedrock:$REGION::foundation-model/$MODEL_ID"

# Define the inference profile name
PROFILE_NAME="ClaudeInferenceProfile"

echo "Creating inference profile for $MODEL_ID in region $REGION"
echo "Model ARN: $MODEL_ARN"

# Check if the inference profile already exists
echo "Checking if inference profile already exists..."
EXISTING_PROFILES=$(aws bedrock list-inference-profiles 2>/dev/null)
if [ $? -eq 0 ]; then
    EXISTING_PROFILE=$(echo $EXISTING_PROFILES | jq -r --arg NAME "$PROFILE_NAME" '.inferenceProfiles[] | select(.name==$NAME) | .inferenceProfileArn' 2>/dev/null)
    if [ -n "$EXISTING_PROFILE" ] && [ "$EXISTING_PROFILE" != "null" ]; then
        echo "Inference profile already exists: $EXISTING_PROFILE"
        PROFILE_ARN=$EXISTING_PROFILE
    else
        echo "No existing inference profile found with name: $PROFILE_NAME"
        EXISTING_PROFILE=""
    fi
else
    echo "Error listing inference profiles. Check your AWS credentials and permissions."
    echo "You may need to create the inference profile manually using the AWS console."
    EXISTING_PROFILE=""
fi

if [ -z "$EXISTING_PROFILE" ]; then
    # Create the inference profile
    echo "Creating new inference profile..."
    RESPONSE=$(aws bedrock create-inference-profile \
        --inference-profile-name "$PROFILE_NAME" \
        --model-source "copyFrom=$MODEL_ARN" \
        --tags Key=project,Value=document-processing 2>/dev/null)
    
    if [ $? -eq 0 ]; then
        # Extract the inference profile ARN
        PROFILE_ARN=$(echo $RESPONSE | jq -r '.inferenceProfileArn')
        
        if [ -n "$PROFILE_ARN" ] && [ "$PROFILE_ARN" != "null" ]; then
            echo "Successfully created inference profile: $PROFILE_ARN"
            
            # Wait for the inference profile to be ready
            echo "Waiting for inference profile to be ready..."
            aws bedrock wait inference-profile-available --inference-profile-identifier "$PROFILE_ARN" 2>/dev/null
            if [ $? -eq 0 ]; then
                echo "Inference profile is now ready to use."
            else
                echo "Warning: Could not confirm if inference profile is ready. Continuing anyway."
            fi
        else
            echo "Failed to extract inference profile ARN from response."
            echo "Response: $RESPONSE"
            echo "You may need to create the inference profile manually using the AWS console."
            exit 1
        fi
    else
        echo "Failed to create inference profile. Check your AWS credentials and permissions."
        echo "You may need to create the inference profile manually using the AWS console."
        exit 1
    fi
fi

# Find Lambda functions related to Bedrock knowledge base
echo "Finding Lambda functions related to Bedrock knowledge base..."
LAMBDA_FUNCTIONS=$(aws lambda list-functions --query "Functions[?contains(FunctionName, 'Bedrock') || contains(FunctionName, 'bedrock')].FunctionName" --output json)

if [ -z "$LAMBDA_FUNCTIONS" ] || [ "$LAMBDA_FUNCTIONS" == "[]" ]; then
    echo "No Lambda functions found with 'Bedrock' or 'bedrock' in the name."
    echo "You will need to update the Lambda functions manually with the inference profile ARN:"
    echo "CLAUDE_INFERENCE_PROFILE_ARN=$PROFILE_ARN"
    exit 0
fi

echo "Found the following Lambda functions:"
echo $LAMBDA_FUNCTIONS | jq -r '.[]'

# Update each Lambda function with the inference profile ARN
echo "Updating Lambda functions with the inference profile ARN..."
for FUNCTION_NAME in $(echo $LAMBDA_FUNCTIONS | jq -r '.[]'); do
    echo "Updating $FUNCTION_NAME..."
    
    # Get current environment variables
    ENV_VARS=$(aws lambda get-function-configuration --function-name $FUNCTION_NAME --query "Environment.Variables" --output json 2>/dev/null)
    
    if [ $? -ne 0 ] || [ -z "$ENV_VARS" ] || [ "$ENV_VARS" == "null" ]; then
        echo "No environment variables found for $FUNCTION_NAME or error getting configuration."
        echo "Setting only the inference profile ARN..."
        aws lambda update-function-configuration \
            --function-name $FUNCTION_NAME \
            --environment "Variables={CLAUDE_INFERENCE_PROFILE_ARN=$PROFILE_ARN}"
    else
        # Add or update the CLAUDE_INFERENCE_PROFILE_ARN variable
        UPDATED_ENV_VARS=$(echo $ENV_VARS | jq --arg ARN "$PROFILE_ARN" '. + {"CLAUDE_INFERENCE_PROFILE_ARN": $ARN}')
        
        # Update the Lambda function
        aws lambda update-function-configuration \
            --function-name $FUNCTION_NAME \
            --environment "Variables=$UPDATED_ENV_VARS"
    fi
    
    if [ $? -eq 0 ]; then
        echo "Successfully updated $FUNCTION_NAME"
    else
        echo "Failed to update $FUNCTION_NAME"
    fi
done

echo "Setup complete!"
echo "Inference profile ARN: $PROFILE_ARN"
echo "Lambda functions have been updated with the inference profile ARN."