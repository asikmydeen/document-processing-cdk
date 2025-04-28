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

    return bedrock_agent_client

# Get the client
try:
    bedrock_agent = get_bedrock_clients()
except Exception as e:
    print(f"Failed to initialize Bedrock clients: {str(e)}")
    # Define fallback values that will cause explicit errors if used
    bedrock_agent = None

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """Lambda function to initialize the Bedrock knowledge base."""
    print(f"Received event: {json.dumps(event)}")

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
        if not processed_bucket:
            return {
                'statusCode': 500,
                'body': json.dumps('PROCESSED_BUCKET_NAME environment variable not set')
            }

        # Get the knowledge base role ARN
        kb_role_arn = os.environ.get('KNOWLEDGE_BASE_ROLE_ARN')
        if not kb_role_arn:
            return {
                'statusCode': 500,
                'body': json.dumps('KNOWLEDGE_BASE_ROLE_ARN environment variable not set')
            }

        # Get the Kendra index ID
        kendra_index_id = os.environ.get('KENDRA_INDEX_ID')
        if not kendra_index_id:
            return {
                'statusCode': 500,
                'body': json.dumps('KENDRA_INDEX_ID environment variable not set')
            }

        # Create the knowledge base
        print(f"Creating knowledge base: {kb_name}")
        try:
            response = bedrock_agent.create_knowledge_base(
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
        except Exception as kb_error:
            print(f"Error in create_knowledge_base call: {str(kb_error)}")
            raise kb_error

        # Get the knowledge base ID
        kb_id = response['knowledgeBase']['knowledgeBaseId']
        print(f"Knowledge base created with ID: {kb_id}")

        # Create a data source for the knowledge base
        print(f"Creating data source for knowledge base: {kb_id}")
        try:
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
        print(f"Data source created with ID: {ds_id}")

        # Store the knowledge base and data source IDs in DynamoDB
        table_name = os.environ.get('METADATA_TABLE_NAME')
        if not table_name:
            return {
                'statusCode': 500,
                'body': json.dumps('METADATA_TABLE_NAME environment variable not set')
            }

        table = dynamodb.Table(table_name)

        # Generate a unique ID for the knowledge base configuration
        kb_config_id = str(uuid.uuid4())

        # Store the knowledge base configuration in DynamoDB
        print(f"Storing knowledge base configuration in DynamoDB table: {table_name}")
        table.put_item(Item={
            'id': kb_config_id,
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
                'message': 'Knowledge base initialized successfully',
                'knowledge_base_id': kb_id,
                'data_source_id': ds_id,
                'config_id': kb_config_id
            })
        }

    except Exception as e:
        print(f"Error initializing knowledge base: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error initializing knowledge base: {str(e)}'
            })
        }
