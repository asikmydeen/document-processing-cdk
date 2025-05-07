import json
import os
import boto3
import uuid
from datetime import datetime

# Initialize AWS clients
s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """Lambda function to retrieve a payload from S3 and extract metadata fields."""
    print(f"Received event: {json.dumps(event)}")

    try:
        # Check if we have a payload reference
        if 'payload_reference' in event:
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

            print(f"Successfully retrieved payload from S3: {bucket}/{key}")

            # Extract the necessary fields for the next step
            # First, check if the payload has metadata
            if 'metadata' in payload:
                metadata = payload.get('metadata', {})
                processed_bucket = metadata.get('processed_bucket')
                processed_key = metadata.get('processed_key')
                document_id = metadata.get('document_id')
                metadata_id = metadata.get('id')

                if processed_bucket and processed_key:
                    return {
                        'statusCode': 200,
                        'processed_bucket': processed_bucket,
                        'processed_key': processed_key,
                        'document_id': document_id or 'unknown',
                        'metadata_id': metadata_id
                    }

            # If metadata is not available, try to extract from other fields
            if isinstance(payload, dict):
                # Try to find processed_bucket and processed_key in the payload
                processed_bucket = payload.get('processed_bucket')
                processed_key = payload.get('processed_key')
                document_id = payload.get('document_id')
                metadata_id = payload.get('metadata_id')

                # If we have the necessary fields, return them
                if processed_bucket and processed_key:
                    return {
                        'statusCode': 200,
                        'processed_bucket': processed_bucket,
                        'processed_key': processed_key,
                        'document_id': document_id or 'unknown',
                        'metadata_id': metadata_id
                    }

                # If we still don't have the fields, try to parse the body if it's a string
                body = payload.get('body')
                if isinstance(body, str):
                    try:
                        body_json = json.loads(body)
                        processed_bucket = body_json.get('processed_bucket')
                        processed_key = body_json.get('processed_key')
                        document_id = body_json.get('document_id')
                        metadata_id = body_json.get('metadata_id')

                        if processed_bucket and processed_key:
                            return {
                                'statusCode': 200,
                                'processed_bucket': processed_bucket,
                                'processed_key': processed_key,
                                'document_id': document_id or 'unknown',
                                'metadata_id': metadata_id
                            }
                    except Exception as json_error:
                        print(f"Error parsing body JSON: {str(json_error)}")

            # If we still don't have the fields, return an error
            return {
                'statusCode': 400,
                'body': json.dumps('Could not extract processed_bucket and processed_key from payload')
            }

        # If we don't have a payload reference, check if we have the fields directly
        processed_bucket = event.get('processed_bucket')
        processed_key = event.get('processed_key')
        document_id = event.get('document_id')
        metadata_id = event.get('metadata_id')

        if processed_bucket and processed_key:
            return {
                'statusCode': 200,
                'processed_bucket': processed_bucket,
                'processed_key': processed_key,
                'document_id': document_id or 'unknown',
                'metadata_id': metadata_id
            }

        # If we still don't have the fields, return an error
        return {
            'statusCode': 400,
            'body': json.dumps('Missing processed_bucket and processed_key in event')
        }

    except Exception as e:
        print(f"Error retrieving payload: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error retrieving payload: {str(e)}')
        }
