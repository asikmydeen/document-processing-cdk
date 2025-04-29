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
                        'kendraIndexArn': f"arn:aws:kendra:us-east-1:361769603480:index/{kendra_index_id}"
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
                        'inclusionPrefixes': ['Smart', 'processed_']  # Include objects with common prefixes
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

            # Check if knowledge base already exists
            print(f"Checking if knowledge base already exists: {kb_name}")
            try:
                # Try to list existing knowledge bases
                try:
                    list_kb_response = bedrock_agent.list_knowledge_bases()
                    existing_kb = None

                    # Check if a knowledge base with the given name already exists
                    for kb in list_kb_response.get('knowledgeBaseSummaries', []):
                        if kb.get('name') == kb_name:
                            existing_kb = kb
                            break
                except Exception as list_error:
                    # If we can't list knowledge bases due to permissions, try to query DynamoDB first
                    print(f"Error listing knowledge bases: {str(list_error)}. Checking DynamoDB for existing configuration.")
                    existing_kb = None

                    # Try to get the knowledge base configuration from DynamoDB
                    kb_query_response = table.query(
                        IndexName='DocumentIdIndex',
                        KeyConditionExpression='document_id = :did',
                        ExpressionAttributeValues={
                            ':did': 'KNOWLEDGE_BASE_CONFIG'
                        }
                    )



                    if kb_query_response['Items']:
                        # Use the existing configuration
                        kb_config = kb_query_response['Items'][0]
                        existing_kb = {
                            'knowledgeBaseId': kb_config['knowledge_base_id'],
                            'name': kb_name
                        }

                if existing_kb:
                    # Use the existing knowledge base
                    print(f"Using existing knowledge base: {kb_name} with ID: {existing_kb['knowledgeBaseId']}")
                    kb_id = existing_kb['knowledgeBaseId']

                    # For Kendra knowledge bases, we don't need to create a data source
                    # Kendra has its own data source management
                    print("This is a Kendra knowledge base, skipping data source creation")
                    ds_id = "KENDRA_MANAGED"  # Use a placeholder for the data source ID
                else:
                    # Create a new knowledge base
                    print(f"Creating new knowledge base: {kb_name}")
                    kb_response = bedrock_agent.create_knowledge_base(
                        name=kb_name,
                        description='Knowledge base for processed documents',
                        roleArn=kb_role_arn,
                        knowledgeBaseConfiguration={
                            'type': 'KENDRA',
                            'kendraKnowledgeBaseConfiguration': {
                                'kendraIndexArn': f"arn:aws:kendra:us-east-1:361769603480:index/{kendra_index_id}"
                            }
                        }
                    )

                    # Get the knowledge base ID from the response
                    kb_id = kb_response['knowledgeBase']['knowledgeBaseId']
                    print(f"Knowledge base created with ID: {kb_id}")

                    # For Kendra knowledge bases, we don't need to create a data source
                    # Kendra has its own data source management
                    print("This is a Kendra knowledge base, skipping data source creation")
                    ds_id = "KENDRA_MANAGED"  # Use a placeholder for the data source ID



            except Exception as kb_error:
                print(f"Error creating knowledge base: {str(kb_error)}")
                return {
                    'statusCode': 500,
                    'body': json.dumps(f'Error creating knowledge base: {str(kb_error)}')
                }

            # Generate a unique ID for the config
            import uuid
            kb_config_id = str(uuid.uuid4())

            # Store the knowledge base configuration in DynamoDB
            print(f"Storing knowledge base configuration in DynamoDB")
            from datetime import datetime
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

        # Now we should have a valid knowledge base configuration
        kb_config = response['Items'][0]
        kb_id = kb_config['knowledge_base_id']
        ds_id = kb_config['data_source_id']

        # For Kendra knowledge bases, we need to use a different approach to add documents
        try:
            if ds_id == "KENDRA_MANAGED":
                print(f"This is a Kendra knowledge base. Using Kendra's document ingestion methods.")

                # Extract the Kendra index ID from the knowledge base ARN
                # The ARN format is: arn:aws:kendra:region:account-id:index/index-id
                kendra_index_id = os.environ.get('KENDRA_INDEX_ID')

                # Get the document ID from the event or from the processed key
                document_id = event.get('document_id', os.path.splitext(os.path.basename(processed_key))[0])
                print(f"Using document ID: {document_id}")

                # Create a Kendra client
                kendra_client = boto3.client('kendra')

                # First, check if the Kendra index has any S3 data sources
                try:
                    print(f"Checking for S3 data sources in Kendra index: {kendra_index_id}")
                    data_sources_response = kendra_client.list_data_sources(
                        IndexId=kendra_index_id
                    )

                    s3_data_source = None
                    for ds in data_sources_response.get('SummaryItems', []):
                        print(f"Found data source: {ds.get('Name')} (Type: {ds.get('Type')})")
                        if ds.get('Type') == 'S3':
                            s3_data_source = ds
                            print(f"Found S3 data source: {ds.get('Name')} (ID: {ds.get('Id')})")
                            break

                    if s3_data_source:
                        # Get more details about the S3 data source
                        ds_id = s3_data_source.get('Id')
                        ds_details = kendra_client.describe_data_source(
                            IndexId=kendra_index_id,
                            Id=ds_id
                        )

                        # Extract the S3 bucket and prefix
                        s3_configuration = ds_details.get('Configuration', {}).get('S3Configuration', {})
                        s3_bucket = s3_configuration.get('BucketName')
                        s3_prefix = s3_configuration.get('InclusionPrefixes', [''])[0]

                        # If bucket name is not found in configuration, use the known bucket name
                        if not s3_bucket:
                            # Use the S3 bucket from environment variable
                            s3_bucket = os.environ.get('KENDRA_S3_BUCKET', 'aseekbot-poc-kb')
                            print(f"Using Kendra S3 data source bucket from environment: {s3_bucket}")

                        print(f"Found Kendra S3 data source bucket: {s3_bucket}, prefix: {s3_prefix}")
                        use_s3_data_source = True
                    else:
                        print("No S3 data source found. Falling back to BatchPutDocument.")
                        use_s3_data_source = False

                except Exception as ds_error:
                    print(f"Error checking data sources: {str(ds_error)}. Falling back to BatchPutDocument.")
                    use_s3_data_source = False

                # Skip checking existing documents as list_documents is not available in this version of the SDK
                print(f"Skipping document listing as it's not supported in this SDK version")

                # Get the document content from S3
                try:
                    s3_response = s3_client.get_object(Bucket=processed_bucket, Key=processed_key)
                    document_content = s3_response['Body'].read().decode('utf-8')
                    document_json = json.loads(document_content)

                    # Extract text content from the processed document
                    text_content = ""

                    # Print the document JSON structure to help debug
                    print(f"Document JSON keys: {list(document_json.keys())}")

                    # Check for document_content field which is likely to contain the full text
                    if 'document_content' in document_json:
                        if isinstance(document_json['document_content'], dict) and 'text_content' in document_json['document_content']:
                            text_content = document_json['document_content']['text_content']
                            print(f"Found document_content.text_content field with length: {len(text_content)}")
                        elif isinstance(document_json['document_content'], str):
                            text_content = document_json['document_content']
                            print(f"Found document_content field with length: {len(text_content)}")
                    elif 'text_content' in document_json:
                        text_content = document_json['text_content']
                        print(f"Found text_content field with length: {len(text_content)}")
                    elif 'content' in document_json:
                        text_content = document_json['content']
                        print(f"Found content field with length: {len(text_content)}")
                    elif 'body' in document_json:
                        text_content = document_json['body']
                        print(f"Found body field with length: {len(text_content)}")
                    elif 'text' in document_json:
                        text_content = document_json['text']
                        print(f"Found text field with length: {len(text_content)}")

                    # If we still don't have content, try to extract it from nested structures
                    if not text_content and isinstance(document_json, dict):
                        # Try to find any field that might contain the text content
                        for key, value in document_json.items():
                            if isinstance(value, str) and len(value) > 100:  # Assume large string fields are content
                                text_content = value
                                print(f"Found potential content in field '{key}' with length: {len(text_content)}")
                                break
                            elif isinstance(value, dict):
                                # Check nested dictionary
                                for nested_key, nested_value in value.items():
                                    if isinstance(nested_value, str) and len(nested_value) > 100:
                                        text_content = nested_value
                                        print(f"Found potential content in nested field '{key}.{nested_key}' with length: {len(text_content)}")
                                        break

                    # If we still don't have content, dump the entire JSON as text
                    if not text_content:
                        print("No content field found. Using the entire JSON as content.")
                        text_content = json.dumps(document_json, indent=2)
                        print(f"Generated content from full JSON with length: {len(text_content)}")

                    # Extract any metadata from the document
                    metadata = {}
                    if isinstance(document_json, dict):
                        # Extract metadata fields if they exist
                        if 'metadata' in document_json and isinstance(document_json['metadata'], dict):
                            metadata = document_json['metadata']
                        elif 'document_metadata' in document_json and isinstance(document_json['document_metadata'], dict):
                            metadata = document_json['document_metadata']

                    # Create attributes for Kendra
                    attributes = []
                    for key, value in metadata.items():
                        if isinstance(value, str):
                            attributes.append({
                                'Key': key,
                                'Value': {
                                    'StringValue': value
                                }
                            })

                    # Create a unique document ID for Kendra
                    import re
                    import uuid
                    from datetime import datetime

                    # Start with the original document ID
                    base_doc_id = document_id

                    # Remove file extension if present
                    if '.' in base_doc_id:
                        base_doc_id = base_doc_id.rsplit('.', 1)[0]

                    # Replace spaces and special characters with underscores
                    base_doc_id = re.sub(r'[^a-zA-Z0-9]', '_', base_doc_id)

                    # Add a timestamp and UUID to make it unique
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                    unique_id = str(uuid.uuid4())[:8]  # Use first 8 chars of UUID

                    # Combine to create a unique document ID
                    clean_doc_id = f"{base_doc_id}_{timestamp}_{unique_id}"

                    print(f"Original document ID: {document_id}")
                    print(f"Unique document ID for Kendra: {clean_doc_id}")

                    # Check if we should use S3 data source or BatchPutDocument
                    if 'use_s3_data_source' in locals() and use_s3_data_source and 's3_bucket' in locals() and s3_bucket:
                        # Create a unique filename for the document
                        unique_filename = f"{clean_doc_id}.txt"

                        # Construct the full S3 key with the prefix
                        s3_key = f"{s3_prefix}/{unique_filename}" if s3_prefix else unique_filename
                        s3_key = s3_key.replace('//', '/')  # Avoid double slashes

                        print(f"Uploading to Kendra S3 data source as: {s3_key}")

                        # Create a metadata file if we have metadata
                        if metadata:
                            # Format metadata according to Kendra's requirements
                            # See: https://docs.aws.amazon.com/kendra/latest/dg/s3-metadata.html
                            metadata_lines = []
                            for key, value in metadata.items():
                                if isinstance(value, str):
                                    metadata_lines.append(f"{key}={value}")

                            if metadata_lines:
                                metadata_content = "\n".join(metadata_lines)
                                metadata_filename = f"{unique_filename}.metadata.txt"
                                metadata_key = f"{s3_prefix}/{metadata_filename}" if s3_prefix else metadata_filename
                                metadata_key = metadata_key.replace('//', '/')  # Avoid double slashes

                                print(f"Uploading metadata file to: {metadata_key}")
                                s3_client.put_object(
                                    Bucket=s3_bucket,
                                    Key=metadata_key,
                                    Body=metadata_content,
                                    ContentType='text/plain'
                                )

                        # Upload the document to the Kendra S3 data source bucket
                        print(f"Uploading document to Kendra S3 data source bucket: {s3_bucket}/{s3_key}")
                        print(f"Document content length: {len(text_content)} characters")

                        s3_client.put_object(
                            Bucket=s3_bucket,
                            Key=s3_key,
                            Body=text_content,
                            ContentType='text/plain'
                        )

                        # Start a sync job to make the document available immediately
                        print(f"Starting sync job for data source: {ds_id}")
                        sync_response = kendra_client.start_data_source_sync_job(
                            IndexId=kendra_index_id,
                            Id=ds_id
                        )

                        job_id = sync_response['ExecutionId']
                        print(f"Started sync job with ID: {job_id}")
                    else:
                        # Use BatchPutDocument to add the document to Kendra
                        print(f"Adding document to Kendra index: {kendra_index_id}")
                        print(f"Document content length: {len(text_content)} characters")

                        # Prepare the document with attributes
                        document = {
                            'Id': clean_doc_id,
                            'Title': clean_doc_id,
                            'ContentType': 'PLAIN_TEXT',
                            'Blob': text_content
                        }

                        # Add attributes if we have any
                        if attributes:
                            document['Attributes'] = attributes
                            print(f"Adding document with {len(attributes)} metadata attributes")

                        kendra_response = kendra_client.batch_put_document(
                            IndexId=kendra_index_id,
                            Documents=[document]
                        )

                        job_id = f"KENDRA-{kendra_response['ResponseMetadata']['RequestId']}"
                        print(f"Document added to Kendra index with job ID: {job_id}")



                    # Wait for document ingestion to complete
                    try:
                        import time
                        max_wait_time = 300  # Maximum wait time in seconds (5 minutes)
                        wait_interval = 10   # Check every 10 seconds
                        start_time = time.time()
                        ingestion_complete = False

                        print(f"Waiting for document ingestion to complete (timeout: {max_wait_time} seconds)...")

                        while (time.time() - start_time) < max_wait_time:
                            # Check the status of the document
                            doc_status_response = kendra_client.batch_get_document_status(
                                IndexId=kendra_index_id,
                                DocumentInfoList=[
                                    {
                                        'DocumentId': clean_doc_id
                                    }
                                ]
                            )

                            # Log the document status
                            if 'DocumentStatusList' in doc_status_response and doc_status_response['DocumentStatusList']:
                                doc_status = doc_status_response['DocumentStatusList'][0]
                                status = doc_status.get('Status', 'Unknown')
                                print(f"Document status in Kendra: {status}")
                                print(f"Full document status: {json.dumps(doc_status)}")

                                if status == 'INDEXED':
                                    print(f"Document successfully indexed in Kendra")
                                    ingestion_complete = True
                                    break
                                elif status in ['FAILED', 'ERROR']:
                                    print(f"Document indexing failed: {doc_status.get('FailureReason', 'Unknown reason')}")
                                    break
                                elif status == 'Unknown' and (time.time() - start_time) > 60:
                                    # After 60 seconds of Unknown status, try to verify with a query
                                    try:
                                        print("Attempting to verify document availability with a query...")
                                        # Extract some content from the document to use as a query
                                        query_text = ""
                                        if len(text_content) > 20:
                                            # Use the first 100 characters as a query
                                            query_text = text_content[:100].strip()
                                        else:
                                            # Fall back to document ID
                                            query_text = document_id

                                        print(f"Querying with text: '{query_text[:50]}...'")

                                        # Try to query for the document to see if it's available
                                        query_response = kendra_client.query(
                                            IndexId=kendra_index_id,
                                            QueryText=query_text
                                        )

                                        # Check if we got any results
                                        if 'ResultItems' in query_response and query_response['ResultItems']:
                                            print(f"Document found in query results! Document appears to be available.")
                                            print(f"Query returned {len(query_response['ResultItems'])} results")
                                            ingestion_complete = True
                                            break
                                        else:
                                            print("Document not found in query results yet")
                                    except Exception as query_error:
                                        print(f"Error querying for document: {str(query_error)}")
                            else:
                                print("No document status information available yet")

                                # After 60 seconds with no status, try listing all documents
                                if (time.time() - start_time) > 60 and (time.time() - start_time) < 70:
                                    try:
                                        print("Attempting to list documents in Kendra index...")
                                        # Try to list documents to see if our document is there
                                        list_response = kendra_client.list_documents(
                                            IndexId=kendra_index_id
                                        )

                                        print(f"Found {len(list_response.get('DocumentInfoList', []))} documents in index")
                                        # Check if our document is in the list
                                        for doc_info in list_response.get('DocumentInfoList', []):
                                            print(f"Document in index: {doc_info.get('DocumentId')}")
                                            if doc_info.get('DocumentId') == clean_doc_id:
                                                print(f"Our document found in index list!")
                                                break
                                    except Exception as list_error:
                                        print(f"Error listing documents: {str(list_error)}")

                            # Wait before checking again
                            time.sleep(wait_interval)

                        if not ingestion_complete:
                            print(f"Warning: Document ingestion did not complete within {max_wait_time} seconds")
                            print(f"The Step Function will continue, but the document may not be immediately available for querying")

                            # Try one final query to see if the document is available
                            try:
                                print("Performing final verification query...")
                                # Extract some content from the document to use as a query
                                query_text = ""
                                if len(text_content) > 20:
                                    # Use the first 100 characters as a query
                                    query_text = text_content[:100].strip()
                                else:
                                    # Fall back to document ID
                                    query_text = document_id

                                print(f"Final verification query with text: '{query_text[:50]}...'")

                                query_response = kendra_client.query(
                                    IndexId=kendra_index_id,
                                    QueryText=query_text
                                )

                                if 'ResultItems' in query_response and query_response['ResultItems']:
                                    print(f"Good news! Document found in final query results. Document appears to be available.")
                                    print(f"Query returned {len(query_response['ResultItems'])} results")
                                    ingestion_complete = True
                                else:
                                    print("Document not found in final query results")

                                    # Try listing all documents one last time
                                    try:
                                        print("Final attempt to list documents in Kendra index...")
                                        list_response = kendra_client.list_documents(
                                            IndexId=kendra_index_id
                                        )

                                        print(f"Found {len(list_response.get('DocumentInfoList', []))} documents in index")
                                        document_found = False
                                        for doc_info in list_response.get('DocumentInfoList', []):
                                            if doc_info.get('DocumentId') == clean_doc_id:
                                                print(f"Our document found in index list! Status: {doc_info.get('Status')}")
                                                document_found = True
                                                break

                                        if not document_found:
                                            print(f"Document {clean_doc_id} not found in index document list")
                                    except Exception as list_error:
                                        print(f"Error listing documents: {str(list_error)}")
                            except Exception as final_query_error:
                                print(f"Error in final verification query: {str(final_query_error)}")

                        # Get final document status for logging
                        doc_status_response = kendra_client.batch_get_document_status(
                            IndexId=kendra_index_id,
                            DocumentInfoList=[
                                {
                                    'DocumentId': clean_doc_id
                                }
                            ]
                        )

                        if 'DocumentStatusList' in doc_status_response and doc_status_response['DocumentStatusList']:
                            doc_status = doc_status_response['DocumentStatusList'][0]
                            print(f"Final document status: {json.dumps(doc_status)}")

                        print(f"Document ingestion process completed. Ingestion success: {ingestion_complete}")
                    except Exception as status_error:
                        print(f"Error checking document status: {str(status_error)}")

                except Exception as s3_error:
                    print(f"Error getting document from S3: {str(s3_error)}")
                    raise s3_error
            else:
                # For vector knowledge bases, use the standard ingestion job
                print(f"Starting ingestion job for knowledge base: {kb_id}, data source: {ds_id}")
                ingestion_response = bedrock_agent.start_ingestion_job(
                    knowledgeBaseId=kb_id,
                    dataSourceId=ds_id,
                    description=f'Ingestion job for {processed_key}'
                )

                job_id = ingestion_response['ingestionJob']['ingestionJobId']
                print(f"Started ingestion job with ID: {job_id}")

                # Wait for the ingestion job to complete
                try:
                    import time
                    max_wait_time = 300  # Maximum wait time in seconds (5 minutes)
                    wait_interval = 10   # Check every 10 seconds
                    start_time = time.time()
                    ingestion_complete = False

                    print(f"Waiting for ingestion job to complete (timeout: {max_wait_time} seconds)...")

                    while (time.time() - start_time) < max_wait_time:
                        # Check the status of the ingestion job
                        job_response = bedrock_agent.get_ingestion_job(
                            knowledgeBaseId=kb_id,
                            ingestionJobId=job_id
                        )

                        status = job_response['ingestionJob']['status']
                        print(f"Ingestion job status: {status}")

                        if status == 'COMPLETE':
                            print(f"Ingestion job completed successfully")
                            ingestion_complete = True
                            break
                        elif status in ['FAILED', 'STOPPED']:
                            print(f"Ingestion job failed: {job_response['ingestionJob'].get('failureReason', 'Unknown reason')}")
                            break

                        # Wait before checking again
                        time.sleep(wait_interval)

                    if not ingestion_complete:
                        print(f"Warning: Ingestion job did not complete within {max_wait_time} seconds")
                        print(f"The Step Function will continue, but the document may not be immediately available for querying")
                except Exception as job_status_error:
                    print(f"Error checking ingestion job status: {str(job_status_error)}")
        except Exception as ingest_error:
            print(f"Error in document ingestion: {str(ingest_error)}")
            raise ingest_error

        # Update the document metadata with the ingestion job ID
        # Use the document_id from the event if available, otherwise extract from the processed key
        if 'document_id' not in locals() or not document_id:
            document_id = event.get('document_id', os.path.splitext(os.path.basename(processed_key))[0])

        response = table.query(
            IndexName='DocumentIdIndex',
            KeyConditionExpression='document_id = :did',
            ExpressionAttributeValues={
                ':did': document_id
            }
        )

        if response['Items']:
            metadata_item = response['Items'][0]

            # Import datetime for timestamp
            from datetime import datetime

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

            # Check which API method is available
            if hasattr(bedrock_agent, 'retrieve'):
                # Use the retrieve method if available
                retrieve_response = bedrock_agent.retrieve(
                    knowledgeBaseId=kb_id,
                    retrievalQuery={
                        'text': query
                    },
                    numberOfResults=5
                )

                # Get the retrieval results
                retrieval_results = retrieve_response['retrievalResults']
            elif hasattr(bedrock_agent, 'query_knowledge_base'):
                # Use the query_knowledge_base method if available
                retrieve_response = bedrock_agent.query_knowledge_base(
                    knowledgeBaseId=kb_id,
                    text=query,
                    numberOfResults=5
                )

                # Get the retrieval results - format may differ between APIs
                if 'retrievalResults' in retrieve_response:
                    retrieval_results = retrieve_response['retrievalResults']
                elif 'results' in retrieve_response:
                    retrieval_results = retrieve_response['results']
                else:
                    # Create a fallback structure if no results found
                    print("No results found in the response. Using empty results.")
                    retrieval_results = []
            else:
                # Try using the Kendra query API directly
                print("Bedrock knowledge base query methods not available. Falling back to Kendra query.")
                kendra_client = boto3.client('kendra')
                kendra_index_id = os.environ.get('KENDRA_INDEX_ID')

                if not kendra_index_id:
                    raise Exception("KENDRA_INDEX_ID environment variable not set")

                kendra_response = kendra_client.query(
                    IndexId=kendra_index_id,
                    QueryText=query
                )

                # Convert Kendra results to a format similar to Bedrock results
                retrieval_results = []
                for result_item in kendra_response.get('ResultItems', []):
                    if result_item.get('Type') == 'DOCUMENT':
                        document_text = result_item.get('DocumentExcerpt', {}).get('Text', '')
                        document_uri = result_item.get('DocumentURI', 'Unknown source')

                        retrieval_results.append({
                            'content': {
                                'text': document_text
                            },
                            'location': {
                                's3Location': {
                                    'uri': document_uri
                                }
                            }
                        })

            print(f"Retrieved {len(retrieval_results)} results")
        except Exception as retrieve_error:
            print(f"Error in retrieve call: {str(retrieve_error)}")
            raise retrieve_error

        # Get the search index table
        search_index_table_name = os.environ.get('SEARCH_INDEX_TABLE_NAME', table_name)
        search_index_table = dynamodb.Table(search_index_table_name)

        # Find relevant images based on the query
        relevant_images = find_relevant_images(query, search_index_table)

        # Print information about the found images
        print(f"Found {len(relevant_images)} relevant images for query: {query}")
        for i, img in enumerate(relevant_images):
            print(f"Image {i+1}: {img.get('image_description', 'No description')} - Score: {img.get('relevance_score', 0)}")
            print(f"  URI: {img.get('image_s3_uri', 'No URI')}")
            print(f"  Has presigned URL: {'Yes' if 'presigned_url' in img else 'No'}")
            print(f"  Matched terms: {img.get('matched_terms', [])}")

        # Use Bedrock to generate a response based on the retrieved content

        # Prepare the context from retrieved documents
        context = ""
        if retrieval_results:
            for result in retrieval_results:
                # Handle different result formats
                if 'content' in result and 'text' in result['content']:
                    content = result['content']['text']
                elif 'document' in result:
                    content = result.get('document', {}).get('content', '')
                elif 'text' in result:
                    content = result['text']
                else:
                    print(f"Warning: Unexpected result format: {result}")
                    content = str(result)

                # Handle different source formats
                if 'location' in result and 's3Location' in result['location']:
                    source = result['location']['s3Location'].get('uri', 'Unknown source')
                elif 'documentURI' in result:
                    source = result.get('documentURI', 'Unknown source')
                elif 'source' in result:
                    source = result['source']
                else:
                    source = 'Unknown source'

                context += f"Source: {source}\nContent: {content}\n\n"
        else:
            context = "No relevant documents found in the knowledge base."
            print("Warning: No retrieval results found. Using empty context.")

        # Add information about relevant images to the context
        if relevant_images:
            context += "\nRelevant images found:\n"
            for i, img in enumerate(relevant_images[:5]):  # Include up to 5 images in the context
                description = img.get('image_description', f"Image {i+1}")
                preview = img.get('text_content_preview', 'No text content')
                context += f"Image {i+1}: {description}\nText content: {preview}\n\n"

        # Generate a response using Claude
        try:
            print("Generating response with Claude")

            # Create a prompt that includes information about images
            image_instruction = ""
            if relevant_images:
                image_instruction = """
                I've also found some images that might be relevant to your question.
                In your answer, please mention that there are relevant images available
                that will be displayed alongside your response.
                """

            prompt = f"""
            Human: I have the following question: {query}

            Here is some context that might help you answer:

            {context}

            {image_instruction}

            Please provide a comprehensive answer based on the context provided. If the context doesn't contain enough information to answer the question, please say so. Include references to the sources in your answer.

            Assistant:
            """

            # Use Claude 3.5 Sonnet model
            model_id = 'anthropic.claude-3-5-sonnet-20241022-v2:0'
            print(f"Using model: {model_id}")

            try:
                # Try using the new Claude 3.5 format first
                response = bedrock_runtime.invoke_model(
                    modelId=model_id,
                    body=json.dumps({
                        'anthropic_version': 'bedrock-2023-05-31',
                        'max_tokens': 4000,
                        'temperature': 0.1,
                        'messages': [
                            {
                                'role': 'user',
                                'content': [
                                    {
                                        'type': 'text',
                                        'text': f"""I have the following question: {query}

Here is some context that might help you answer:

{context}

{image_instruction}

Please provide a comprehensive answer based on the context provided. If the context doesn't contain enough information to answer the question, please say so. Include references to the sources in your answer."""
                                    }
                                ]
                            }
                        ]
                    })
                )

                # Parse the response for Claude 3.5
                response_body = json.loads(response['body'].read())
                answer = response_body.get('content', [{}])[0].get('text', '')
                print("Generated response successfully using Claude 3.5 format")
            except Exception as claude3_error:
                print(f"Error using Claude 3.5 format: {str(claude3_error)}. Falling back to Claude 2 format.")

                # Fall back to the older Claude 2 format
                response = bedrock_runtime.invoke_model(
                    modelId='anthropic.claude-v2',
                    body=json.dumps({
                        'prompt': prompt,
                        'max_tokens_to_sample': 4000,
                        'temperature': 0.1
                    })
                )

                # Parse the response for Claude 2
                response_body = json.loads(response['body'].read())
                answer = response_body.get('completion', '')
                print("Generated response successfully using Claude 2 format")
        except Exception as model_error:
            print(f"Error invoking model: {str(model_error)}")
            raise model_error

        # Format the images for the response
        formatted_images = []
        for img in relevant_images:
            formatted_img = {
                'description': img.get('image_description', 'Image'),
                'uri': img.get('image_s3_uri', ''),
                'relevance_score': img.get('relevance_score', 0)
            }

            # Add presigned URL if available
            if 'presigned_url' in img:
                formatted_img['url'] = img['presigned_url']

            # Add text content preview
            if 'text_content_preview' in img:
                formatted_img['text_content'] = img['text_content_preview']

            formatted_images.append(formatted_img)

        # Prepare the response with both text answer and relevant images
        response_data = {
            'statusCode': 200,
            'body': json.dumps({
                'query': query,
                'answer': answer,
                'sources': get_sources_from_results(retrieval_results),
                'has_images': len(formatted_images) > 0,
                'image_count': len(formatted_images),
                'images': formatted_images
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
        print(f"Error querying knowledge base: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error querying knowledge base: {str(e)}'
            })
        }

def get_sources_from_results(retrieval_results):
    """Extract source information from retrieval results in different formats."""
    sources = []
    if not retrieval_results:
        return sources

    for result in retrieval_results:
        source = None
        # Handle different source formats
        if 'location' in result and 's3Location' in result['location']:
            source = result['location']['s3Location'].get('uri')
        elif 'documentURI' in result:
            source = result.get('documentURI')
        elif 'source' in result:
            source = result['source']

        if source and source not in sources:
            sources.append(source)

    return sources or ['No sources found']

def find_relevant_images(query, search_index_table):
    """Find images that are relevant to the query based on their text content."""
    try:
        print(f"Finding relevant images for query: {query}")

        # First, scan for image content indices
        image_indices = []

        # Scan for image_content indices
        response = search_index_table.scan(
            FilterExpression='attribute_exists(image_s3_uri) AND index_type = :type',
            ExpressionAttributeValues={
                ':type': 'image_content'
            }
        )
        image_content_indices = response.get('Items', [])
        print(f"Found {len(image_content_indices)} image_content indices")
        image_indices.extend(image_content_indices)

        # Scan for embedded_image indices
        response = search_index_table.scan(
            FilterExpression='attribute_exists(image_s3_uri) AND index_type = :type',
            ExpressionAttributeValues={
                ':type': 'embedded_image'
            }
        )
        embedded_image_indices = response.get('Items', [])
        print(f"Found {len(embedded_image_indices)} embedded_image indices")
        image_indices.extend(embedded_image_indices)

        # Scan for embedded_image_section indices
        response = search_index_table.scan(
            FilterExpression='attribute_exists(image_s3_uri) AND index_type = :type',
            ExpressionAttributeValues={
                ':type': 'embedded_image_section'
            }
        )
        section_indices = response.get('Items', [])
        print(f"Found {len(section_indices)} embedded_image_section indices")
        image_indices.extend(section_indices)

        print(f"Total image indices found: {len(image_indices)}")

        # If we have no images, return empty list
        if not image_indices:
            print("No image indices found in the database")
            return []

        # Prepare query terms for matching
        query_terms = [term.lower() for term in query.split() if len(term) > 3]  # Only use terms with more than 3 chars
        print(f"Query terms for matching: {query_terms}")

        # Score images based on relevance to the query
        image_scores = {}
        for index in image_indices:
            index_value = index.get('index_value', '').lower()
            image_s3_uri = index.get('image_s3_uri', '')

            if not image_s3_uri:
                continue

            # Initialize score for this image if not already done
            if image_s3_uri not in image_scores:
                image_scores[image_s3_uri] = {
                    'score': 0,
                    'index': index,
                    'matched_terms': set()
                }

            # Calculate score based on term matches
            for term in query_terms:
                if term in index_value:
                    # Add to the score based on the index type
                    if index.get('index_type') == 'embedded_image':
                        image_scores[image_s3_uri]['score'] += 3  # Higher weight for direct image text
                    elif index.get('index_type') == 'image_content':
                        image_scores[image_s3_uri]['score'] += 2  # Medium weight for image content
                    else:
                        image_scores[image_s3_uri]['score'] += 1  # Lower weight for section matches

                    # Record the matched term
                    image_scores[image_s3_uri]['matched_terms'].add(term)

        # Sort images by score (descending)
        sorted_images = sorted(
            image_scores.items(),
            key=lambda x: x[1]['score'],
            reverse=True
        )

        print(f"Found {len(sorted_images)} images with non-zero scores")

        # Take the top 10 images
        top_images = sorted_images[:10]

        # Create the result list with image details
        relevant_images = []
        for image_uri, score_data in top_images:
            if score_data['score'] == 0:
                continue  # Skip images with zero score

            index = score_data['index']
            index_value = index.get('index_value', '')

            # Get the image details
            image_info = {
                'image_s3_uri': image_uri,
                'document_id': index.get('document_id', ''),
                'image_description': index.get('image_description', ''),
                'text_content_preview': index_value[:100] + '...' if len(index_value) > 100 else index_value,
                'relevance_score': score_data['score'],
                'matched_terms': list(score_data['matched_terms'])
            }

            # Add position information if available
            if 'image_position' in index:
                image_info['position'] = index['image_position']

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
                            print(f"Generated presigned URL for image: {image_uri}")
                        except Exception as e:
                            print(f"Error generating presigned URL for {image_uri}: {str(e)}")
                    else:
                        print(f"Warning: Empty bucket or key in S3 URI: {image_uri}")

            relevant_images.append(image_info)

        print(f"Returning {len(relevant_images)} relevant images")
        return relevant_images

    except Exception as e:
        print(f"Error finding relevant images: {str(e)}")
        return []
