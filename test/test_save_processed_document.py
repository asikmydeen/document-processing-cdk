import os
import sys
import json
import pytest
import importlib.util

# Dynamically load the textract-processor module
LAMBDA_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../lambda/textract-processor.py')
)
spec = importlib.util.spec_from_file_location('textract_processor', LAMBDA_PATH)
textract_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(textract_module)

@py.test.fixture(autouse=True)
def clear_env(monkeypatch):
    # Ensure a clean environment for each test
    monkeypatch.delenv('PROCESSED_BUCKET_NAME', raising=False)
    yield

class FakeDynamoClient:
    def get_item(self, TableName):
        # Return fake HTTPHeaders date for timestamp hack
        return {'ResponseMetadata': {'HTTPHeaders': {'date': 'fake-date'}}}

class FakeS3Client:
    def __init__(self):
        self.calls = []
    def put_object(self, Bucket, Key, Body, ContentType):
        # Record the call parameters
        self.calls.append({
            'Bucket': Bucket,
            'Key': Key,
            'Body': Body,
            'ContentType': ContentType
        })


def test_save_processed_document_success(monkeypatch):
    # Arrange
    bucket = 'original-bucket'
    key = 'path/to/document.pdf'
    document_content = {'text_content': 'hello'}
    metadata = {'meta': 'data'}

    # Set the processed bucket environment variable
    monkeypatch.setenv('PROCESSED_BUCKET_NAME', 'processed-bucket')

    # Patch the module's s3_client and boto3.client for dynamodb
    fake_s3 = FakeS3Client()
    monkeypatch.setattr(textract_module, 's3_client', fake_s3)
    # Replace boto3.client used inside save_processed_document
    def fake_boto3_client(service_name):
        if service_name == 'dynamodb':
            return FakeDynamoClient()
        raise ValueError(f"Unexpected service: {service_name}")
    monkeypatch.setattr(textract_module, 'boto3', type('boto3', (), {'client': fake_boto3_client}))

    # Act
    result_key = textract_module.save_processed_document(bucket, key, document_content, metadata)

    # Assert
    expected_key = os.path.splitext(key)[0] + '.json'
    assert result_key == expected_key
    # Ensure one S3 put_object call was made
    assert len(fake_s3.calls) == 1
    call = fake_s3.calls[0]
    assert call['Bucket'] == 'processed-bucket'
    assert call['Key'] == expected_key
    # Body should be valid JSON with correct fields
    body = json.loads(call['Body'])
    assert body['original_bucket'] == bucket
    assert body['original_key'] == key
    assert body['document_content'] == document_content
    assert body['metadata'] == metadata
    assert call['ContentType'] == 'application/json'


def test_save_processed_document_empty_key_raises(monkeypatch):
    # Arrange: key is empty, simulate missing processed_key
    bucket = 'original-bucket'
    key = ''
    document_content = {'text_content': 'hello'}
    metadata = {'meta': 'data'}

    monkeypatch.setenv('PROCESSED_BUCKET_NAME', 'processed-bucket')

    # Patch s3_client and boto3.client
    fake_s3 = FakeS3Client()
    monkeypatch.setattr(textract_module, 's3_client', fake_s3)
    def fake_boto3_client(service_name):
        if service_name == 'dynamodb':
            return FakeDynamoClient()
        raise ValueError(f"Unexpected service: {service_name}")
    monkeypatch.setattr(textract_module, 'boto3', type('boto3', (), {'client': fake_boto3_client}))

    # Act & Assert: expect ValidationError and no S3 call
    with pytest.raises(textract_module.ValidationError):
        textract_module.save_processed_document(bucket, key, document_content, metadata)
    assert len(fake_s3.calls) == 0
