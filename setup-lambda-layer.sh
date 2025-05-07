#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

echo "Setting up Lambda layer for PDF image extraction..."

# Clean up any existing layer directory
if [ -d "lambda-layer" ]; then
    echo "Removing existing lambda-layer directory..."
    rm -rf lambda-layer
fi

# Create a directory for the Lambda layer with the correct structure
echo "Creating Lambda layer directory structure..."
mkdir -p lambda-layer/python
mkdir -p lambda-layer/bin

# Install the required packages into the layer directory
echo "Installing Python packages for Lambda (Linux x86_64, Python 3.9)..."

# Define target directory and ensure it's clean
LAYER_PYTHON_DIR="lambda-layer/python"
rm -rf "$LAYER_PYTHON_DIR"
mkdir -p "$LAYER_PYTHON_DIR"

# Define packages
# Using a version range for PyMuPDF to pick up a compatible 1.23.x version
# PyMuPDF wheels are typically tagged like: PyMuPDF-1.23.8-cp39-cp39-manylinux2014_x86_64.whl
PYMUPDF_SPECIFIER="PyMuPDF~=1.23.0"

# Target Python version and platform for Lambda
TARGET_PYTHON_VERSION="3.9"
TARGET_PLATFORM="manylinux2014_x86_64" # A widely compatible manylinux tag
TARGET_ABI="cp39" # For CPython 3.9, can also be cp39m

echo "Attempting to download Lambda-compatible PyMuPDF wheel for Python $TARGET_PYTHON_VERSION on $TARGET_PLATFORM..."
DOWNLOAD_DIR=$(mktemp -d)

# Try with 'cp39m' ABI first, then 'cp39'
PIP_DOWNLOAD_CMD_BASE="pip download --only-binary=:all: --platform $TARGET_PLATFORM --python-version $TARGET_PYTHON_VERSION --implementation cp --dest $DOWNLOAD_DIR $PYMUPDF_SPECIFIER"

echo "Attempt 1: Trying ABI ${TARGET_ABI}m"
if $PIP_DOWNLOAD_CMD_BASE --abi "${TARGET_ABI}m" && ls "$DOWNLOAD_DIR"/PyMuPDF*.whl 1> /dev/null 2>&1; then
    echo "Successfully downloaded PyMuPDF wheel with ABI ${TARGET_ABI}m."
elif (echo "Attempt 2: Trying ABI ${TARGET_ABI}" && rm -f "$DOWNLOAD_DIR"/PyMuPDF*.whl && $PIP_DOWNLOAD_CMD_BASE --abi "$TARGET_ABI" && ls "$DOWNLOAD_DIR"/PyMuPDF*.whl 1> /dev/null 2>&1); then
    echo "Successfully downloaded PyMuPDF wheel with ABI ${TARGET_ABI}."
else
    echo "ERROR: Failed to download suitable manylinux wheel for PyMuPDF for Python $TARGET_PYTHON_VERSION, platform $TARGET_PLATFORM."
    echo "Contents of download directory $DOWNLOAD_DIR:"
    ls -la "$DOWNLOAD_DIR"
    echo "Consider using Docker to build the layer for guaranteed compatibility, or check PyPI for available wheels for $PYMUPDF_SPECIFIER."
    rm -rf "$DOWNLOAD_DIR"
    exit 1
fi

echo "Unpacking downloaded PyMuPDF wheel(s) into $LAYER_PYTHON_DIR..."
# Manually unpack .whl files to bypass pip's host platform check
# This is less robust than pip install but can work for cross-platform targeting without Docker.
if ls "$DOWNLOAD_DIR"/*.whl 1> /dev/null 2>&1; then
    for wheel_file in "$DOWNLOAD_DIR"/*.whl; do
        echo "Unzipping $wheel_file into $LAYER_PYTHON_DIR..."
        unzip -q -o "$wheel_file" -d "$LAYER_PYTHON_DIR"
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to unzip $wheel_file into $LAYER_PYTHON_DIR."
            exit 1
        fi
    done
    echo "Successfully unpacked all .whl files."
else
    echo "ERROR: No .whl files found in $DOWNLOAD_DIR to unpack."
    exit 1
fi


# Clean up download directory
rm -rf "$DOWNLOAD_DIR"

# Remove Poppler setup as PyMuPDF does not need external Poppler binaries
echo "PyMuPDF does not require separate Poppler binaries. Skipping Poppler setup."
if [ -d "lambda-layer/bin" ]; then
    rm -rf lambda-layer/bin
    echo "Removed lambda-layer/bin directory."
fi

# Check if the PyMuPDF installation was successful (basic check for 'fitz' directory)
if [ ! -d "lambda-layer/python/fitz" ]; then
    echo "ERROR: PyMuPDF (fitz) package was not installed correctly into $LAYER_PYTHON_DIR!"
    exit 1
fi

echo "Successfully installed Python packages."
echo "Installed packages in $LAYER_PYTHON_DIR:"
ls -la lambda-layer/python
# The Poppler binary setup is no longer needed as PyMuPDF includes its own rendering engine.

# Create a zip file for the Lambda layer
echo "Creating Lambda layer zip file..."
cd lambda-layer
zip -r ../lambda-layer.zip .
cd ..

# Verify the zip file was created
if [ ! -f "lambda-layer.zip" ]; then
    echo "ERROR: Failed to create lambda-layer.zip!"
    exit 1
fi

echo "Lambda layer created successfully: lambda-layer.zip"
echo "Size of lambda-layer.zip: $(du -h lambda-layer.zip | cut -f1)"
echo ""
echo "Next steps:"
echo "1. Deploy your CDK stack to create and attach the Lambda layer"
echo "2. Test the PDF image extraction functionality"