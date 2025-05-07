import json
import os
import boto3
import uuid
import decimal
from datetime import datetime

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
bedrock_runtime = boto3.client('bedrock-runtime')

# Helper class to convert a DynamoDB item to JSON
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)

# Get environment variables
METADATA_TABLE_NAME = os.environ.get('METADATA_TABLE_NAME')
BEDROCK_MODEL_ID_FOR_IMAGE_DESC = os.environ.get('BEDROCK_MODEL_ID_FOR_IMAGE_DESC', 'anthropic.claude-3-5-sonnet-20241022-v2:0')

def lambda_handler(event, context):
    """Lambda function to generate AI descriptions for images."""
    print(f"Received event: {json.dumps(event)}")

    try:
        # Get the document ID and metadata ID from the event
        document_id = event.get('document_id')
        metadata_id = event.get('metadata_id')

        if not document_id or not metadata_id:
            return {
                'statusCode': 400,
                'body': json.dumps('Missing document_id or metadata_id in event')
            }

        # Get the metadata item from DynamoDB
        metadata_table = dynamodb.Table(METADATA_TABLE_NAME)
        response = metadata_table.get_item(
            Key={
                'id': metadata_id,
                'document_id': document_id
            }
        )

        if 'Item' not in response:
            return {
                'statusCode': 404,
                'body': json.dumps(f'Metadata item not found for document_id: {document_id}, metadata_id: {metadata_id}')
            }

        metadata_item = response['Item']

        # Get the images to process
        images_to_process = []

        if 'images' in metadata_item:
            # Use images directly from the item
            images_to_process = metadata_item['images']
            print(f"Using {len(images_to_process)} images directly from metadata")
        elif 'images_s3_key' in metadata_item and 'images_s3_bucket' in metadata_item:
            # Retrieve images from S3
            try:
                images_s3_response = s3_client.get_object(
                    Bucket=metadata_item['images_s3_bucket'],
                    Key=metadata_item['images_s3_key']
                )
                images_to_process = json.loads(images_s3_response['Body'].read().decode('utf-8'))
                print(f"Retrieved {len(images_to_process)} images from S3")
            except Exception as e:
                print(f"Error retrieving images from S3: {str(e)}")
                # Fall back to using any images that might be in the item
                if 'images' in metadata_item:
                    images_to_process = metadata_item['images']
                    print(f"Falling back to {len(images_to_process)} images in metadata")

        if not images_to_process:
            return {
                'statusCode': 200,
                'body': json.dumps('No images to process'),
                'document_id': document_id,
                'metadata_id': metadata_id,
                'processed_images': []
            }

        # Get Q&A pairs from metadata if available
        qa_pairs = []

        if 'qa_pairs' in metadata_item:
            # Use Q&A pairs directly from the item
            qa_pairs = metadata_item['qa_pairs']
            print(f"Found {len(qa_pairs)} Q&A pairs directly in metadata")
        elif 'qa_pairs_sample' in metadata_item:
            # Use the sample Q&A pairs from the item
            qa_pairs = metadata_item['qa_pairs_sample']
            print(f"Using {len(qa_pairs)} sample Q&A pairs from metadata")
        elif 'qa_pairs_s3_key' in metadata_item and 'qa_pairs_s3_bucket' in metadata_item:
            # Retrieve Q&A pairs from S3
            try:
                qa_s3_response = s3_client.get_object(
                    Bucket=metadata_item['qa_pairs_s3_bucket'],
                    Key=metadata_item['qa_pairs_s3_key']
                )
                qa_pairs = json.loads(qa_s3_response['Body'].read().decode('utf-8'))
                print(f"Retrieved {len(qa_pairs)} Q&A pairs from S3")
            except Exception as e:
                print(f"Error retrieving Q&A pairs from S3: {str(e)}")
                # Fall back to using the sample if available
                if 'qa_pairs_sample' in metadata_item:
                    qa_pairs = metadata_item['qa_pairs_sample']
                    print(f"Falling back to {len(qa_pairs)} sample Q&A pairs")

        # Process each image
        processed_images = []

        for img_info in images_to_process:
            if img_info.get('extracted_image_s3_uri') and not img_info.get('extraction_error'):
                img_uri_parts = img_info['extracted_image_s3_uri'].replace("s3://", "").split("/",1)
                if len(img_uri_parts) == 2:
                    img_bucket, img_key = img_uri_parts

                    # Find the closest Q&A pair to this image based on page number
                    img_page = img_info.get('page_number', 0)
                    context_text = ""
                    associated_qa = None

                    if qa_pairs:
                        # Try to find a Q&A pair on the same page
                        for qa_pair in qa_pairs:
                            if qa_pair.get('page_number') == img_page:
                                context_text = f"Question: {qa_pair.get('question', '')}\nAnswer: {qa_pair.get('answer', '')}"
                                associated_qa = qa_pair
                                print(f"Found Q&A pair on page {img_page} for image: {img_info['extracted_image_s3_uri']}")
                                break

                        # If no Q&A pair on the same page, use the closest one
                        if not associated_qa and qa_pairs:
                            closest_qa = min(qa_pairs, key=lambda qa: abs(qa.get('page_number', 0) - img_page))
                            context_text = f"Question: {closest_qa.get('question', '')}\nAnswer: {closest_qa.get('answer', '')}"
                            associated_qa = closest_qa
                            print(f"Using closest Q&A pair from page {closest_qa.get('page_number')} for image on page {img_page}")

                    print(f"Generating AI description for extracted image: {img_info['extracted_image_s3_uri']}")
                    img_ai_description = get_image_description_from_bedrock(img_bucket, img_key, context_text)
                    img_info['ai_generated_description'] = img_ai_description

                    # Store the associated Q&A pair with the image
                    if associated_qa:
                        img_info['associated_qa'] = associated_qa

                    # Update text_content for this specific image, this will be used by create_image_search_indices
                    if associated_qa:
                        img_info['text_content'] = f"{img_ai_description}\n\nQuestion: {associated_qa.get('question', '')}\nAnswer: {associated_qa.get('answer', '')}"
                    else:
                        img_info['text_content'] = img_ai_description

            processed_images.append(img_info)

        # Store the processed images back in S3
        processed_images_s3_key = f"processed_images/{document_id}_{str(uuid.uuid4())}.json"
        processed_bucket = metadata_item.get('processed_bucket')

        if not processed_bucket:
            processed_bucket = os.environ.get('PROCESSED_BUCKET_NAME')

        s3_client.put_object(
            Bucket=processed_bucket,
            Key=processed_images_s3_key,
            Body=json.dumps(processed_images, cls=DecimalEncoder),
            ContentType='application/json'
        )

        print(f"Stored {len(processed_images)} processed images in S3: {processed_bucket}/{processed_images_s3_key}")

        # Return the result with field names that match what the next state expects
        return {
            'statusCode': 200,
            'document_id': document_id,
            'metadata_id': metadata_id,
            'processed_bucket': processed_bucket,  # Use consistent field name
            'processed_key': processed_images_s3_key,  # Use consistent field name
            'processed_images_s3_bucket': processed_bucket,  # Keep original for backward compatibility
            'processed_images_s3_key': processed_images_s3_key,  # Keep original for backward compatibility
            'processed_images_count': len(processed_images)
        }

    except Exception as e:
        print(f"Error generating image descriptions: {str(e)}")

        # Get the metadata item to extract necessary fields even in case of error
        try:
            # Get the metadata item from DynamoDB
            metadata_table = dynamodb.Table(METADATA_TABLE_NAME)
            response = metadata_table.get_item(
                Key={
                    'id': metadata_id,
                    'document_id': document_id
                }
            )

            if 'Item' in response:
                metadata_item = response['Item']
                processed_bucket = metadata_item.get('processed_bucket', '')
                processed_key = metadata_item.get('processed_key', '')
            else:
                processed_bucket = os.environ.get('PROCESSED_BUCKET_NAME', '')
                processed_key = ''
        except Exception as inner_e:
            print(f"Error retrieving metadata in exception handler: {str(inner_e)}")
            processed_bucket = os.environ.get('PROCESSED_BUCKET_NAME', '')
            processed_key = ''

        # Return error with necessary fields for the next state
        return {
            'statusCode': 500,
            'document_id': document_id,
            'processed_bucket': processed_bucket,
            'processed_key': processed_key,
            'error': f'Error generating image descriptions: {str(e)}'
        }

def get_image_description_from_bedrock(image_s3_bucket: str, image_s3_key: str, context_text: str = "") -> str:
    """Generate a detailed description for an image using Bedrock Claude 3.5 Sonnet.

    Args:
        image_s3_bucket: The S3 bucket containing the image
        image_s3_key: The S3 key of the image
        context_text: Optional surrounding text context to help with image description
    """
    print(f"Generating description for image s3://{image_s3_bucket}/{image_s3_key} using model {BEDROCK_MODEL_ID_FOR_IMAGE_DESC}")

    import base64

    try:
        # Download image from S3
        s3_response = s3_client.get_object(Bucket=image_s3_bucket, Key=image_s3_key)
        image_bytes = s3_response['Body'].read()

        # Determine media type (simple check, can be enhanced)
        media_type = "image/png" # Default
        if image_s3_key.lower().endswith(".jpg") or image_s3_key.lower().endswith(".jpeg"):
            media_type = "image/jpeg"
        elif image_s3_key.lower().endswith(".gif"):
            media_type = "image/gif"
        elif image_s3_key.lower().endswith(".webp"):
            media_type = "image/webp"

        # Base64 encode the image bytes
        base64_image_data = base64.b64encode(image_bytes).decode('utf-8')

        # Prepare the prompt based on whether we have context
        prompt_text = "Describe this image in detail. What are the key objects, context, and any text visible? If it's a document or screenshot, summarize its purpose and content."

        # If we have context text, enhance the prompt to use it
        if context_text:
            prompt_text = f"""Describe this image in detail. What are the key objects, context, and any text visible?

This image appears in a document with the following surrounding text:
{context_text}

Based on this context and the image content, provide a detailed description that explains:
1. What the image shows
2. How it relates to the surrounding text (especially if it's part of a question and answer)
3. Any text visible in the image itself
4. The specific purpose this image serves in the document

If it's a document or screenshot, summarize its purpose and content."""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt_text
                        }
                    ]
                }
            ]
        }

        response = bedrock_runtime.invoke_model(
            body=json.dumps(request_body),
            modelId=BEDROCK_MODEL_ID_FOR_IMAGE_DESC,
            contentType='application/json',
            accept='application/json'
        )
        response_body = json.loads(response.get('body').read())

        # Extract text content from the response
        # Claude 3.5 Sonnet (Messages API) returns content in a list, usually with one text block.
        description = ""
        if response_body.get("content") and isinstance(response_body["content"], list):
            for block in response_body["content"]:
                if block.get("type") == "text":
                    description += block.get("text", "")

        if description:
            print(f"Successfully generated description for s3://{image_s3_bucket}/{image_s3_key}: {description[:200]}...")
            return description.strip()
        else:
            print(f"Warning: Bedrock returned no text description for s3://{image_s3_bucket}/{image_s3_key}. Response: {response_body}")
            return "No description generated by Bedrock."

    except Exception as e:
        print(f"Error generating image description from Bedrock for s3://{image_s3_bucket}/{image_s3_key}: {str(e)}")
        return f"Error generating description: {str(e)}"
