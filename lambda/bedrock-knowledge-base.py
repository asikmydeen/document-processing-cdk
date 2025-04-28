import json
import os
import boto3
import uuid
from datetime import datetime

# Initialize AWS clients
def get_bedrock_clients():
    """Initialize the correct Bedrock clients based on what's available in the region."""
    bedrock_client = None
    bedrock_agent_client = None

    # Try to create the bedrock-agent client first
    try:
        bedrock_agent_client = boto3.client('bedrock-agent')
        print("Successfully created 'bedrock-agent' client")
    except Exception as e:
        print(f"Error creating bedrock-agent client: {str(e)}")
        try:
            # Fall back to bedrock client for regions that use this API instead
            bedrock_client = boto3.client('bedrock')
            bedrock_agent_client = bedrock_client  # Use the same client for both
            print("Using 'bedrock' client for agent functions")
        except Exception as e2:
            print(f"Error creating bedrock client: {str(e2)}")
            raise Exception("Failed to create any Bedrock client")

    # Create the runtime client for model invocation
    bedrock_runtime = boto3.client('bedrock-runtime')

    return bedrock_agent_client, bedrock_runtime

# Get the clients
try:
    bedrock_agent, bedrock_runtime = get_bedrock_clients()
except Exception as e:
    print(f"Failed to initialize Bedrock clients: {str(e)}")
    # Define fallback values that will cause explicit errors if used
    bedrock_agent = None
    bedrock_runtime = None

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
    if bedrock_agent is None:
        return {
            'statusCode': 500,
            'body': json.dumps('Bedrock clients not properly initialized')
        }

    try:
        # Get the knowledge base name from the event or use a default
        kb_name = event.get('knowledge_base_name', 'DocumentProcessingKnowledgeBase')

        # Get the S3 bucket for the knowledge base data source
        processed_bucket = os.environ.get('PROCESSED_BUCKET_NAME')

        # Create the knowledge base
        try:
            print(f"Attempting to create knowledge base: {kb_name}")
            # Get the Kendra index ID
            kendra_index_id = os.environ.get('KENDRA_INDEX_ID')
            if not kendra_index_id:
                print("KENDRA_INDEX_ID environment variable not set")
                return {
                    'statusCode': 500,
                    'body': json.dumps('KENDRA_INDEX_ID environment variable not set')
                }

            response = bedrock_agent.create_knowledge_base(
                name=kb_name,
                description='Knowledge base for processed documents',
                roleArn=os.environ.get('KNOWLEDGE_BASE_ROLE_ARN'),
                knowledgeBaseConfiguration={
                    'type': 'KENDRA',
                    'kendraKnowledgeBaseConfiguration': {
                        'indexId': kendra_index_id
                    }
                }
            )
        except Exception as kb_error:
            print(f"Error in create_knowledge_base call: {str(kb_error)}")
            raise kb_error

        # Get the knowledge base ID
        kb_id = response['knowledgeBase']['knowledgeBaseId']
        print(f"Created knowledge base with ID: {kb_id}")

        # Create a data source for the knowledge base
        try:
            print(f"Creating data source for knowledge base: {kb_id}")
            data_source_response = bedrock_agent.create_data_source(
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
        except Exception as ds_error:
            print(f"Error in create_data_source call: {str(ds_error)}")
            raise ds_error

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
    if bedrock_agent is None:
        return {
            'statusCode': 500,
            'body': json.dumps('Bedrock clients not properly initialized')
        }

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

        # If knowledge base configuration not found, create it
        if not response['Items']:
            print("Knowledge base configuration not found. Creating new knowledge base...")

            # Get parameters from environment variables
            kb_name = 'DocumentProcessingKnowledgeBase'
            kb_role_arn = os.environ.get('KNOWLEDGE_BASE_ROLE_ARN')
            kendra_index_id = os.environ.get('KENDRA_INDEX_ID')

            if not kb_role_arn:
                return {
                    'statusCode': 500,
                    'body': json.dumps('KNOWLEDGE_BASE_ROLE_ARN environment variable not set')
                }

            if not kendra_index_id:
                return {
                    'statusCode': 500,
                    'body': json.dumps('KENDRA_INDEX_ID environment variable not set')
                }

            # Create the knowledge base
            print(f"Creating knowledge base: {kb_name}")
            try:
                kb_response = bedrock_agent.create_knowledge_base(
                    name=kb_name,
                    description='Knowledge base for processed documents',
                    roleArn=kb_role_arn,
                    knowledgeBaseConfiguration={
                        'type': 'KENDRA',
                        'kendraKnowledgeBaseConfiguration': {
                            'indexId': kendra_index_id
                        }
                    }
                )

                # Get the knowledge base ID
                kb_id = kb_response['knowledgeBase']['knowledgeBaseId']
                print(f"Knowledge base created with ID: {kb_id}")

                # Create a data source for the knowledge base
                print(f"Creating data source for knowledge base: {kb_id}")
                data_source_response = bedrock_agent.create_data_source(
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
                print(f"Data source created with ID: {ds_id}")

                # Generate a unique ID for the config
                kb_config_id = str(uuid.uuid4())

                # Store the knowledge base configuration in DynamoDB
                print(f"Storing knowledge base configuration in DynamoDB")
                table.put_item(Item={
                    'id': kb_config_id,
                    'document_id': 'KNOWLEDGE_BASE_CONFIG',
                    'knowledge_base_id': kb_id,
                    'data_source_id': ds_id,
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat(),
                    'status': 'CREATED'
                })

                # Query again to get the newly created configuration
                response = table.query(
                    IndexName='DocumentIdIndex',
                    KeyConditionExpression='document_id = :did',
                    ExpressionAttributeValues={
                        ':did': 'KNOWLEDGE_BASE_CONFIG'
                    }
                )

                if not response['Items']:
                    return {
                        'statusCode': 500,
                        'body': json.dumps('Failed to create knowledge base configuration')
                    }

                print("Successfully created and stored knowledge base configuration")

            except Exception as kb_error:
                print(f"Error creating knowledge base: {str(kb_error)}")
                return {
                    'statusCode': 500,
                    'body': json.dumps(f'Error creating knowledge base: {str(kb_error)}')
                }

        # Now we should have a valid knowledge base configuration
        kb_config = response['Items'][0]
        kb_id = kb_config['knowledge_base_id']
        ds_id = kb_config['data_source_id']

        # Start an ingestion job for the document
        try:
            print(f"Starting ingestion job for knowledge base: {kb_id}, data source: {ds_id}")
            ingestion_response = bedrock_agent.start_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id,
                description=f'Ingestion job for {processed_key}'
            )

            job_id = ingestion_response['ingestionJob']['ingestionJobId']
            print(f"Started ingestion job with ID: {job_id}")
        except Exception as ingest_error:
            print(f"Error in start_ingestion_job call: {str(ingest_error)}")
            raise ingest_error

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

        # Prepare success response
        response_data = {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Document ingestion started',
                'knowledge_base_id': kb_id,
                'data_source_id': ds_id,
                'ingestion_job_id': job_id
            })
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
                import uuid
                from datetime import datetime
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
                    }
                }
            except Exception as e:
                print(f"Error storing large response in S3: {str(e)}")
                # Fall back to returning the full response
                return response_data

        # Return the response directly if not too large
        return response_data

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
    if bedrock_agent is None or bedrock_runtime is None:
        return {
            'statusCode': 500,
            'body': json.dumps('Bedrock clients not properly initialized')
        }

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
        try:
            print(f"Retrieving information from knowledge base: {kb_id} with query: {query}")
            retrieve_response = bedrock_agent.retrieve(
                knowledgeBaseId=kb_id,
                retrievalQuery={
                    'text': query
                },
                numberOfResults=5
            )

            # Get the retrieval results
            retrieval_results = retrieve_response['retrievalResults']
            print(f"Retrieved {len(retrieval_results)} results")
        except Exception as retrieve_error:
            print(f"Error in retrieve call: {str(retrieve_error)}")
            raise retrieve_error

        # Get the search index table
        search_index_table_name = os.environ.get('SEARCH_INDEX_TABLE_NAME', table_name)
        search_index_table = dynamodb.Table(search_index_table_name)

        # Find relevant images based on the query
        relevant_images = find_relevant_images(query, search_index_table)

        # Use Bedrock to generate a response based on the retrieved content

        # Prepare the context from retrieved documents
        context = ""
        for result in retrieval_results:
            content = result['content']['text']
            source = result.get('location', {}).get('s3Location', {}).get('uri', 'Unknown source')
            context += f"Source: {source}\nContent: {content}\n\n"

        # Generate a response using Claude
        try:
            print("Generating response with Claude")
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
            print("Generated response successfully")
        except Exception as model_error:
            print(f"Error invoking model: {str(model_error)}")
            raise model_error

        # Prepare the response with both text answer and relevant images
        response_data = {
            'statusCode': 200,
            'body': json.dumps({
                'query': query,
                'answer': answer,
                'sources': [result.get('location', {}).get('s3Location', {}).get('uri', 'Unknown source') for result in retrieval_results],
                'relevant_images': relevant_images
            })
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
                import uuid
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
                    }
                }
            except Exception as e:
                print(f"Error storing large response in S3: {str(e)}")
                # Fall back to returning the full response
                return response_data

        # Return the response directly if not too large
        return response_data

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
