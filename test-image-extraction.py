#!/usr/bin/env python3
"""
Test script for the image extraction functionality.
This script uploads a PDF to the input bucket, waits for processing to complete,
and then queries the knowledge base with a question related to the document.
"""

import boto3
import json
import time
import argparse
import os

def main():
    parser = argparse.ArgumentParser(description='Test the image extraction functionality')
    parser.add_argument('--pdf', required=True, help='Path to the PDF file to upload')
    parser.add_argument('--bucket', required=True, help='Name of the input S3 bucket')
    parser.add_argument('--function', required=True, help='Name of the Bedrock knowledge base Lambda function')
    parser.add_argument('--query', default='What information is in this document?', 
                        help='Query to send to the knowledge base')
    args = parser.parse_args()
    
    # Initialize AWS clients
    s3_client = boto3.client('s3')
    lambda_client = boto3.client('lambda')
    
    # Upload the PDF to the input bucket
    pdf_key = os.path.basename(args.pdf)
    print(f"Uploading {args.pdf} to s3://{args.bucket}/{pdf_key}...")
    s3_client.upload_file(args.pdf, args.bucket, pdf_key)
    
    # Wait for processing to complete (approximately 2 minutes)
    print("Waiting for document processing to complete...")
    time.sleep(120)
    
    # Query the knowledge base
    print(f"Querying the knowledge base with: '{args.query}'")
    response = lambda_client.invoke(
        FunctionName=args.function,
        InvocationType='RequestResponse',
        Payload=json.dumps({
            'operation': 'query_knowledge_base',
            'query': args.query
        })
    )
    
    # Parse the response
    response_payload = json.loads(response['Payload'].read().decode('utf-8'))
    
    # Check if the response contains a payload reference (for large responses)
    if 'payload_reference' in response_payload:
        bucket = response_payload['payload_reference']['bucket']
        key = response_payload['payload_reference']['key']
        print(f"Response is too large, retrieving from S3: s3://{bucket}/{key}")
        
        # Get the payload from S3
        s3_response = s3_client.get_object(Bucket=bucket, Key=key)
        response_payload = json.loads(s3_response['Body'].read().decode('utf-8'))
    
    # Extract the response body
    if 'body' in response_payload:
        try:
            body = json.loads(response_payload['body'])
            
            # Print the answer
            print("\nAnswer:")
            print(body.get('answer', 'No answer found'))
            
            # Print information about images
            images = body.get('images', [])
            print(f"\nFound {len(images)} images in the response")
            
            for i, img in enumerate(images):
                print(f"\nImage {i+1}:")
                print(f"Description: {img.get('description', 'No description')}")
                print(f"Relevance score: {img.get('relevance_score', 0)}")
                if 'url' in img:
                    print(f"URL: {img['url'][:100]}...")  # Print first 100 chars of URL
                if 'direct_url' in img:
                    print(f"Direct URL: {img['direct_url'][:100]}...")  # Print first 100 chars of URL
            
            # Print the structured response
            structured = body.get('structured_response', [])
            print(f"\nStructured response has {len(structured)} blocks")
            
            for i, block in enumerate(structured):
                block_type = block.get('type', 'unknown')
                print(f"Block {i+1}: {block_type}")
                if block_type == 'image':
                    print(f"  Image URL: {block.get('url', 'No URL')[:50]}...")
        except json.JSONDecodeError:
            print("Error parsing response body as JSON")
            print(f"Raw body: {response_payload['body']}")
    else:
        print("No body found in response")
        print(f"Raw response: {json.dumps(response_payload, indent=2)}")

if __name__ == '__main__':
    main()