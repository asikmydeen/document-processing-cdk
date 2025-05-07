import json
import os
import boto3
import uuid
import urllib.parse
import shutil
import tempfile
from datetime import datetime
import sys

# Lambda automatically adds /opt/bin to PATH and /opt/python to sys.path
# No need for manual path manipulation if layer is structured correctly.
# Print paths for debugging during initial setup.
print(f"Initial PATH: {os.environ.get('PATH')}")
print(f"Initial sys.path: {sys.path}")
# Ensure /opt/bin is in PATH for Poppler, Lambda should do this.
# If not, this indicates a deeper environment issue or a very unusual Lambda runtime.
if '/opt/bin' not in os.environ.get('PATH', '').split(':'):
    print("Warning: /opt/bin was not found in PATH automatically. Adding it.")
    os.environ['PATH'] = f"/opt/bin:{os.environ.get('PATH', '')}"
    print(f"Updated PATH: {os.environ.get('PATH')}")

# Custom exception classes
class ValidationError(Exception):
    """Raised when input validation fails"""
    pass

class TextractParseError(Exception):
    """Raised when Textract response parsing fails"""
    pass

# Initialize AWS clients
s3_client = boto3.client('s3')
textract_client = boto3.client('textract')
bedrock_runtime = boto3.client('bedrock-runtime')

def get_file_extension(key: str) -> str:
    """Get the file extension from the key."""
    _, file_extension = os.path.splitext(key)
    return file_extension.lower()

def process_document(bucket: str, key: str, enable_image_extraction: bool = True) -> dict:
    """Process document using AWS Textract based on file type."""
    file_extension = get_file_extension(key)

    print(f"Processing document with file extension: {file_extension}")
    print(f"Image extraction enabled: {enable_image_extraction}")

    # Determine document type and processing method
    if file_extension in ['.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif']:
        return process_document_with_textract(bucket, key, enable_image_extraction)
    elif file_extension in ['.csv', '.txt']:
        return process_text_document(bucket, key)
    elif file_extension in ['.xlsx', '.xls']:
        return process_excel_document(bucket, key, enable_image_extraction)
    elif file_extension in ['.doc', '.docx']:
        return process_word_document(bucket, key, enable_image_extraction)
    else:
        return {
            'status': 'error',
            'message': f'Unsupported file type: {file_extension}',
            'document_type': file_extension,
            'text_content': '',
            'tables': [],
            'forms': [],
            'images': []
        }

def process_document_with_textract(bucket: str, key: str, enable_image_extraction: bool = True) -> dict:
    """Process document using AWS Textract."""
    file_extension = get_file_extension(key)
    is_image = file_extension in ['.png', '.jpg', '.jpeg', '.tiff', '.tif']

    print(f"Processing document with Textract: {key}")
    print(f"File is an image: {is_image}")
    print(f"Image extraction enabled: {enable_image_extraction}")

    # For PDFs, use document analysis
    if file_extension == '.pdf':
        # First, run document analysis to get text, tables, and forms
        response = textract_client.start_document_analysis(
            DocumentLocation={
                'S3Object': {
                    'Bucket': bucket,
                    'Name': key
                }
            },
            FeatureTypes=['TABLES', 'FORMS']
        )
        job_id = response['JobId']

        # Wait for the job to complete
        response = textract_client.get_document_analysis(JobId=job_id)
        while response['JobStatus'] == 'IN_PROGRESS':
            print(f"Waiting for Textract job {job_id} to complete...")
            # Wait for 5 seconds before checking again
            import time
            time.sleep(5)
            response = textract_client.get_document_analysis(JobId=job_id)

        # Process the results
        blocks = []
        next_token = None

        while True:
            if next_token:
                response = textract_client.get_document_analysis(JobId=job_id, NextToken=next_token)
            else:
                response = textract_client.get_document_analysis(JobId=job_id)

            blocks.extend(response['Blocks'])

            if 'NextToken' in response:
                next_token = response['NextToken']
            else:
                break

        # Extract text, tables, and forms
        text_content = extract_text_from_blocks(blocks)
        tables = extract_tables_from_blocks(blocks)
        forms = extract_forms_from_blocks(blocks)

        # Now, run document detection to find images in the PDF
        print(f"Looking for images in PDF: {key}")

        # Start a detection job for images
        response = textract_client.start_document_text_detection(
            DocumentLocation={
                'S3Object': {
                    'Bucket': bucket,
                    'Name': key
                }
            }
        )
        detection_job_id = response['JobId']

        # Wait for the detection job to complete
        response = textract_client.get_document_text_detection(JobId=detection_job_id)
        while response['JobStatus'] == 'IN_PROGRESS':
            print(f"Waiting for Textract detection job {detection_job_id} to complete...")
            time.sleep(5)
            response = textract_client.get_document_text_detection(JobId=detection_job_id)

        # Process the detection results to find images
        detection_blocks = []
        next_token = None

        while True:
            if next_token:
                response = textract_client.get_document_text_detection(JobId=detection_job_id, NextToken=next_token)
            else:
                response = textract_client.get_document_text_detection(JobId=detection_job_id)

            detection_blocks.extend(response['Blocks'])

            if 'NextToken' in response:
                next_token = response['NextToken']
            else:
                break

        # Extract images from the detection blocks if enabled
        if enable_image_extraction:
            print(f"Extracting images from PDF: {key}")
            try:
                images = extract_images_from_blocks(detection_blocks, bucket, key)
                print(f"Successfully extracted {len(images)} images from PDF")
            except Exception as img_error:
                print(f"Error extracting images from PDF: {str(img_error)}")
                # Continue with empty images list if extraction fails
                images = []
        else:
            print("Image extraction disabled - skipping PDF image extraction")
            images = []

    # For images, use document detection
    else:
        response = textract_client.detect_document_text(
            Document={
                'S3Object': {
                    'Bucket': bucket,
                    'Name': key
                }
            }
        )

        # Extract text
        text_content = ""
        for item in response['Blocks']:
            if item['BlockType'] == 'LINE':
                text_content += item['Text'] + '\n'

        # For images, we don't have tables and forms
        tables = []
        forms = []

        # For images, store the image reference
        if is_image:
            images = [{
                'source_bucket': bucket,
                'source_key': key,
                'file_type': file_extension,
                'text_content': text_content,
                's3_uri': f"s3://{bucket}/{key}"
            }]
        else:
            images = []

    return {
        'status': 'success',
        'document_type': file_extension,
        'text_content': text_content,
        'tables': tables,
        'forms': forms,
        'images': images,
        'is_image': is_image
    }

def process_text_document(bucket: str, key: str) -> dict:
    """Process text documents (CSV, TXT)."""
    # Download the file from S3
    download_path = f"/tmp/{os.path.basename(key)}"
    s3_client.download_file(bucket, key, download_path)

    # Read the content
    with open(download_path, 'r') as file:
        text_content = file.read()

    return {
        'status': 'success',
        'document_type': get_file_extension(key),
        'text_content': text_content,
        'tables': [],
        'forms': [],
        'images': [],
        'is_image': False
    }

def process_excel_document(bucket: str, key: str, enable_image_extraction: bool = True) -> dict:
    """Process Excel documents."""
    # For Excel files, we'll use Textract to extract tables
    result = process_document_with_textract(bucket, key, enable_image_extraction)
    result['is_image'] = False
    return result

def process_word_document(bucket: str, key: str, enable_image_extraction: bool = True) -> dict:
    """Process Word documents."""
    # For Word files, we'll use Textract to extract text, tables, and forms
    result = process_document_with_textract(bucket, key, enable_image_extraction)
    result['is_image'] = False
    return result

def extract_text_from_blocks(blocks: list) -> str:
    """Extract text from Textract blocks."""
    try:
        text_content = ""
        for block in blocks:
            if block['BlockType'] == 'LINE':
                text_content += block['Text'] + '\n'
        return text_content
    except (KeyError, IndexError) as e:
        raise TextractParseError(f"Error extracting text from Textract blocks: {str(e)}")

def extract_tables_from_blocks(blocks: list) -> list:
    """Extract tables from Textract blocks."""
    tables = []
    # Implementation for extracting tables from blocks
    # This is a simplified version - a full implementation would be more complex
    try:
        table_blocks = [block for block in blocks if block['BlockType'] == 'TABLE']

        for table_block in table_blocks:
            table_id = table_block['Id']
            table_cells = []

            # Safely collect cells that belong to this table
            for block in blocks:
                if block['BlockType'] == 'CELL':
                    relationships = block.get('Relationships', [])
                    for rel in relationships:
                        if 'Ids' in rel and table_id in rel.get('Ids', []):
                            table_cells.append(block)
                            break

            # Organize cells into a table structure
            table_data = {}
            for cell in table_cells:
                row_index = cell['RowIndex']
                col_index = cell['ColumnIndex']

                # Safely extract cell content
                cell_content = ''
                relationships = cell.get('Relationships', [])
                child_rel = next((rel for rel in relationships if rel.get('Type') == 'CHILD'), None)

                if child_rel and 'Ids' in child_rel:
                    child_ids = child_rel.get('Ids', [])
                    cell_content = ' '.join(block['Text'] for block in blocks if block['Id'] in child_ids and block['BlockType'] == 'WORD')

                if row_index not in table_data:
                    table_data[row_index] = {}

                table_data[row_index][col_index] = cell_content

            tables.append(table_data)
    except (KeyError, IndexError) as e:
        raise TextractParseError(f"Error parsing tables from Textract blocks: {str(e)}")

    return tables

def extract_forms_from_blocks(blocks: list) -> list:
    """Extract forms (key-value pairs) from Textract blocks."""
    forms = []
    # Implementation for extracting forms from blocks
    # This is a simplified version - a full implementation would be more complex
    try:
        key_blocks = [block for block in blocks if block['BlockType'] == 'KEY_VALUE_SET' and 'EntityTypes' in block and 'KEY' in block['EntityTypes']]

        for key_block in key_blocks:
            # key_id = key_block['Id']  # Not used but kept for reference
            key_text = ""

            # Get the key text safely
            relationships = key_block.get('Relationships', [])
            child_rel = next((rel for rel in relationships if rel.get('Type') == 'CHILD'), None)

            if child_rel and 'Ids' in child_rel:
                child_ids = child_rel.get('Ids', [])
                key_text = ' '.join(block['Text'] for block in blocks if block['Id'] in child_ids and block['BlockType'] == 'WORD')

            # Find the corresponding value block safely
            value_block = None
            relationships = key_block.get('Relationships', [])
            value_rel = next((rel for rel in relationships if rel.get('Type') == 'VALUE'), None)

            if value_rel and 'Ids' in value_rel:
                value_ids = value_rel.get('Ids', [])
                if value_ids:  # Make sure we have at least one ID
                    value_block = next((block for block in blocks if block['Id'] == value_ids[0]), None)

            value_text = ""
            if value_block:
                relationships = value_block.get('Relationships', [])
                child_rel = next((rel for rel in relationships if rel.get('Type') == 'CHILD'), None)

                if child_rel and 'Ids' in child_rel:
                    child_ids = child_rel.get('Ids', [])
                    value_text = ' '.join(block['Text'] for block in blocks if block['Id'] in child_ids and block['BlockType'] == 'WORD')

            if key_text and value_text:
                forms.append({key_text: value_text})
    except (KeyError, IndexError) as e:
        raise TextractParseError(f"Error parsing forms from Textract blocks: {str(e)}")

    return forms

def extract_images_from_blocks(blocks: list, bucket: str, key: str) -> list:
    """Extract images from Textract blocks (primarily for PDFs) using PyMuPDF."""
    images_metadata = []
    
    try:
        import fitz  # PyMuPDF
        print("Successfully imported fitz (PyMuPDF).")
    except ImportError as import_err:
        print(f"CRITICAL: Failed to import fitz (PyMuPDF): {str(import_err)}")
        print("Ensure 'PyMuPDF' package is correctly installed in the Lambda layer.")
        # Return a single error entry if PyMuPDF itself can't be imported
        return [{'error': 'PyMuPDF (fitz) library not available or import failed', 'details': str(import_err), 'source_key': key}]
    except Exception as general_import_err:
        print(f"CRITICAL: An unexpected error occurred during PyMuPDF import: {str(general_import_err)}")
        return [{'error': 'Unexpected error during PyMuPDF import', 'details': str(general_import_err), 'source_key': key}]

    # Textract `blocks` are passed but not directly used by PyMuPDF for image extraction from the original PDF.
    # PyMuPDF will open the PDF directly from S3.
    # We can use the page information from blocks if needed to decide which pages to process,
    # but for full image extraction, PyMuPDF iterates through pages itself.

    # Create a temporary file to download the PDF to
    with tempfile.NamedTemporaryFile(suffix=".pdf") as temp_pdf_file:
        try:
            print(f"Downloading s3://{bucket}/{key} to {temp_pdf_file.name}")
            s3_client.download_file(bucket, key, temp_pdf_file.name)
            pdf_document = fitz.open(temp_pdf_file.name)
        except Exception as e:
            print(f"Error opening PDF s3://{bucket}/{key} with PyMuPDF: {str(e)}")
            images_metadata.append({
                'source_bucket': bucket,
                'source_key': key,
                'extraction_error': f"Failed to open PDF: {str(e)}"
            })
            return images_metadata # Return early if PDF can't be opened

        print(f"Processing PDF {key} with {len(pdf_document)} pages using PyMuPDF.")
        for page_num in range(len(pdf_document)):
            page_info = {
                'source_bucket': bucket,
                'source_key': key,
                'page_number': page_num + 1, # 1-indexed for user display
                'file_type': '.pdf'
                # 'text_content' could be added here if we also extract text per page with PyMuPDF
            }
            try:
                image_list = pdf_document[page_num].get_images(full=True)
                if not image_list:
                    # No images on this page, or Textract didn't identify this page as having image content
                    # We can still add a page entry if desired, or skip if only pages with images are needed.
                    # For now, let's assume we only care about pages where PyMuPDF finds images.
                    # If Textract blocks indicated content, we might still want a record.
                    # This part depends on whether 'blocks' should guide which pages to report on.
                    # For simplicity, we'll only report if PyMuPDF extracts an image.
                    pass

                for img_index, img_props in enumerate(image_list):
                    xref = img_props[0]
                    base_image = pdf_document.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    
                    # Sanitize file extension
                    if not image_ext or len(image_ext) > 4:
                        image_ext = "png" # Default to png if extension is weird

                    extracted_image_s3_key = f"extracted_images/{os.path.basename(key).split('.')[0]}_page{page_num + 1}_img{img_index + 1}.{image_ext}"

                    with tempfile.NamedTemporaryFile(suffix=f".{image_ext}") as temp_img_file:
                        temp_img_file.write(image_bytes)
                        temp_img_file.flush() # Ensure all data is written before S3 upload

                        s3_client.upload_file(
                            temp_img_file.name,
                            bucket, # Assuming processedBucket is the target for extracted images
                            extracted_image_s3_key,
                            ExtraArgs={'ContentType': f'image/{image_ext}'}
                        )
                    
                    # Create a new entry for each successfully extracted image
                    # This is different from the pdf2image approach which created one image per page.
                    # PyMuPDF extracts individual embedded images.
                    image_entry = {
                        'source_bucket': bucket,
                        'source_key': key,
                        'page_number': page_num + 1,
                        'image_index_on_page': img_index + 1,
                        'extracted_image_s3_uri': f"s3://{bucket}/{extracted_image_s3_key}",
                        'extracted_image_key': extracted_image_s3_key,
                        'original_image_extension': image_ext,
                        's3_uri': f"s3://{bucket}/{key}#page={page_num+1}" # Reference to original PDF page
                    }
                    images_metadata.append(image_entry)
                    print(f"Extracted image {img_index + 1} from page {page_num + 1} of {key} to {extracted_image_s3_key}")

            except Exception as page_err:
                err_msg = f"Error processing page {page_num + 1} of {key} with PyMuPDF: {str(page_err)}"
                print(err_msg)
                # Add error info for this specific page if it fails
                page_info['extraction_error'] = err_msg
                images_metadata.append(page_info) # Append page_info with error

        pdf_document.close()

    if not images_metadata and len(pdf_document) > 0 : # If no images were extracted but PDF was processed
        images_metadata.append({
            'source_bucket': bucket,
            'source_key': key,
            'info': 'PDF processed by PyMuPDF, but no images were extracted or an error occurred early.',
            'page_count': len(pdf_document) if 'pdf_document' in locals() else 0
        })
    elif not images_metadata: # PDF could not be opened or had 0 pages
         images_metadata.append({
            'source_bucket': bucket,
            'source_key': key,
            'info': 'PDF could not be opened or was empty.'
        })


    print(f"PyMuPDF processing for {key} resulted in {len(images_metadata)} image/page entries.")
    return images_metadata

def generate_metadata_with_bedrock(document_content: dict) -> dict:
    """Generate metadata using Amazon Bedrock."""
    # Prepare the prompt for Bedrock
    prompt = f"""
    I have a document with the following content:

    Text: {document_content['text_content'][:2000]}  # Limiting to first 2000 chars for brevity

    Document Type: {document_content['document_type']}
    Is Image: {document_content['is_image']}

    Please analyze this document and provide the following metadata:
    1. A concise title for the document
    2. A summary of the document content (max 200 words)
    3. Key topics or themes in the document (comma-separated)
    4. Document category (e.g., financial, legal, technical, etc.)
    5. Entities mentioned (people, organizations, locations, etc.)
    6. If this is an image, describe what the image appears to contain based on the text

    Format your response as JSON with the following structure:
    {{
        "title": "Document Title",
        "summary": "Document summary...",
        "topics": ["topic1", "topic2", "topic3"],
        "category": "document category",
        "entities": {{
            "people": ["person1", "person2"],
            "organizations": ["org1", "org2"],
            "locations": ["location1", "location2"]
        }},
        "image_description": "Description of the image content (if applicable)"
    }}
    """

    # Call Bedrock with Claude 3.5 Sonnet model
    # Get the Bedrock model ID from environment variable, with a default
    bedrock_model_id = os.environ.get('BEDROCK_MODEL_ID', 'us.anthropic.claude-3-5-sonnet-20240620-v1:0')
    print(f"Using Bedrock model ID: {bedrock_model_id}")

    response = bedrock_runtime.invoke_model(
        modelId=bedrock_model_id,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        })
    )

    # Parse the response - Claude 3.5 Sonnet uses a different response format
    response_body = json.loads(response['body'].read())
    # Get the content from the assistant's message
    completion = response_body.get('content', [{}])[0].get('text', '')

    # Extract the JSON part from the completion
    try:
        # Find the JSON object in the response
        json_start = completion.find('{')
        json_end = completion.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            metadata_json = completion[json_start:json_end]
            metadata = json.loads(metadata_json)
        else:
            # Fallback if JSON parsing fails
            metadata = {
                "title": "Unknown Document",
                "summary": "Could not generate summary",
                "topics": [],
                "category": "unknown",
                "entities": {
                    "people": [],
                    "organizations": [],
                    "locations": []
                },
                "image_description": "" if document_content['is_image'] else None
            }
    except Exception as e:
        print(f"Error parsing Bedrock response: {str(e)}")
        metadata = {
            "title": "Unknown Document",
            "summary": "Could not generate summary",
            "topics": [],
            "category": "unknown",
            "entities": {
                "people": [],
                "organizations": [],
                "locations": []
            },
            "image_description": "" if document_content['is_image'] else None
        }

    return metadata

def save_processed_document(bucket: str, key: str, document_content: dict, metadata: dict) -> str:
    """Save processed document to the processed bucket."""
    # Create a structured JSON that combines document content and metadata
    processed_document = {
        "document_id": key,
        "original_bucket": bucket,
        "original_key": key,
        "processing_timestamp": datetime.now().isoformat(),  # Replace the DynamoDB hack with direct timestamp
        "document_content": document_content,
        "metadata": metadata
    }

    # Create a processed key that maintains the original path but with a .json extension
    processed_key = os.path.splitext(key)[0] + '.json'

    # Validate processed_key is not empty
    if not processed_key:
        raise ValidationError("Generated processed_key is empty")

    # Get the processed bucket name from environment variables
    processed_bucket = os.environ.get('PROCESSED_BUCKET_NAME')
    if not processed_bucket:
        raise ValidationError("PROCESSED_BUCKET_NAME environment variable not set")

    try:
        # Save to the processed bucket
        s3_client.put_object(
            Bucket=processed_bucket,
            Key=processed_key,
            Body=json.dumps(processed_document, indent=2),
            ContentType='application/json'
        )

        return processed_key
    except Exception as e:
        raise Exception(f"Failed to save processed document: {str(e)}")

def lambda_handler(event, context):
    """Lambda function handler."""
    print(f"Received event: {json.dumps(event)}")

    try:
        # Get bucket and key from the event
        if 'Records' in event:
            # S3 event notification
            record = event['Records'][0]
            bucket = record['s3']['bucket']['name']
            key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        else:
            # Direct invocation
            bucket = event.get('bucket')
            key = event.get('key')

        if not bucket or not key:
            raise ValidationError('Missing bucket or key parameter')

        try:
            # Check if image extraction is enabled
            enable_image_extraction = event.get('enable_image_extraction', True)
            print(f"Image extraction enabled: {enable_image_extraction}")

            # Process the document
            document_content = process_document(bucket, key, enable_image_extraction)

            # Generate metadata using Bedrock
            metadata = generate_metadata_with_bedrock(document_content)

            # Save processed document
            processed_key = save_processed_document(bucket, key, document_content, metadata)

            # Return success response
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Document processed successfully',
                    'document_id': key,
                    'processed_key': processed_key,
                    'metadata': metadata,
                    'is_image': document_content.get('is_image', False),
                    'images': document_content.get('images', []),
                    'image_extraction_enabled': enable_image_extraction
                })
            }
        except TextractParseError as e:
            print(f"Error parsing Textract response: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'message': f'Error parsing Textract response: {str(e)}',
                    'document_id': key,
                    'error_type': 'TextractParseError'
                })
            }

    except ValidationError as e:
        print(f"Validation error: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({
                'message': f'Validation error: {str(e)}',
                'error_type': 'ValidationError'
            })
        }
    except Exception as e:
        print(f"Error processing document: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error processing document: {str(e)}',
                'error_type': 'GeneralError'
            })
        }
