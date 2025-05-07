# PDF Image Extraction Layer Fix

This document provides instructions for fixing the PDF image extraction layer in the document processing solution.

## Problem

The PDF image extraction functionality is not working correctly because the Lambda layer containing the `pdf2image` package and poppler binaries is not properly structured or not being correctly accessed by the Lambda functions.

## Solution

We've made several improvements to fix this issue:

1. Updated the Lambda layer creation script to ensure proper structure
2. Improved the path handling in the Lambda functions to correctly locate the layer components
3. Added better error handling and debugging information
4. Created verification tools to test the layer setup

## Steps to Fix

Follow these steps to fix the PDF image extraction layer:

### 1. Recreate the Lambda Layer

Run the updated setup script to create a properly structured Lambda layer:

```bash
# Make the script executable if needed
chmod +x setup-lambda-layer.sh

# Run the script
./setup-lambda-layer.sh
```

This will:
- Install the pdf2image package and its dependencies
- Install and configure the poppler binaries
- Create a lambda-layer.zip file with the correct structure

### 2. Verify the Lambda Layer

Run the verification script to check if the Lambda layer is correctly set up:

```bash
# Make the script executable if needed
chmod +x verify-lambda-layer.py

# Run the script
./verify-lambda-layer.py
```

This will check:
- If pdf2image is installed and can be imported
- If poppler binaries are available in the PATH
- If lambda-layer.zip contains all required components
- If PDF conversion works correctly (if a test PDF is available)

### 3. Deploy the Updated Solution

Deploy the updated solution using CDK:

```bash
# Deploy the stack
cdk deploy
```

The deployment will:
- Create a new Lambda layer with the updated code
- Attach the layer to the relevant Lambda functions
- Update the Lambda functions with improved path handling

### 4. Test the Solution

After deployment, test the PDF image extraction functionality:

```bash
# Run the test script with your PDF file
python test-image-extraction.py --pdf your-test-file.pdf --bucket your-input-bucket --function your-bedrock-kb-function
```

## Troubleshooting

If you encounter issues:

1. Check the Lambda function logs in CloudWatch for error messages
2. Verify that the Lambda layer is correctly attached to the functions
3. Make sure the poppler binaries are executable in the Lambda environment
4. Check if the pdf2image package is correctly installed in the layer

## Technical Details

### Lambda Layer Structure

The Lambda layer has the following structure:

```
/
├── bin/               # Mounted at /opt/bin in Lambda
│   ├── pdftoppm      # Poppler binary for PDF to image conversion
│   └── pdfinfo       # Poppler binary for PDF information extraction
└── python/           # Mounted at /opt/python in Lambda
    ├── pdf2image/    # The pdf2image package
    └── ...           # Other Python packages
```

### Path Configuration

The Lambda functions are configured to look for:
- Python packages in `/opt/python` and `/opt/python/lib/python3.9/site-packages`
- Poppler binaries in `/opt/bin`, `/var/task/bin`, and other potential locations

### Error Handling

The code now includes better error handling and debugging information:
- Detailed logging of import attempts and path configurations
- Fallback mechanisms for finding the required components
- Clear error messages when components are not found
