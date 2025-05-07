import json
import boto3
import os
import subprocess
import tempfile
import shutil
import zipfile
import urllib.request
import sys
import time
from pathlib import Path

# Initialize AWS clients
s3_client = boto3.client('s3')
lambda_client = boto3.client('lambda')

def lambda_handler(event, context):
    """
    Lambda function to create a PDF image layer with pdf2image and poppler,
    upload it to S3, and attach it to the specified Lambda functions.
    """
    try:
        print("Starting PDF image layer creation process")

        # Get parameters from the event
        bucket_name = event.get('bucket_name')
        layer_name = event.get('layer_name', 'pdf-image-layer')
        lambda_functions = event.get('lambda_functions', [])

        if not bucket_name:
            raise ValueError("bucket_name is required in the event")

        print(f"Creating layer '{layer_name}' for functions: {lambda_functions}")

        # Print environment information for debugging
        print("Python version:", sys.version)
        print("Current directory:", os.getcwd())
        print("Environment variables:", dict(os.environ))

        # Create a temporary directory for the layer
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = os.path.join(temp_dir, 'bin')

            # Create directories - Lambda layer structure
            # For Python packages, Lambda looks in:
            # 1. /opt/python - direct python directory
            # 2. /opt/python/lib/python3.9/site-packages - site-packages directory

            # Create the python directory (will be mounted at /opt/python)
            python_dir = os.path.join(temp_dir, 'python')
            os.makedirs(python_dir, exist_ok=True)

            # Create the bin directory (will be mounted at /opt/bin)
            bin_dir = os.path.join(temp_dir, 'bin')
            os.makedirs(bin_dir, exist_ok=True)

            # We don't need the layer_dir structure anymore as we're using the direct python directory
            # This simplifies the layer structure and makes it more reliable

            # Install Python packages to the python directory
            packages = ['pdf2image==1.16.3', 'pillow>=9.0.0']

            # Install to python directory (will be mounted at /opt/python)
            try:
                subprocess.check_call([
                    'pip', 'install',
                    '-t', python_dir,
                ] + packages)
                print("Successfully installed packages to:", python_dir)
                print("Contents of python directory:", os.listdir(python_dir))
            except Exception as e:
                print(f"Error installing packages to python directory: {str(e)}")

            # Download and install poppler binaries for Lambda (Amazon Linux 2)
            print("Downloading poppler binaries")

            # Try multiple sources for poppler binaries
            poppler_urls = [
                "https://github.com/bwits/pdf2image-lambda-layer/raw/master/bin/poppler-utils.zip",
                "https://github.com/shelfio/lambda-poppler-layer/releases/download/v0.0.1/poppler-utils-0.0.1.zip",
                "https://github.com/jeylabs/aws-lambda-poppler-layer/raw/master/poppler-utils.zip"
            ]

            poppler_zip = os.path.join(temp_dir, 'poppler-utils.zip')
            download_success = False

            for url in poppler_urls:
                try:
                    print(f"Trying to download poppler from: {url}")
                    urllib.request.urlretrieve(url, poppler_zip)
                    download_success = True
                    print(f"Successfully downloaded poppler from: {url}")
                    break
                except Exception as e:
                    print(f"Failed to download from {url}: {str(e)}")

            if not download_success:
                raise Exception("Failed to download poppler binaries from any source")

            # Extract the poppler binaries to the bin directory
            print("Extracting poppler binaries")
            with zipfile.ZipFile(poppler_zip, 'r') as zip_ref:
                zip_ref.extractall(bin_dir)

            print(f"Extracted poppler binaries to {bin_dir}")
            print(f"Contents of bin directory: {os.listdir(bin_dir)}")

            # Make the binaries executable
            for binary in os.listdir(bin_dir):
                binary_path = os.path.join(bin_dir, binary)
                if os.path.isfile(binary_path):
                    os.chmod(binary_path, 0o755)
                    print(f"Made {binary_path} executable")

            # Create a zip file of the layer
            print("Creating layer zip file")
            layer_zip = os.path.join(temp_dir, 'layer.zip')
            with zipfile.ZipFile(layer_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add the python directory
                for root, _, files in os.walk(python_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zipf.write(file_path, arcname)

                # Add the bin directory
                for root, _, files in os.walk(bin_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zipf.write(file_path, arcname)

            # Verify the layer contents
            print("Verifying layer contents:")
            print("Python packages:")
            pdf2image_found = False
            for root, _, files in os.walk(python_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    print(f"  {file_path}")
                    if 'pdf2image' in file_path:
                        pdf2image_found = True

            if not pdf2image_found:
                print("WARNING: pdf2image package not found in python directory!")

            print("Bin directory:")
            poppler_found = False
            for root, _, files in os.walk(bin_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    print(f"  {file_path}")
                    if 'pdftoppm' in file_path:
                        poppler_found = True

            if not poppler_found:
                print("WARNING: pdftoppm binary not found in bin directory!")

            # Upload the layer zip to S3
            print(f"Uploading layer zip to S3 bucket: {bucket_name}")
            layer_key = f"layers/{layer_name}.zip"
            s3_client.upload_file(layer_zip, bucket_name, layer_key)
            print(f"Successfully uploaded layer zip to s3://{bucket_name}/{layer_key}")

            # Create the Lambda layer
            print(f"Creating Lambda layer: {layer_name}")
            try:
                response = lambda_client.publish_layer_version(
                    LayerName=layer_name,
                    Description='Lambda layer for pdf2image and poppler',
                    Content={
                        'S3Bucket': bucket_name,
                        'S3Key': layer_key
                    },
                    CompatibleRuntimes=['python3.9'],
                    LicenseInfo='MIT'
                )
                print(f"Layer creation response: {json.dumps(response, default=str)}")
            except Exception as e:
                print(f"Error creating Lambda layer: {str(e)}")
                raise

            layer_version_arn = response['LayerVersionArn']
            print(f"Created layer version: {layer_version_arn}")

            # Attach the layer to the specified Lambda functions
            if lambda_functions:
                print(f"Attaching layer to {len(lambda_functions)} Lambda functions")
                for function_name in lambda_functions:
                    print(f"Updating function: {function_name}")
                    try:
                        # Get the current configuration
                        function_config = lambda_client.get_function_configuration(
                            FunctionName=function_name
                        )
                        print(f"Current function configuration: {json.dumps(function_config, default=str)}")

                        # Get existing layers
                        existing_layers = function_config.get('Layers', [])
                        existing_layer_arns = [layer['Arn'] for layer in existing_layers]
                        print(f"Existing layers: {existing_layer_arns}")

                        # Remove any existing pdf-image-layer versions
                        filtered_layer_arns = [arn for arn in existing_layer_arns if layer_name not in arn]
                        print(f"Filtered layers: {filtered_layer_arns}")

                        # Add the new layer
                        updated_layer_arns = filtered_layer_arns + [layer_version_arn]
                        print(f"Updated layers: {updated_layer_arns}")

                        # Update the function configuration
                        update_response = lambda_client.update_function_configuration(
                            FunctionName=function_name,
                            Layers=updated_layer_arns
                        )
                        print(f"Update response: {json.dumps(update_response, default=str)}")
                        print(f"Successfully attached layer to {function_name}")
                    except Exception as e:
                        print(f"Error updating function {function_name}: {str(e)}")
                        # Try again with a different approach
                        try:
                            print(f"Trying alternative approach for {function_name}")
                            lambda_client.update_function_configuration(
                                FunctionName=function_name,
                                Layers=[layer_version_arn]
                            )
                            print(f"Successfully attached layer to {function_name} using alternative approach")
                        except Exception as e2:
                            print(f"Error with alternative approach for {function_name}: {str(e2)}")

            # Wait for the Lambda functions to be updated
            print("Waiting for Lambda functions to be updated...")
            for function_name in lambda_functions:
                # Lambda doesn't have a built-in waiter for function updates
                # So we'll implement our own waiting logic
                max_attempts = 60
                delay_seconds = 5
                for attempt in range(max_attempts):
                    try:
                        # Get the current function configuration
                        function_config = lambda_client.get_function_configuration(
                            FunctionName=function_name
                        )

                        # Check if the function is in a terminal state
                        state = function_config.get('State', '')
                        last_update_status = function_config.get('LastUpdateStatus', '')

                        print(f"Function {function_name} state: {state}, last update status: {last_update_status}")

                        # If the function is active and the update is successful, we're done
                        if state == 'Active' and (last_update_status == 'Successful' or last_update_status == ''):
                            # Verify the layer is attached
                            layers = function_config.get('Layers', [])
                            layer_arns = [layer['Arn'] for layer in layers]

                            if any(layer_name in arn for arn in layer_arns):
                                print(f"Function {function_name} successfully updated with layer")
                                break
                            else:
                                print(f"Function {function_name} is active but layer is not attached. Retrying...")

                        # If the update failed, try again
                        elif last_update_status == 'Failed':
                            print(f"Function {function_name} update failed. Retrying...")
                            # Try to update the function again
                            lambda_client.update_function_configuration(
                                FunctionName=function_name,
                                Layers=[layer_version_arn]
                            )

                        # Otherwise, wait and try again
                        else:
                            print(f"Function {function_name} update in progress. Waiting...")

                        # Wait before checking again
                        time.sleep(delay_seconds)
                    except Exception as e:
                        print(f"Error checking function {function_name} status: {str(e)}")
                        time.sleep(delay_seconds)

                # After waiting, invoke the function to force a cold start with the new layer
                try:
                    print(f"Invoking function {function_name} to force cold start with new layer")
                    lambda_client.invoke(
                        FunctionName=function_name,
                        InvocationType='RequestResponse',
                        Payload=json.dumps({
                            'force_cold_start': True,
                            'test_layer': True
                        })
                    )
                    print(f"Successfully invoked function {function_name}")
                except Exception as e:
                    print(f"Error invoking function {function_name}: {str(e)}")

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'PDF image layer created and attached successfully',
                    'layer_name': layer_name,
                    'layer_version_arn': layer_version_arn,
                    'updated_functions': lambda_functions
                })
            }

    except Exception as e:
        print(f"Error creating PDF image layer: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f'Error creating PDF image layer: {str(e)}'
            })
        }