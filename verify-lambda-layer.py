#!/usr/bin/env python3
"""
Script to verify the Lambda layer for PDF image extraction.
This script checks if the pdf2image package and poppler binaries are correctly set up.
"""

import os
import sys
import subprocess
import tempfile
from pathlib import Path

def check_pdf2image():
    """Check if pdf2image is installed and can be imported."""
    try:
        import pdf2image
        print(f"✅ pdf2image is installed (version: {pdf2image.__version__})")
        return True
    except ImportError:
        print("❌ pdf2image is not installed or cannot be imported")
        return False

def check_poppler():
    """Check if poppler binaries are available in the PATH."""
    try:
        # Try to run pdftoppm to check if it's available
        result = subprocess.run(['pdftoppm', '-v'], 
                               capture_output=True, 
                               text=True)
        print(f"✅ poppler is installed: {result.stderr.strip()}")
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        print("❌ poppler binaries are not available in the PATH")
        return False

def check_lambda_layer_zip():
    """Check if lambda-layer.zip exists and contains the required files."""
    if not os.path.exists('lambda-layer.zip'):
        print("❌ lambda-layer.zip does not exist")
        return False
    
    # Check the contents of the zip file
    try:
        result = subprocess.run(['unzip', '-l', 'lambda-layer.zip'], 
                               capture_output=True, 
                               text=True)
        output = result.stdout
        
        # Check for pdf2image
        if 'python/pdf2image' in output:
            print("✅ lambda-layer.zip contains pdf2image")
        else:
            print("❌ lambda-layer.zip does not contain pdf2image")
            return False
        
        # Check for poppler binaries
        if 'bin/pdftoppm' in output:
            print("✅ lambda-layer.zip contains pdftoppm")
        else:
            print("❌ lambda-layer.zip does not contain pdftoppm")
            return False
        
        if 'bin/pdfinfo' in output:
            print("✅ lambda-layer.zip contains pdfinfo")
        else:
            print("❌ lambda-layer.zip does not contain pdfinfo")
            return False
        
        return True
    except subprocess.SubprocessError:
        print("❌ Failed to check lambda-layer.zip contents")
        return False

def test_pdf_conversion():
    """Test PDF conversion with pdf2image if a test PDF is available."""
    # Look for a PDF file to test with
    pdf_files = list(Path('.').glob('*.pdf'))
    if not pdf_files:
        print("⚠️ No PDF files found for testing conversion")
        return None
    
    test_pdf = pdf_files[0]
    print(f"Testing PDF conversion with {test_pdf}")
    
    try:
        from pdf2image import convert_from_path
        
        # Convert the first page of the PDF to an image
        with tempfile.TemporaryDirectory() as output_dir:
            images = convert_from_path(
                test_pdf,
                output_folder=output_dir,
                first_page=1,
                last_page=1,
                dpi=150
            )
            
            if images:
                print(f"✅ Successfully converted PDF to image: {len(images)} image(s) generated")
                return True
            else:
                print("❌ PDF conversion failed: no images generated")
                return False
    except Exception as e:
        print(f"❌ PDF conversion failed with error: {str(e)}")
        return False

def main():
    """Main function to run all checks."""
    print("Verifying Lambda layer for PDF image extraction...")
    print("\nChecking pdf2image installation:")
    pdf2image_ok = check_pdf2image()
    
    print("\nChecking poppler installation:")
    poppler_ok = check_poppler()
    
    print("\nChecking lambda-layer.zip:")
    layer_zip_ok = check_lambda_layer_zip()
    
    if pdf2image_ok and poppler_ok:
        print("\nTesting PDF conversion:")
        conversion_ok = test_pdf_conversion()
    else:
        conversion_ok = False
        print("\nSkipping PDF conversion test due to missing dependencies")
    
    # Print summary
    print("\n=== Summary ===")
    print(f"pdf2image installed: {'✅' if pdf2image_ok else '❌'}")
    print(f"poppler binaries available: {'✅' if poppler_ok else '❌'}")
    print(f"lambda-layer.zip valid: {'✅' if layer_zip_ok else '❌'}")
    if conversion_ok is not None:
        print(f"PDF conversion test: {'✅' if conversion_ok else '❌'}")
    else:
        print("PDF conversion test: ⚠️ skipped (no test PDF available)")
    
    # Overall status
    if pdf2image_ok and poppler_ok and layer_zip_ok and (conversion_ok is True or conversion_ok is None):
        print("\n✅ Lambda layer setup is SUCCESSFUL")
        return 0
    else:
        print("\n❌ Lambda layer setup has ISSUES that need to be fixed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
