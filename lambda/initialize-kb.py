import json
import os
import boto3
import uuid
from datetime import datetime

# Initialize AWS clients
bedrock_agent = boto3.client('bedrock-agent')  # Use bedrock-agent instead of bedrock
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """Lambda function to initialize the Bedrock knowledge base."""
    print(f"Received event: {json.dumps(event)}")

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

        # Create the knowledge base
        print(f"Creating knowledge base: {kb_name}")
        response = bedrock_agent.create_knowledge_base(
            name=kb_name,
            description='Knowledge base for processed documents',
            roleArn=kb_role_arn,
            knowledgeBaseConfiguration={
                'type': 'VECTOR',
                'vectorKnowledgeBaseConfiguration': {
                    'embeddingModelArn': 'arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1'
                }
            }
        )

        # Get the knowledge base ID
        kb_id = response['knowledgeBase']['knowledgeBaseId']
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
