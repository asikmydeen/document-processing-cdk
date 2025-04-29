import json
import boto3
import os
import urllib.parse

def lambda_handler(event, context):
    # Get the S3 bucket and key from the event
    record = event['Records'][0]
    bucket = record['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(record['s3']['object']['key'])

    # Start the Step Functions state machine
    client = boto3.client('stepfunctions')
    response = client.start_execution(
        stateMachineArn=os.environ['STATE_MACHINE_ARN'],
        input=json.dumps({
            'bucket': bucket,
            'key': key
        })
    )

    return {
        'statusCode': 200,
        'body': json.dumps('Started document processing state machine')
    }