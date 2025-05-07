# Image Extraction and Display Enhancement

This update enhances the document processing system to properly extract and display images from documents in knowledge base responses.

## Problem Solved

The original implementation had the following issues:
1. Images in PDFs were only referenced by page number but not extracted as separate image files
2. When querying the knowledge base, these PDF page references couldn't be displayed as images
3. The structured response included image references, but they pointed to PDF pages, not actual image files

## Solution Implemented

The solution includes the following enhancements:

1. **PDF Image Extraction**:
   - Modified `textract-processor.py` to extract actual images from PDF pages using pdf2image
   - Each PDF page is converted to a PNG image and stored in S3
   - Both the PDF page reference and the extracted image reference are maintained

2. **Metadata Storage Enhancement**:
   - Updated `metadata-extractor.py` to properly store both PDF page references and extracted image references
   - Created additional search indices to link PDF pages to their extracted images

3. **Knowledge Base Query Enhancement**:
   - Enhanced `bedrock-knowledge-base.py` to prioritize extracted images over PDF page references
   - Improved the structured response format to include proper image URLs

## Deployment Instructions

### 1. Deploy the CDK Stack

The solution now includes an automated process for creating and deploying the Lambda layer. Simply deploy the CDK stack:

```bash
npm run build
cdk deploy
```

This will:
1. Build the TypeScript code
2. Deploy the stack with all necessary components
3. Automatically create the PDF image layer during deployment
4. Attach the layer to the Lambda functions

If you're deploying for the first time, you may need to bootstrap your AWS environment:

```bash
cdk bootstrap
```

### 2. How the Automated Layer Creation Works

During deployment, a custom resource will:
1. Create a Lambda function (`CreatePdfImageLayerFunction`) that:
   - Installs the required dependencies (pdf2image, poppler)
   - Creates a zip file with the dependencies
   - Uploads the zip file to S3
   - Creates a Lambda layer from the zip file
   - Attaches the layer to the specified Lambda functions

2. Trigger the Lambda function to create and attach the layer
3. Update the Lambda functions to use the new layer

This automated approach eliminates the need for manual steps to create and deploy the Lambda layer.

### 3. First Deployment Considerations

During the first deployment, you may see log messages like:

```
Extracting images from PDF: Smart FAQs.pdf
Python path: ['/var/task', '/opt/python/lib/python3.9/site-packages', '/opt/python', ...]
Current directory: /var/task
Directory contents: ['bedrock-knowledge-base.py', 'create-pdf-image-layer.py', ...]
Contents of /opt directory: []
pdf2image is not available - cannot extract images from PDF
This is expected during the first deployment. The Lambda layer will be created and attached.
```

This is expected because:

1. The Lambda functions are deployed before the Lambda layer is created
2. The custom resource that creates the layer runs after the Lambda functions are deployed
3. The Lambda functions will be updated with the layer after it's created

If you upload a document immediately after the first deployment, it might not be processed correctly. Wait a few minutes for the layer creation and attachment to complete, then try again.

The enhanced Lambda layer creation process now:
1. Installs packages to multiple locations to ensure compatibility
2. Uses multiple sources for poppler binaries
3. Implements a custom waiting mechanism to ensure functions are updated
4. Forces a cold start of the functions to ensure the layer is loaded

### 4. Manual Layer Verification and Attachment

If you're still seeing "pdf2image is not available" errors after deployment, you can manually verify and attach the layer:

1. Check if the layer was created:
   ```bash
   aws lambda list-layers --query "Layers[?LayerName=='pdf-image-layer']"
   ```

2. Get the latest layer version ARN:
   ```bash
   aws lambda list-layer-versions --layer-name pdf-image-layer --query "LayerVersions[0].LayerVersionArn" --output text
   ```

3. Manually attach the layer to the Lambda functions:
   ```bash
   aws lambda update-function-configuration --function-name YourTextractProcessorFunction --layers arn:aws:lambda:region:account:layer:pdf-image-layer:1
   aws lambda update-function-configuration --function-name YourMetadataExtractorFunction --layers arn:aws:lambda:region:account:layer:pdf-image-layer:1
   aws lambda update-function-configuration --function-name YourBedrockKnowledgeBaseFunction --layers arn:aws:lambda:region:account:layer:pdf-image-layer:1
   ```

4. Verify the layer is attached:
   ```bash
   aws lambda get-function --function-name YourTextractProcessorFunction --query "Configuration.Layers"
   ```

### 5. Reprocessing Documents

If you've already processed documents before the Lambda layer was properly attached, you'll need to reprocess them to extract the images. You can do this by:

1. Deleting the processed documents from the processed bucket
2. Deleting the metadata entries from the DynamoDB tables
3. Re-uploading the original documents to the input bucket

Alternatively, you can use the following AWS CLI command to trigger reprocessing of a specific document:

```bash
aws lambda invoke --function-name YourTextractProcessorFunction \
  --payload '{"bucket": "your-input-bucket", "key": "your-document.pdf", "enable_image_extraction": true}' \
  response.json
```

### 6. Debugging Layer Issues

If you're still having issues with the Lambda layer, you can check the CloudWatch logs for detailed debugging information:

1. The `CreatePdfImageLayerFunction` logs will show:
   - The Python packages installed to multiple locations
   - The poppler binaries downloaded from multiple sources
   - The layer creation response
   - The function update responses
   - The custom waiting logic status

2. The `TextractProcessorFunction` logs will show:
   - The Python path and directories
   - The contents of the /opt directory (where layers are mounted)
   - The search for poppler binaries
   - Any errors encountered during image extraction

You can also manually create and upload the layer with multiple package locations:

```bash
# Create a directory structure for the layer with multiple package locations
mkdir -p lambda-layer/python
mkdir -p lambda-layer/python/lib/python3.9/site-packages
mkdir -p lambda-layer/bin

# Install the required packages to multiple locations
pip install pdf2image==1.16.3 pillow>=9.0.0 -t lambda-layer/python
pip install pdf2image==1.16.3 pillow>=9.0.0 -t lambda-layer/python/lib/python3.9/site-packages

# Download and extract poppler binaries from multiple sources
# Try these sources:
# - https://github.com/bwits/pdf2image-lambda-layer/raw/master/bin/poppler-utils.zip
# - https://github.com/shelfio/lambda-poppler-layer/releases/download/v0.0.1/poppler-utils-0.0.1.zip
# - https://github.com/jeylabs/aws-lambda-poppler-layer/raw/master/poppler-utils.zip

# Create the layer zip
cd lambda-layer
zip -r ../lambda-layer.zip .

# Upload the layer
aws lambda publish-layer-version \
  --layer-name pdf-image-layer \
  --description "Lambda layer for pdf2image and poppler" \
  --zip-file fileb://../lambda-layer.zip \
  --compatible-runtimes python3.9

# Attach the layer to all functions and force a cold start
for function in YourTextractProcessorFunction YourMetadataExtractorFunction YourBedrockKnowledgeBaseFunction; do
  aws lambda update-function-configuration --function-name $function --layers arn:aws:lambda:region:account:layer:pdf-image-layer:1
  aws lambda invoke --function-name $function --payload '{"force_cold_start":true}' /dev/null
done
```

### 7. Verify the Deployment

After deployment, verify that:
1. The Lambda layer has been created (check the Lambda console under Layers)
2. The Lambda functions have the layer attached (check each function's configuration)
3. The Lambda functions have the necessary permissions

You can check the CloudWatch logs for the `CreatePdfImageLayerFunction` to see the status of the layer creation and attachment.

### 8. Testing with a Simple PDF

If you're having trouble with complex PDFs, try testing with a simple PDF first:

1. Create a simple PDF with text and images
2. Upload it to the input bucket
3. Check the CloudWatch logs for the `TextractProcessorFunction`
4. Verify that images are extracted and stored in the processed bucket

This can help isolate whether the issue is with the PDF processing or with the layer setup.

### 9. Verifying Image Extraction

To verify that images are being properly extracted and indexed:

1. Check the CloudWatch logs for the `TextractProcessorFunction` after processing a document:
   - Look for messages like "Successfully extracted image from page X to Y"
   - If you see "pdf2image is not available", the Lambda layer is not properly attached
   - Look for "Set POPPLER_PATH to /opt/bin" or similar

2. Check the CloudWatch logs for the `MetadataExtractorFunction`:
   - Look for messages like "Found extracted image: s3://..."
   - Look for messages like "Successfully added index X to DynamoDB"
   - Look for "Created X image search indices"

3. Check the CloudWatch logs for the `BedrockKnowledgeBaseFunction` when querying:
   - Look for "Found X image_content indices"
   - Look for "Found X embedded_image indices"
   - Look for "Total image indices found: X"
   - Look for "Returning X relevant images"
   - Look for "Top scoring image: s3://..."

4. Check the S3 bucket for extracted images:
   - Look in the processed bucket under the "extracted_images" prefix
   - Verify that the images are properly extracted and stored

### 10. Troubleshooting

If you encounter issues with image extraction:

1. **Layer not created**: Check the CloudWatch logs for the `CreatePdfImageLayerFunction` to see if there were any errors during layer creation.
   - Look for "Successfully installed packages to: /tmp/..."
   - Look for "Layer creation response: {...}"
   - Look for "Successfully attached layer to function-name"

2. **Layer not attached**: Verify that the Lambda functions have the layer attached in the Lambda console.
   - Check the "Layers" section of each Lambda function
   - Verify that the ARN matches the latest version of the pdf-image-layer
   - Check the function state and last update status

3. **Empty /opt directory**: If the logs show an empty /opt directory, it means the layer is not properly mounted.
   - Check if the layer was created successfully
   - Verify that the layer ARN is correct
   - Try forcing a cold start of the Lambda function
   - Try updating the function configuration again

4. **Poppler binaries not found**: The Lambda function looks for poppler binaries in several locations:
   - `/opt/bin/pdftoppm`
   - `/var/task/bin/pdftoppm`
   - `/opt/pdftoppm`
   - `/tmp/bin/pdftoppm`
   - `pdftoppm` (in PATH)
   
   The function will also try to copy binaries from /opt/bin to /tmp/bin if needed.

5. **pdf2image not found**: The function checks for pdf2image in several locations:
   - Default Python path
   - `/opt/python`
   - `/opt/python/lib/python3.9/site-packages`
   
   Check the logs for "Python path:" to see where Python is looking for packages.

6. **Images not found in queries**: Check the CloudWatch logs for the `BedrockKnowledgeBaseFunction`:
   - If "Total image indices found: 0", the images are not being properly indexed
   - Check the `MetadataExtractorFunction` logs to see if indices are being created
   - Try reprocessing the document after the Lambda layer is properly attached

7. **Permission issues**: Ensure that the Lambda functions have the necessary permissions to access the S3 buckets and other resources.

8. **Memory or timeout issues**: Check if the Lambda functions are running out of memory or timing out:
   - Increase the memory allocation for the Lambda functions
   - Increase the timeout for the Lambda functions

9. **Cold start issues**: Lambda functions with layers may need a cold start to properly load the layer:
   - Force a cold start by invoking the function with a test payload
   - Check the logs after the cold start to see if the layer is properly loaded

If you need to manually create the layer, you can still use the provided script:

```bash
chmod +x setup-lambda-layer.sh
./setup-lambda-layer.sh
```

Then upload the resulting `lambda-layer.zip` file to the Lambda console as a new layer.

### 11. Common Error Messages and Solutions

| Error Message | Cause | Solution |
|---------------|-------|----------|
| "pdf2image is not available" | Lambda layer not attached | Wait for layer creation to complete or manually attach the layer |
| "Poppler not available" | Poppler binaries not found | Check the Lambda layer contents or manually upload the binaries |
| "No image indices found in the database" | Images not properly indexed | Reprocess the document after the layer is attached |
| "Error adding index to DynamoDB" | Permission issues | Check the IAM role permissions for the Lambda function |
| "Error extracting image from PDF page" | PDF processing error | Check the PDF file format or try a different PDF |
| "pdftoppm not found at /opt/bin/pdftoppm" | Poppler binaries not in expected location | Check the layer contents and try alternative paths |
| "Error checking for pdf2image" | Python import error | Check the layer structure and Python path |
| "Found 0 image_content indices" | No images indexed | Check the metadata extraction process and reprocess documents |
| "Error updating function" | Permission issues | Check the IAM role permissions for the CreatePdfImageLayerFunction |
| "Memory limit exceeded" | Lambda function out of memory | Increase the memory allocation for the Lambda function |

### 4. Test the Solution

1. Upload a PDF document to the input S3 bucket
2. Verify that the document is processed and images are extracted
3. Query the knowledge base with a question related to the document
4. Verify that the response includes proper image references

## Technical Details

### Image Extraction Process

1. When a PDF is processed, each page is analyzed for text content
2. Pages with text are converted to PNG images using pdf2image
3. The extracted images are stored in S3 with a reference to the original PDF page

### Image Search and Retrieval

1. When a query is made to the knowledge base, the system searches for relevant text
2. If the text is associated with images, those images are included in the response
3. The system prioritizes extracted images over PDF page references
4. Presigned URLs are generated for the images to allow direct access

### Structured Response Format

The structured response now includes:
- Text blocks with the answer
- Image blocks with:
  - URL: Presigned URL to the extracted image
  - Description: Description of the image
  - Relevance score: How relevant the image is to the query
  - Source PDF: Reference to the original PDF page (if applicable)