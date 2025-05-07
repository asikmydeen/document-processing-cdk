import json
import os
import boto3
import uuid
from datetime import datetime

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime') # Add Bedrock runtime client

# Bedrock Model ID for Image Description
BEDROCK_MODEL_ID_FOR_IMAGE_DESC = os.environ.get('BEDROCK_MODEL_ID_IMAGE_DESC', 'us.anthropic.claude-3-5-sonnet-20240620-v1:0')

def extract_qa_pairs(text):
    """Extract question-answer pairs from document text.

    This function analyzes the document text to identify question-answer pairs,
    particularly in FAQ-style documents or documents with clear Q&A sections.

    Args:
        text: The document text to analyze

    Returns:
        A list of dictionaries, each containing a question, answer, and estimated page number
    """
    if not text:
        return []

    qa_pairs = []

    # Try to identify Q&A patterns in the text
    lines = text.split('\n')
    current_question = None
    current_answer = []
    current_page = 1
    line_count = 0

    # Estimate 40 lines per page for rough page number assignment
    lines_per_page = 40

    for line in lines:
        line = line.strip()
        line_count += 1

        # Update the estimated page number
        current_page = (line_count // lines_per_page) + 1

        # Skip empty lines
        if not line:
            continue

        # Check if this line looks like a question
        # Common patterns: starts with "Q:", "Question:", contains a question mark, etc.
        is_question = False

        if line.startswith('Q:') or line.startswith('Question:'):
            is_question = True
        elif line.endswith('?'):
            is_question = True
        elif line.lower().startswith('how ') or line.lower().startswith('what ') or line.lower().startswith('why ') or line.lower().startswith('when ') or line.lower().startswith('where ') or line.lower().startswith('who ') or line.lower().startswith('which '):
            is_question = True

        if is_question:
            # If we already have a question, save the previous Q&A pair
            if current_question and current_answer:
                qa_pairs.append({
                    'question': current_question,
                    'answer': '\n'.join(current_answer),
                    'page_number': current_page
                })

            # Start a new question
            current_question = line
            current_answer = []
        elif current_question:
            # This is part of the answer to the current question
            current_answer.append(line)

    # Don't forget to add the last Q&A pair
    if current_question and current_answer:
        qa_pairs.append({
            'question': current_question,
            'answer': '\n'.join(current_answer),
            'page_number': current_page
        })

    print(f"Extracted {len(qa_pairs)} Q&A pairs from document text")
    return qa_pairs

def process_images_without_ai(images, document_id, processed_bucket):
    """Process images without generating AI descriptions.

    This function processes images from a document but skips the AI description generation
    to avoid timeouts. The AI descriptions will be generated in a separate Lambda function.

    Args:
        images: List of image data from the document
        document_id: The document ID
        processed_bucket: The S3 bucket where processed documents are stored

    Returns:
        List of processed image information
    """
    processed_images = []

    for img_idx, img_data_from_input in enumerate(images):
        image_info = {
            'source_bucket': img_data_from_input.get('source_bucket', processed_bucket),
            'source_key': img_data_from_input.get('source_key', ''),
            'page_number': img_data_from_input.get('page_number', 0),
            'file_type': img_data_from_input.get('file_type', ''),
            'text_content': img_data_from_input.get('text_content', ''),
            's3_uri': img_data_from_input.get('s3_uri', '')
        }

        # Add extracted image information if available
        if 'extracted_image_s3_uri' in img_data_from_input and img_data_from_input['extracted_image_s3_uri']:
            image_info['extracted_image_key'] = img_data_from_input.get('extracted_image_key', '')
            image_info['extracted_image_s3_uri'] = img_data_from_input['extracted_image_s3_uri']
            print(f"Processing image {img_idx}: {image_info['extracted_image_s3_uri']}")
        elif 'extraction_error' in img_data_from_input:
            image_info['extraction_error'] = img_data_from_input['extraction_error']
            print(f"Image extraction error: {img_data_from_input['extraction_error']}")

        processed_images.append(image_info)

    print(f"Processed {len(processed_images)} images without AI descriptions")
    return processed_images

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

        print(f"Processing document with {len(images)} images")

        # Process extracted images if available
        processed_images = []
        if images: # If there's an 'images' array in the document_content_data
            for img_idx, img_data_from_input in enumerate(images):
                image_info = {
                    'source_bucket': img_data_from_input.get('source_bucket', processed_bucket), # Default to current processed bucket
                    'source_key': img_data_from_input.get('source_key', processed_key),       # Default to current processed key
                    'page_number': img_data_from_input.get('page_number', 1 if is_image else 0), # Default to 1 if it's an image doc
                    'file_type': img_data_from_input.get('file_type', document_content_data.get('document_type')),
                    'text_content': img_data_from_input.get('text_content', document_content_data.get('text_content', '') if is_image else ''),
                    's3_uri': img_data_from_input.get('s3_uri') # This is the URI of the image file or PDF page
                }

                if is_image:
                    # The document being processed IS an image.
                    # The 'img_data_from_input' (from its own 'images' array) refers to itself.
                    # Its 's3_uri' is the actual image URI.
                    image_info['extracted_image_s3_uri'] = img_data_from_input.get('s3_uri')
                    image_info['extracted_image_key'] = os.path.basename(img_data_from_input.get('s3_uri', '')) if img_data_from_input.get('s3_uri') else processed_key
                    print(f"Processing image doc: using its s3_uri for extracted_image_s3_uri: {image_info['extracted_image_s3_uri']}")
                elif 'extracted_image_s3_uri' in img_data_from_input and img_data_from_input['extracted_image_s3_uri']:
                    # This came from PyMuPDF extracting an image from a PDF
                    image_info['extracted_image_key'] = img_data_from_input.get('extracted_image_key')
                    image_info['extracted_image_s3_uri'] = img_data_from_input['extracted_image_s3_uri']
                    print(f"Processing PDF-extracted image: using extracted_image_s3_uri: {image_info['extracted_image_s3_uri']}")
                elif 'extraction_error' in img_data_from_input:
                    print(f"Image extraction error noted: {img_data_from_input['extraction_error']}")
                    image_info['extraction_error'] = img_data_from_input['extraction_error']

                processed_images.append(image_info)
        elif is_image: # Document is an image itself, but 'images' array was empty/missing in its JSON
            print(f"Document itself is an image ({processed_bucket}/{processed_key}) and no 'images' array was found in its content. Creating self-referential entry.")
            processed_images.append({
                'source_bucket': processed_bucket,
                'source_key': processed_key,
                'page_number': 1,
                'file_type': document_content_data.get('document_type', ''),
                'text_content': document_content_data.get('text_content', ''),
                's3_uri': f"s3://{processed_bucket}/{processed_key}",
                'extracted_image_s3_uri': f"s3://{processed_bucket}/{processed_key}",
                'extracted_image_key': processed_key
            })

        print(f"Processed {len(processed_images)} images")

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
            'images': processed_images,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'status': 'PROCESSED'
        }

        # --- New logic to generate AI descriptions for images ---
        if is_image:
            # The document itself is an image. Generate a description for it.
            # Determine its S3 bucket and key for the Bedrock call.
            image_s3_uri_to_describe = None
            image_bucket_to_describe = None
            image_key_to_describe = None

            if processed_images and processed_images[0].get('extracted_image_s3_uri'): # Should be self-referential
                image_s3_uri_to_describe = processed_images[0]['extracted_image_s3_uri']
            elif processed_key.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.tiff')): # Fallback if processed_images wasn't set up as expected
                image_s3_uri_to_describe = f"s3://{processed_bucket}/{processed_key}"

            if image_s3_uri_to_describe:
                uri_parts = image_s3_uri_to_describe.replace("s3://", "").split("/", 1)
                if len(uri_parts) == 2:
                    image_bucket_to_describe, image_key_to_describe = uri_parts

                    print(f"Document is an image. Generating AI description for: {image_s3_uri_to_describe}")
                    ai_description = get_image_description_from_bedrock(image_bucket_to_describe, image_key_to_describe)
                    item['image_description'] = ai_description # Set main item's image_description

                    # Update the corresponding entry in processed_images (should be the first/only one)
                    if processed_images:
                        processed_images[0]['ai_generated_description'] = ai_description
                        processed_images[0]['text_content'] = ai_description # Use AI desc for indexing text_content
                    else: # Should have been created by the logic at line 204 if is_image was true
                        print(f"Warning: is_image is True, but processed_images is empty. Description for {document_id} set, but not in images list.")
            else:
                print(f"Could not determine S3 URI for AI description of image document: {document_id}")
                item['image_description'] = metadata.get('image_description', 'Description not available.') # Fallback

        else: # Document is not an image (e.g., PDF), but might contain extracted images
            item['image_description'] = metadata.get('image_description', '') # This is description of the whole PDF text

            # Extract Q&A content from the document text if available
            document_text = document_content_data.get('text_content', '')
            qa_pairs = extract_qa_pairs(document_text)

            if qa_pairs:
                print(f"Extracted {len(qa_pairs)} Q&A pairs from document")

                # Instead of storing all Q&A pairs directly in the item, store them in S3
                # and keep a reference in the DynamoDB item
                qa_pairs_key = f"qa_pairs/{document_id}_{str(uuid.uuid4())}.json"

                try:
                    # Store the Q&A pairs in S3
                    s3_client.put_object(
                        Bucket=processed_bucket,
                        Key=qa_pairs_key,
                        Body=json.dumps(qa_pairs),
                        ContentType='application/json'
                    )

                    print(f"Stored {len(qa_pairs)} Q&A pairs in S3: {processed_bucket}/{qa_pairs_key}")

                    # Store a reference to the S3 object in the DynamoDB item
                    item['qa_pairs_s3_key'] = qa_pairs_key
                    item['qa_pairs_s3_bucket'] = processed_bucket
                    item['qa_pairs_count'] = len(qa_pairs)

                    # Store a small subset of Q&A pairs directly in the item for quick access
                    # (just the first few pairs)
                    max_inline_pairs = 5
                    item['qa_pairs_sample'] = qa_pairs[:max_inline_pairs] if len(qa_pairs) > max_inline_pairs else qa_pairs

                except Exception as e:
                    print(f"Error storing Q&A pairs in S3: {str(e)}")
                    # Fall back to storing a limited number of Q&A pairs directly in the item
                    print("Falling back to storing limited Q&A pairs directly in the item")
                    item['qa_pairs'] = qa_pairs[:10]  # Store only the first 10 pairs to limit size

            # We'll skip generating AI descriptions for images here to avoid timeouts
            # Instead, we'll just associate Q&A pairs with images and let the image-description-generator Lambda handle the AI descriptions
            for img_info in processed_images:
                if img_info.get('extracted_image_s3_uri') and not img_info.get('extraction_error'):
                    # Find the closest Q&A pair to this image based on page number
                    img_page = img_info.get('page_number', 0)
                    associated_qa = None

                    # Get Q&A pairs - either from the direct item or from S3
                    qa_pairs_to_use = []

                    if 'qa_pairs' in item:
                        # Use Q&A pairs directly from the item
                        qa_pairs_to_use = item['qa_pairs']
                    elif 'qa_pairs_sample' in item:
                        # Use the sample Q&A pairs from the item
                        qa_pairs_to_use = item['qa_pairs_sample']
                        print(f"Using {len(qa_pairs_to_use)} sample Q&A pairs from the item")
                    elif 'qa_pairs_s3_key' in item and 'qa_pairs_s3_bucket' in item:
                        # Retrieve Q&A pairs from S3
                        try:
                            qa_s3_response = s3_client.get_object(
                                Bucket=item['qa_pairs_s3_bucket'],
                                Key=item['qa_pairs_s3_key']
                            )
                            qa_pairs_to_use = json.loads(qa_s3_response['Body'].read().decode('utf-8'))
                            print(f"Retrieved {len(qa_pairs_to_use)} Q&A pairs from S3")
                        except Exception as e:
                            print(f"Error retrieving Q&A pairs from S3: {str(e)}")
                            # Fall back to using the sample if available
                            if 'qa_pairs_sample' in item:
                                qa_pairs_to_use = item['qa_pairs_sample']
                                print(f"Falling back to {len(qa_pairs_to_use)} sample Q&A pairs")

                    if qa_pairs_to_use:
                        # Try to find a Q&A pair on the same page
                        for qa_pair in qa_pairs_to_use:
                            if qa_pair.get('page_number') == img_page:
                                associated_qa = qa_pair
                                print(f"Found Q&A pair on page {img_page} for image: {img_info['extracted_image_s3_uri']}")
                                break

                        # If no Q&A pair on the same page, use the closest one
                        if not associated_qa and qa_pairs_to_use:
                            closest_qa = min(qa_pairs_to_use, key=lambda qa: abs(qa.get('page_number', 0) - img_page))
                            associated_qa = closest_qa
                            print(f"Using closest Q&A pair from page {closest_qa.get('page_number')} for image on page {img_page}")

                    # Store the associated Q&A pair with the image
                    if associated_qa:
                        img_info['associated_qa'] = associated_qa

                    # Set a placeholder for text_content - will be updated by the image-description-generator Lambda
                    if associated_qa:
                        img_info['text_content'] = f"Question: {associated_qa.get('question', '')}\nAnswer: {associated_qa.get('answer', '')}"
                    else:
                        img_info['text_content'] = "Image content will be processed separately."

        # Store images in S3 if there are many of them to avoid exceeding DynamoDB item size limits
        if len(processed_images) > 10:
            # Store the full processed_images list in S3
            images_s3_key = f"images/{document_id}_{str(uuid.uuid4())}.json"

            try:
                # Store the images in S3
                s3_client.put_object(
                    Bucket=processed_bucket,
                    Key=images_s3_key,
                    Body=json.dumps(processed_images),
                    ContentType='application/json'
                )

                print(f"Stored {len(processed_images)} images in S3: {processed_bucket}/{images_s3_key}")

                # Store a reference to the S3 object in the DynamoDB item
                item['images_s3_key'] = images_s3_key
                item['images_s3_bucket'] = processed_bucket
                item['images_count'] = len(processed_images)

                # Store a small subset of images directly in the item for quick access
                # (just the first few images)
                max_inline_images = 5
                item['images'] = processed_images[:max_inline_images]

            except Exception as e:
                print(f"Error storing images in S3: {str(e)}")
                # Fall back to storing a limited number of images directly in the item
                print("Falling back to storing limited images directly in the item")
                item['images'] = processed_images[:10]  # Store only the first 10 images to limit size
        else:
            # If there aren't many images, store them directly in the item
            item['images'] = processed_images # Ensure item['images'] has the updated list with AI descriptions

        # Add the item to DynamoDB
        table.put_item(Item=item)

        # Create search indices for the document
        search_indices = create_search_indices(item, document_content_data)

        # If this is an image or has embedded images, create special image search indices
        print(f"Before calling create_image_search_indices. is_image: {is_image}")
        if processed_images:
            print(f"processed_images contains {len(processed_images)} items.")
            for i, p_img in enumerate(processed_images):
                print(f"  processed_image {i}: extracted_uri: {p_img.get('extracted_image_s3_uri')}, error: {p_img.get('extraction_error')}")
        else:
            print("processed_images is empty or None.")

        if is_image or processed_images:
            print(f"Calling create_image_search_indices for document_id: {item.get('document_id')}")
            image_search_indices = create_image_search_indices(item, document_content_data)
            search_indices.extend(image_search_indices)
            print(f"Extended search_indices with {len(image_search_indices)} image search indices.")
        else:
            print("Skipping create_image_search_indices as is_image is false and processed_images is empty.")

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
                        'id': item['id'],
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

    # Print debug information about images
    print(f"Creating image search indices for document: {metadata_item['document_id']}")
    print(f"Is image document: {metadata_item.get('is_image', False)}")
    print(f"Number of embedded images: {len(metadata_item.get('images', []))}")

    # Get the images - either directly from the item or from S3
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

    # Log the actual images for debugging
    for i, img in enumerate(images_to_process):
        print(f"Image {i+1}:")
        print(f"  S3 URI: {img.get('s3_uri', 'None')}")
        print(f"  Extracted image S3 URI: {img.get('extracted_image_s3_uri', 'None')}")
        print(f"  Extraction error: {img.get('extraction_error', 'None')}")
        if 'associated_qa' in img:
            print(f"  Associated Q&A: {img['associated_qa'].get('question', '')[:50]}...")

    # Extract text content from the document
    document_text = document_content.get('text_content', '')
    if not document_text and isinstance(document_content, dict):
        # Try to find text content in nested structure
        for key, value in document_content.items():
            if isinstance(value, dict) and 'text_content' in value:
                document_text = value['text_content']
                print(f"Found text content in nested field: {key}.text_content")
                break

    # Get Q&A pairs from metadata if available - either directly or from S3
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

    if qa_pairs:
        print(f"Using {len(qa_pairs)} Q&A pairs for image indexing")

    # If this is an image document, create indices for the text content
    if metadata_item.get('is_image', False):
        # Create a special image content index
        image_s3_uri = f"s3://{metadata_item['original_bucket']}/{metadata_item['original_key']}"
        print(f"Creating image content index for: {image_s3_uri}")

        index_item = {
            'id': str(uuid.uuid4()),
            'document_id': metadata_item['document_id'],
            'metadata_id': metadata_item['id'],
            'index_type': 'image_content',
            'index_value': document_text[:1000],  # Limit to 1000 chars
            'image_s3_uri': image_s3_uri,
            'image_description': metadata_item.get('image_description', ''),
            'created_at': datetime.now().isoformat()
        }

        # Add the search index to DynamoDB
        table.put_item(Item=index_item)
        search_indices.append(index_item)

    # For documents with embedded images, create indices for each image
    for i, image in enumerate(images_to_process):
        # First check for extracted image URI, then fall back to PDF page URI
        image_s3_uri = image.get('extracted_image_s3_uri', image.get('s3_uri', ''))

        print(f"Creating index for image {i+1} with URI: {image_s3_uri}")

        # If we have image data but no S3 URI, upload the image to S3
        if not image_s3_uri and 'image_data' in image:
            try:
                # Generate a unique key for the image
                image_key = f"images/{metadata_item['document_id']}/image_{i}_{uuid.uuid4()}.png"

                # Upload the image to S3
                s3_client.put_object(
                    Bucket=metadata_item['processed_bucket'],
                    Key=image_key,
                    Body=image['image_data'],
                    ContentType='image/png'
                )

                # Update the S3 URI
                image_s3_uri = f"s3://{metadata_item['processed_bucket']}/{image_key}"
                print(f"Uploaded image to S3: {image_s3_uri}")
            except Exception as e:
                print(f"Error uploading image to S3: {str(e)}")

        if not image_s3_uri:
            print(f"Warning: No S3 URI for image {i} in document {metadata_item['document_id']}")
            continue

        # Get text content associated with this image. Prioritize AI-generated description.
        # The 'text_content' field in 'image' should have already been updated to ai_generated_description
        # in the lambda_handler if AI description was successful.
        image_text = image.get('text_content', '') # This should be the AI description now

        if not image_text and document_text: # Fallback if image_text is still empty for some reason
            print(f"Warning: image_text for image {i+1} is empty. Falling back to document_text for index_value.")
            image_text = document_text[:1000]
        elif not image_text:
            print(f"Warning: image_text for image {i+1} and document_text are both empty. index_value will be empty.")
            image_text = "" # Ensure it's a string

        # Create a description for the image. Prioritize AI-generated, then specific, then main, then fallback.
        image_description = image.get('ai_generated_description', image.get('description', metadata_item.get('image_description', '')))
        if not image_description:
            image_description = f"Image {i+1} from document {metadata_item['document_id']}"

        print(f"Creating embedded image index for: {image_s3_uri}")
        print(f"Using Image description for index item: {image_description[:100]}...")
        print(f"Using Text content for index_value: {image_text[:100]}...")

        # Create a unique ID for this index to avoid duplicates
        index_id = str(uuid.uuid4())

        # Check if this image has an associated Q&A pair
        associated_qa = image.get('associated_qa')

        # Create two indices - one for the extracted image and one for the PDF page
        # This ensures we can find the image regardless of which URI is used in the query

        # Index for the extracted image or primary image URI
        index_item = {
            'id': index_id,
            'document_id': metadata_item['document_id'],
            'metadata_id': metadata_item['id'],
            'index_type': 'embedded_image',
            'index_value': image_text[:1000],  # Limit to 1000 chars
            'image_s3_uri': image_s3_uri,
            'image_description': image_description,
            'image_position': i,
            'created_at': datetime.now().isoformat()
        }

        # Add Q&A information if available
        if associated_qa:
            index_item['question'] = associated_qa.get('question', '')
            index_item['answer'] = associated_qa.get('answer', '')
            index_item['page_number'] = associated_qa.get('page_number', 0)
            # Set a special index type for Q&A images to prioritize them in search
            index_item['index_type'] = 'qa_image'
            print(f"Created Q&A image index for question: {associated_qa.get('question', '')[:50]}...")

        # Add extracted image URI if available
        if 'extracted_image_s3_uri' in image:
            index_item['extracted_image_s3_uri'] = image['extracted_image_s3_uri']

        # Add the search index to DynamoDB
        try:
            table.put_item(Item=index_item)
            print(f"Successfully added index {index_id} to DynamoDB")
            search_indices.append(index_item)
        except Exception as e:
            print(f"Error adding index to DynamoDB: {str(e)}")

        # If we have both extracted image and PDF page URIs, create an additional index for the PDF page
        pdf_page_uri = image.get('s3_uri', '')
        if pdf_page_uri and pdf_page_uri != image_s3_uri:
            pdf_index_item = {
                'id': str(uuid.uuid4()),
                'document_id': metadata_item['document_id'],
                'metadata_id': metadata_item['id'],
                'index_type': 'pdf_page_image',
                'index_value': image_text[:1000],  # Limit to 1000 chars
                'image_s3_uri': pdf_page_uri,
                'extracted_image_s3_uri': image_s3_uri,  # Reference to the extracted image
                'image_description': image_description,
                'image_position': i,
                'created_at': datetime.now().isoformat()
            }

            # Add Q&A information to the PDF page index as well
            if associated_qa:
                pdf_index_item['question'] = associated_qa.get('question', '')
                pdf_index_item['answer'] = associated_qa.get('answer', '')
                pdf_index_item['page_number'] = associated_qa.get('page_number', 0)
                # Set a special index type for Q&A PDF pages
                pdf_index_item['index_type'] = 'qa_pdf_page'

            # Add the PDF page index to DynamoDB
            table.put_item(Item=pdf_index_item)
            search_indices.append(pdf_index_item)

        # Create additional indices for different sections of text if the document is long
        if len(document_text) > 1000:
            # Create indices for different sections of the document
            section_size = 1000
            for j in range(1, min(5, len(document_text) // section_size)):  # Up to 5 sections
                section_start = j * section_size
                section_text = document_text[section_start:section_start + section_size]

                # Create a unique ID for this section index
                section_index_id = str(uuid.uuid4())

                section_index_item = {
                    'id': section_index_id,
                    'document_id': metadata_item['document_id'],
                    'metadata_id': metadata_item['id'],
                    'index_type': 'embedded_image_section',
                    'index_value': section_text,
                    'image_s3_uri': image_s3_uri,
                    'image_description': image_description,
                    'image_position': i,
                    'section': j,
                    'created_at': datetime.now().isoformat()
                }

                # Add extracted image URI if available
                if 'extracted_image_s3_uri' in image:
                    section_index_item['extracted_image_s3_uri'] = image['extracted_image_s3_uri']

                # Add the section index to DynamoDB
                try:
                    table.put_item(Item=section_index_item)
                    print(f"Successfully added section index {section_index_id} to DynamoDB")
                    search_indices.append(section_index_item)
                except Exception as e:
                    print(f"Error adding section index to DynamoDB: {str(e)}")

    print(f"Created {len(search_indices)} image search indices")
    return search_indices
