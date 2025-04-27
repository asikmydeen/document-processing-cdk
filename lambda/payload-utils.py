import json
import os
import boto3
import uuid
from datetime import datetime

# Initialize AWS clients
s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """Lambda function to store and retrieve large payloads in S3."""
    print(f"Received event: {json.dumps(event)}")
    
    # Get the operation from the event
    operation = event.get('operation', 'store')
    
    if operation == 'store':
        return store_payload(event)
    elif operation == 'retrieve':
        return retrieve_payload(event)
    else:
        return {
            'statusCode': 400,
            'body': json.dumps(f'Unsupported operation: {operation}')
        }

def store_payload(event):
    """Store a large payload in S3 and return a reference."""
    try:
        # Get the payload to store
        payload = event.get('payload', {})
        
        # Get the S3 bucket for storing payloads
        payload_bucket = os.environ.get('PAYLOAD_BUCKET_NAME')
        if not payload_bucket:
            return {
                'statusCode': 500,
                'body': json.dumps('PAYLOAD_BUCKET_NAME environment variable not set')
            }
        
        # Generate a unique key for the payload
        payload_key = f"payloads/{datetime.now().strftime('%Y-%m-%d')}/{str(uuid.uuid4())}.json"
        
        # Store the payload in S3
        s3_client.put_object(
            Bucket=payload_bucket,
            Key=payload_key,
            Body=json.dumps(payload),
            ContentType='application/json'
        )
        
        # Return a reference to the stored payload
        return {
            'statusCode': 200,
            'payload_reference': {
                'bucket': payload_bucket,
                'key': payload_key
            },
            'original_status_code': payload.get('statusCode', 200) if isinstance(payload, dict) else 200
        }
    
    except Exception as e:
        print(f"Error storing payload: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error storing payload: {str(e)}')
        }

def retrieve_payload(event):
    """Retrieve a payload from S3 using a reference."""
    try:
        # Get the payload reference
        payload_reference = event.get('payload_reference', {})
        bucket = payload_reference.get('bucket')
        key = payload_reference.get('key')
        
        if not bucket or not key:
            return {
                'statusCode': 400,
                'body': json.dumps('Missing bucket or key in payload_reference')
            }
        
        # Retrieve the payload from S3
        response = s3_client.get_object(Bucket=bucket, Key=key)
        payload = json.loads(response['Body'].read().decode('utf-8'))
        
        # Return the retrieved payload
        return {
            'statusCode': 200,
            'payload': payload
        }
    
    except Exception as e:
        print(f"Error retrieving payload: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error retrieving payload: {str(e)}')
        }
