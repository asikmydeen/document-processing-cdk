import json
import os
import boto3
import uuid
from datetime import datetime

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """Lambda function to extract and store metadata in DynamoDB."""
    print(f"Received event: {json.dumps(event)}")

    # Get the processed document information directly from the event
    processed_bucket = event.get('processed_bucket')
    processed_key = event.get('processed_key')

    # Check if processed_key is a reference to a payload in S3
    if processed_key and processed_key.startswith('payloads/'):
        try:
            print(f"Attempting to retrieve payload from S3: {processed_bucket}/{processed_key}")

            # List objects in the bucket to debug
            try:
                list_response = s3_client.list_objects_v2(
                    Bucket=processed_bucket,
                    Prefix='payloads/'
                )
                if 'Contents' in list_response:
                    print(f"Found {len(list_response['Contents'])} objects with prefix 'payloads/'")
                    for obj in list_response['Contents'][:5]:  # Print first 5 for debugging
                        print(f"  - {obj['Key']}")
                else:
                    print("No objects found with prefix 'payloads/'")
            except Exception as list_error:
                print(f"Error listing objects: {str(list_error)}")

            # Try to retrieve the payload from the payload bucket
            try:
                # Get the payload bucket name from environment variables
                payload_bucket = os.environ.get('PAYLOAD_BUCKET_NAME')
                if payload_bucket:
                    print(f"Trying payload bucket: {payload_bucket}")
                    response = s3_client.get_object(Bucket=payload_bucket, Key=processed_key)
                    payload = json.loads(response['Body'].read().decode('utf-8'))
                    print(f"Successfully retrieved payload from payload bucket")
                else:
                    # Try the processed bucket
                    response = s3_client.get_object(Bucket=processed_bucket, Key=processed_key)
                    payload = json.loads(response['Body'].read().decode('utf-8'))
                    print(f"Successfully retrieved payload from processed bucket")

                # Extract the processed_key from the payload
                if isinstance(payload, dict) and 'body' in payload:
                    body_str = payload['body']
                    if isinstance(body_str, str):
                        try:
                            body_json = json.loads(body_str)
                            new_processed_key = body_json.get('processed_key')
                            if new_processed_key:
                                processed_key = new_processed_key
                                print(f"Extracted processed_key from body: {processed_key}")
                        except Exception as json_error:
                            print(f"Error parsing body JSON: {str(json_error)}")

                print(f"Final processed_key: {processed_key}")
            except Exception as get_error:
                print(f"Error retrieving payload: {str(get_error)}")
                # Continue with the original processed_key
                print(f"Continuing with original processed_key: {processed_key}")
        except Exception as e:
            print(f"Error in payload retrieval process: {str(e)}")
            # Don't fail the function, try to continue with the original processed_key
            print(f"Continuing with original processed_key: {processed_key}")

    if not processed_bucket or not processed_key:
        return {
            'statusCode': 400,
            'body': json.dumps('Missing processed_bucket or processed_key parameter')
        }

    try:
        # Get the processed document from S3
        response = s3_client.get_object(Bucket=processed_bucket, Key=processed_key)
        document_content = json.loads(response['Body'].read().decode('utf-8'))

        # Extract metadata
        metadata = document_content.get('metadata', {})
        document_content_data = document_content.get('document_content', {})

        # Get the DynamoDB table
        table_name = os.environ.get('METADATA_TABLE_NAME')
        table = dynamodb.Table(table_name)

        # Create a unique document ID if not present
        document_id = document_content.get('document_id', processed_key)
        if '/' in document_id:
            # Extract just the filename part if it's a path
            document_id = document_id.split('/')[-1]

        # Check if the document is an image
        is_image = document_content_data.get('is_image', False)
        images = document_content_data.get('images', [])

        # Prepare the item for DynamoDB
        item = {
            'id': str(uuid.uuid4()),  # Primary key
            'document_id': document_id,
            'original_bucket': document_content.get('original_bucket', ''),
            'original_key': document_content.get('original_key', ''),
            'processed_bucket': processed_bucket,
            'processed_key': processed_key,
            'document_type': document_content_data.get('document_type', ''),
            'title': metadata.get('title', 'Unknown Document'),
            'summary': metadata.get('summary', ''),
            'topics': metadata.get('topics', []),
            'category': metadata.get('category', 'unknown'),
            'entities': metadata.get('entities', {}),
            'has_text': bool(document_content_data.get('text_content', '')),
            'has_tables': len(document_content_data.get('tables', [])) > 0,
            'has_forms': len(document_content_data.get('forms', [])) > 0,
            'is_image': is_image,
            'image_description': metadata.get('image_description', ''),
            'images': images,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'status': 'PROCESSED'
        }

        # Add the item to DynamoDB
        table.put_item(Item=item)

        # Create search indices for the document
        search_indices = create_search_indices(item, document_content_data)

        # If this is an image, create special image search indices
        if is_image or images:
            image_search_indices = create_image_search_indices(item, document_content_data)
            search_indices.extend(image_search_indices)

        # Prepare success response
        response_data = {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Metadata extracted and stored successfully',
                'document_id': document_id,
                'metadata_id': item['id'],
                'search_indices': len(search_indices),
                'is_image': is_image,
                'images': images
            }),
            'metadata': item,
            'search_indices': search_indices
        }

        # Estimate the size of the response
        response_size = len(json.dumps(response_data))
        print(f"Estimated response size: {response_size} bytes")

        # If the response is too large (over 200KB), store it in S3
        if response_size > 200000:
            try:
                # Get the payload bucket name
                payload_bucket = os.environ.get('PAYLOAD_BUCKET_NAME')
                if not payload_bucket:
                    print("PAYLOAD_BUCKET_NAME environment variable not set")
                    return response_data

                # Generate a unique key for the payload
                payload_key = f"payloads/{datetime.now().strftime('%Y-%m-%d')}/{str(uuid.uuid4())}.json"

                # Store the payload in S3
                s3_client.put_object(
                    Bucket=payload_bucket,
                    Key=payload_key,
                    Body=json.dumps(response_data),
                    ContentType='application/json'
                )

                print(f"Stored large response in S3: {payload_bucket}/{payload_key}")

                # Return a reference to the stored payload
                return {
                    'statusCode': 200,
                    'payload_reference': {
                        'bucket': payload_bucket,
                        'key': payload_key
                    },
                    'metadata': {
                        'document_id': document_id,
                        'processed_bucket': processed_bucket,
                        'processed_key': processed_key
                    }
                }
            except Exception as e:
                print(f"Error storing large response in S3: {str(e)}")
                # Fall back to returning the full response
                return response_data

        # Return the metadata and search indices directly if not too large
        return response_data

    except Exception as e:
        print(f"Error extracting metadata: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error extracting metadata: {str(e)}',
                'processed_key': processed_key
            })
        }

def create_search_indices(metadata_item, document_content):
    """Create search indices for the document to improve searchability."""
    # Get the DynamoDB table for search indices
    table_name = os.environ.get('SEARCH_INDEX_TABLE_NAME', os.environ.get('METADATA_TABLE_NAME'))
    table = dynamodb.Table(table_name)

    search_indices = []

    # Add basic metadata as search indices
    indices = [
        {'type': 'title', 'value': metadata_item.get('title', '')},
        {'type': 'category', 'value': metadata_item.get('category', '')},
    ]

    # Add topics as search indices
    for topic in metadata_item.get('topics', []):
        indices.append({'type': 'topic', 'value': topic})

    # Add entities as search indices
    entities = metadata_item.get('entities', {})
    for entity_type, entity_list in entities.items():
        for entity in entity_list:
            indices.append({'type': f'entity_{entity_type}', 'value': entity})

    # Create and store each search index
    for index in indices:
        if not index['value']:
            continue

        index_item = {
            'id': str(uuid.uuid4()),
            'document_id': metadata_item['document_id'],
            'metadata_id': metadata_item['id'],
            'index_type': index['type'],
            'index_value': index['value'],
            'created_at': datetime.now().isoformat()
        }

        # Add the search index to DynamoDB
        table.put_item(Item=index_item)
        search_indices.append(index_item)

    return search_indices

def create_image_search_indices(metadata_item, document_content):
    """Create special search indices for images to enable image retrieval by text content."""
    # Get the DynamoDB table for search indices
    table_name = os.environ.get('SEARCH_INDEX_TABLE_NAME', os.environ.get('METADATA_TABLE_NAME'))
    table = dynamodb.Table(table_name)

    search_indices = []

    # If this is an image document, create indices for the text content
    if metadata_item.get('is_image', False):
        # Create a special image content index
        index_item = {
            'id': str(uuid.uuid4()),
            'document_id': metadata_item['document_id'],
            'metadata_id': metadata_item['id'],
            'index_type': 'image_content',
            'index_value': document_content.get('text_content', '')[:1000],  # Limit to 1000 chars
            'image_s3_uri': f"s3://{metadata_item['original_bucket']}/{metadata_item['original_key']}",
            'image_description': metadata_item.get('image_description', ''),
            'created_at': datetime.now().isoformat()
        }

        # Add the search index to DynamoDB
        table.put_item(Item=index_item)
        search_indices.append(index_item)

    # For documents with embedded images, create indices for each image
    for image in metadata_item.get('images', []):
        index_item = {
            'id': str(uuid.uuid4()),
            'document_id': metadata_item['document_id'],
            'metadata_id': metadata_item['id'],
            'index_type': 'embedded_image',
            'index_value': image.get('text_content', '')[:1000],  # Limit to 1000 chars
            'image_s3_uri': image.get('s3_uri', ''),
            'image_description': metadata_item.get('image_description', ''),
            'created_at': datetime.now().isoformat()
        }

        # Add the search index to DynamoDB
        table.put_item(Item=index_item)
        search_indices.append(index_item)

    return search_indices
