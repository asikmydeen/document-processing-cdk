import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as path from 'path';
import { Construct } from 'constructs';

/**
 * This function creates a Lambda layer for pdf2image and poppler
 * @param scope The CDK construct scope
 * @param id The ID for the layer
 * @returns The Lambda layer
 */
export function createPdfImageLayer(scope: Construct, id: string): lambda.LayerVersion {
  // Create a Lambda layer for pdf2image and poppler
  const pdfImageLayer = new lambda.LayerVersion(scope, id, {
    code: lambda.Code.fromAsset(path.join(__dirname, '../lambda-layer.zip')),
    compatibleRuntimes: [lambda.Runtime.PYTHON_3_9],
    description: 'Lambda layer for pdf2image and poppler binaries',
    license: 'MIT',
  });

  return pdfImageLayer;
}