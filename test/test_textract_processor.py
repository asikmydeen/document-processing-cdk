import os
import importlib.util

import pytest

# Dynamically load the textract-processor module
MODULE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../lambda/textract-processor.py')
)
spec = importlib.util.spec_from_file_location("textract_processor", MODULE_PATH)
textract_processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(textract_processor)

# References to the functions under test
def extract_tables(blocks):
    return textract_processor.extract_tables_from_blocks(blocks)

def extract_forms(blocks):
    return textract_processor.extract_forms_from_blocks(blocks)

# Tests for extract_tables_from_blocks

def test_extract_tables_empty_blocks_returns_empty_list():
    assert extract_tables([]) == []


def test_extract_tables_no_relationships_entries_returns_empty_list():
    blocks = [
        {'BlockType': 'TABLE', 'Id': 'table1'}
    ]
    # No CELL blocks and no relationships -> should return empty list
    assert extract_tables(blocks) == []


def test_extract_tables_malformed_blocks_missing_blocktype_returns_empty_list():
    blocks = [
        {'Id': 'unknown'}  # Missing BlockType and Id without BlockType
    ]
    # Malformed block entry should be handled gracefully
    assert extract_tables(blocks) == []

# Tests for extract_forms_from_blocks

def test_extract_forms_empty_blocks_returns_empty_list():
    assert extract_forms([]) == []


def test_extract_forms_empty_entitytypes_returns_empty_list():
    blocks = [
        {'BlockType': 'KEY_VALUE_SET', 'EntityTypes': [], 'Id': 'kv1'}
    ]
    # EntityTypes empty -> no KEY blocks processed
    assert extract_forms(blocks) == []


def test_extract_forms_missing_fields_returns_empty_list():
    blocks = [
        {'SomeField': 'value'}  # Missing BlockType, EntityTypes, Relationships
    ]
    # Malformed block should not raise
    assert extract_forms(blocks) == []


def test_extract_forms_no_relationships_entries_returns_empty_list():
    blocks = [
        {'BlockType': 'KEY_VALUE_SET', 'EntityTypes': ['KEY'], 'Id': 'kv1'}
    ]
    # KEY block without relationships -> should return empty list
    assert extract_forms(blocks) == []
