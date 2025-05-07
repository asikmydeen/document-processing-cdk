import json
import boto3
import os
import urllib.parse
import sys

# Check if we're running in a Lambda environment
IN_LAMBDA = os.environ.get('AWS_LAMBDA_FUNCTION_NAME') is not None

def lambda_handler(event, context):
    """Lambda function to trigger the document processing state machine."""
    print(f"Received event: {json.dumps(event)}")
    
    try:
        # Get the S3 bucket and key from the event
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        
        # Log information about the file being processed
        print(f"Processing file: s3://{bucket}/{key}")
        
        # Check if the file is a PDF and log that we'll be extracting images
        if key.lower().endswith('.pdf'):
            print(f"PDF file detected: {key}. Image extraction will be attempted during processing.")
        
        # Start the Step Functions state machine
        client = boto3.client('stepfunctions')
        response = client.start_execution(
            stateMachineArn=os.environ['STATE_MACHINE_ARN'],
            input=json.dumps({
                'bucket': bucket,
                'key': key,
                'enable_image_extraction': True  # Flag to enable image extraction
            })
        )
        
        print(f"Started state machine execution: {response['executionArn']}")
        
        return {
            'statusCode': 200,
            'body': json.dumps('Started document processing state machine')
        }
    except Exception as e:
        print(f"Error triggering state machine: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error triggering state machine: {str(e)}')
        }