import json
import os
import boto3
import uuid
from datetime import datetime

# Initialize AWS clients
bedrock = boto3.client('bedrock')
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """Lambda function to create and manage Bedrock knowledge base."""
    print(f"Received event: {json.dumps(event)}")

    # Get the operation from the event
    operation = event.get('operation', 'create_knowledge_base')

    if operation == 'create_knowledge_base':
        return create_knowledge_base(event)
    elif operation == 'add_document_to_knowledge_base':
        return add_document_to_knowledge_base(event)
    elif operation == 'query_knowledge_base':
        return query_knowledge_base(event)
    else:
        return {
            'statusCode': 400,
            'body': json.dumps(f'Unsupported operation: {operation}')
        }

def create_knowledge_base(event):
    """Create a new Bedrock knowledge base."""
    try:
        # Get the knowledge base name from the event or use a default
        kb_name = event.get('knowledge_base_name', 'DocumentProcessingKnowledgeBase')

        # Get the S3 bucket for the knowledge base data source
        processed_bucket = os.environ.get('PROCESSED_BUCKET_NAME')

        # Create the knowledge base
        response = bedrock.create_knowledge_base(
            name=kb_name,
            description='Knowledge base for processed documents',
            roleArn=os.environ.get('KNOWLEDGE_BASE_ROLE_ARN'),
            knowledgeBaseConfiguration={
                'type': 'VECTOR',
                'vectorKnowledgeBaseConfiguration': {
                    'embeddingModelArn': 'arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1'
                }
            }
        )

        # Get the knowledge base ID
        kb_id = response['knowledgeBase']['knowledgeBaseId']

        # Create a data source for the knowledge base
        data_source_response = bedrock.create_data_source(
            knowledgeBaseId=kb_id,
            name=f"{kb_name}DataSource",
            description='S3 data source for processed documents',
            dataSourceConfiguration={
                'type': 'S3',
                's3Configuration': {
                    'bucketArn': f"arn:aws:s3:::{processed_bucket}",
                    'inclusionPrefixes': ['']  # Include all objects
                }
            },
            vectorIngestionConfiguration={
                'chunkingConfiguration': {
                    'chunkingStrategy': 'FIXED_SIZE',
                    'fixedSizeChunkingConfiguration': {
                        'maxTokens': 300,
                        'overlapPercentage': 10
                    }
                }
            }
        )

        # Get the data source ID
        ds_id = data_source_response['dataSource']['dataSourceId']

        # Store the knowledge base and data source IDs in DynamoDB
        table_name = os.environ.get('METADATA_TABLE_NAME')
        table = dynamodb.Table(table_name)

        table.put_item(Item={
            'id': str(uuid.uuid4()),
            'document_id': 'KNOWLEDGE_BASE_CONFIG',
            'knowledge_base_id': kb_id,
            'data_source_id': ds_id,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'status': 'CREATED'
        })

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Knowledge base created successfully',
                'knowledge_base_id': kb_id,
                'data_source_id': ds_id
            })
        }

    except Exception as e:
        print(f"Error creating knowledge base: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error creating knowledge base: {str(e)}'
            })
        }

def add_document_to_knowledge_base(event):
    """Add a document to the Bedrock knowledge base."""
    try:
        # Get the document information directly from the event
        processed_bucket = event.get('processed_bucket')
        processed_key = event.get('processed_key')

        # Check if processed_key is a reference to a payload in S3
        if processed_key and processed_key.startswith('payloads/'):
            try:
                # Retrieve the payload from S3
                response = s3_client.get_object(Bucket=processed_bucket, Key=processed_key)
                payload = json.loads(response['Body'].read().decode('utf-8'))

                # Extract the processed_key from the payload
                if isinstance(payload, dict) and 'metadata' in payload:
                    processed_key = payload['metadata'].get('processed_key')
                elif isinstance(payload, dict) and 'body' in payload:
                    body_str = payload['body']
                    if isinstance(body_str, str):
                        try:
                            body_json = json.loads(body_str)
                            processed_key = body_json.get('processed_key')
                        except:
                            pass

                print(f"Retrieved processed_key from S3: {processed_key}")
            except Exception as e:
                print(f"Error retrieving payload from S3: {str(e)}")
                return {
                    'statusCode': 500,
                    'body': json.dumps(f'Error retrieving payload from S3: {str(e)}')
                }

        if not processed_bucket or not processed_key:
            return {
                'statusCode': 400,
                'body': json.dumps('Missing processed_bucket or processed_key parameter')
            }

        # Get the knowledge base configuration from DynamoDB
        table_name = os.environ.get('METADATA_TABLE_NAME')
        table = dynamodb.Table(table_name)

        response = table.query(
            IndexName='DocumentIdIndex',
            KeyConditionExpression='document_id = :did',
            ExpressionAttributeValues={
                ':did': 'KNOWLEDGE_BASE_CONFIG'
            }
        )

        if not response['Items']:
            return {
                'statusCode': 404,
                'body': json.dumps('Knowledge base configuration not found')
            }

        kb_config = response['Items'][0]
        kb_id = kb_config['knowledge_base_id']
        ds_id = kb_config['data_source_id']

        # Start an ingestion job for the document
        ingestion_response = bedrock.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            description=f'Ingestion job for {processed_key}'
        )

        job_id = ingestion_response['ingestionJob']['ingestionJobId']

        # Update the document metadata with the ingestion job ID
        document_id = os.path.splitext(os.path.basename(processed_key))[0]

        response = table.query(
            IndexName='DocumentIdIndex',
            KeyConditionExpression='document_id = :did',
            ExpressionAttributeValues={
                ':did': document_id
            }
        )

        if response['Items']:
            metadata_item = response['Items'][0]

            table.update_item(
                Key={
                    'id': metadata_item['id'],
                    'document_id': metadata_item['document_id']
                },
                UpdateExpression='SET ingestion_job_id = :jid, updated_at = :ua, kb_status = :st',
                ExpressionAttributeValues={
                    ':jid': job_id,
                    ':ua': datetime.now().isoformat(),
                    ':st': 'INGESTING'
                }
            )

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Document ingestion started',
                'knowledge_base_id': kb_id,
                'data_source_id': ds_id,
                'ingestion_job_id': job_id
            })
        }

    except Exception as e:
        print(f"Error adding document to knowledge base: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error adding document to knowledge base: {str(e)}'
            })
        }

def query_knowledge_base(event):
    """Query the Bedrock knowledge base."""
    try:
        # Get the query from the event
        query = event.get('query')

        if not query:
            return {
                'statusCode': 400,
                'body': json.dumps('Missing query parameter')
            }

        # Get the knowledge base configuration from DynamoDB
        table_name = os.environ.get('METADATA_TABLE_NAME')
        metadata_table = dynamodb.Table(table_name)

        response = metadata_table.query(
            IndexName='DocumentIdIndex',
            KeyConditionExpression='document_id = :did',
            ExpressionAttributeValues={
                ':did': 'KNOWLEDGE_BASE_CONFIG'
            }
        )

        if not response['Items']:
            return {
                'statusCode': 404,
                'body': json.dumps('Knowledge base configuration not found')
            }

        kb_config = response['Items'][0]
        kb_id = kb_config['knowledge_base_id']

        # Query the knowledge base
        retrieve_response = bedrock.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={
                'text': query
            },
            numberOfResults=5
        )

        # Get the retrieval results
        retrieval_results = retrieve_response['retrievalResults']

        # Get the search index table
        search_index_table_name = os.environ.get('SEARCH_INDEX_TABLE_NAME', table_name)
        search_index_table = dynamodb.Table(search_index_table_name)

        # Find relevant images based on the query
        relevant_images = find_relevant_images(query, search_index_table)

        # Use Bedrock to generate a response based on the retrieved content
        bedrock_runtime = boto3.client('bedrock-runtime')

        # Prepare the context from retrieved documents
        context = ""
        for result in retrieval_results:
            content = result['content']['text']
            source = result.get('location', {}).get('s3Location', {}).get('uri', 'Unknown source')
            context += f"Source: {source}\nContent: {content}\n\n"

        # Generate a response using Claude
        prompt = f"""
        Human: I have the following question: {query}

        Here is some context that might help you answer:

        {context}

        Please provide a comprehensive answer based on the context provided. If the context doesn't contain enough information to answer the question, please say so. Include references to the sources in your answer.

        Assistant:
        """

        response = bedrock_runtime.invoke_model(
            modelId='anthropic.claude-v2',
            body=json.dumps({
                'prompt': prompt,
                'max_tokens_to_sample': 4000,
                'temperature': 0.1
            })
        )

        # Parse the response
        response_body = json.loads(response['body'].read())
        answer = response_body.get('completion', '')

        # Prepare the response with both text answer and relevant images
        return {
            'statusCode': 200,
            'body': json.dumps({
                'query': query,
                'answer': answer,
                'sources': [result.get('location', {}).get('s3Location', {}).get('uri', 'Unknown source') for result in retrieval_results],
                'relevant_images': relevant_images
            })
        }

    except Exception as e:
        print(f"Error querying knowledge base: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error querying knowledge base: {str(e)}'
            })
        }

def find_relevant_images(query, search_index_table):
    """Find images that are relevant to the query based on their text content."""
    try:
        # First, scan for image content indices
        image_indices = []

        # Scan for image_content indices
        response = search_index_table.scan(
            FilterExpression='attribute_exists(image_s3_uri) AND index_type = :type',
            ExpressionAttributeValues={
                ':type': 'image_content'
            }
        )
        image_indices.extend(response.get('Items', []))

        # Scan for embedded_image indices
        response = search_index_table.scan(
            FilterExpression='attribute_exists(image_s3_uri) AND index_type = :type',
            ExpressionAttributeValues={
                ':type': 'embedded_image'
            }
        )
        image_indices.extend(response.get('Items', []))

        # Filter images based on query relevance
        relevant_images = []
        for index in image_indices:
            # Simple relevance check: if query terms appear in the image text content
            index_value = index.get('index_value', '').lower()
            if any(term.lower() in index_value for term in query.split()):
                # Get the image details
                image_info = {
                    'image_s3_uri': index.get('image_s3_uri', ''),
                    'document_id': index.get('document_id', ''),
                    'image_description': index.get('image_description', ''),
                    'text_content_preview': index_value[:100] + '...' if len(index_value) > 100 else index_value
                }

                # Generate a presigned URL for the image if it's in S3
                if image_info['image_s3_uri'].startswith('s3://'):
                    parts = image_info['image_s3_uri'].replace('s3://', '').split('/', 1)
                    if len(parts) == 2:
                        bucket, key = parts
                        # Verify that both bucket and key are non-empty
                        if bucket and key:
                            try:
                                presigned_url = s3_client.generate_presigned_url(
                                    'get_object',
                                    Params={'Bucket': bucket, 'Key': key},
                                    ExpiresIn=3600  # URL valid for 1 hour
                                )
                                image_info['presigned_url'] = presigned_url
                            except Exception as e:
                                print(f"Error generating presigned URL for {image_info['image_s3_uri']}: {str(e)}")
                        else:
                            print(f"Warning: Empty bucket or key in S3 URI: {image_info['image_s3_uri']}")

                relevant_images.append(image_info)

        return relevant_images

    except Exception as e:
        print(f"Error finding relevant images: {str(e)}")
        return []
