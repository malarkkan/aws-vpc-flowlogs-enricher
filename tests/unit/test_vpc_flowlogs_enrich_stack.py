import pytest
import json
import os
from unittest.mock import patch, MagicMock
from lambda_function.lambda_function import lambda_handler

@pytest.fixture
def s3_event():
    return {
        "Records": [{
            "s3": {
                "bucket": {
                    "name": "test-bucket"
                },
                "object": {
                    "key": "AWSLogs/flow-logs/test.log"
                }
            }
        }]
    }

@pytest.fixture
def flow_log_record():
    return "2 123456789010 eni-1234567890 172.31.16.139 172.31.16.21 20641 22 6 20 4249 1418530010 1418530070 ACCEPT OK"

@pytest.fixture
def mock_redis():
    with patch('redis.Redis') as mock:
        mock_instance = MagicMock()
        mock.return_value = mock_instance
        yield mock_instance

@pytest.fixture
def mock_s3():
    with patch('boto3.client') as mock:
        mock_s3_client = MagicMock()
        mock.return_value = mock_s3_client
        yield mock_s3_client

def test_lambda_handler_processes_valid_flow_log(s3_event, flow_log_record, mock_redis, mock_s3):
    # Setup
    mock_s3.get_object.return_value = {
        'Body': MagicMock(read=lambda: flow_log_record.encode())
    }
    mock_redis.get.return_value = None
    
    # Execute
    response = lambda_handler(s3_event, None)
    
    # Verify
    assert response['statusCode'] == 200
    mock_s3.get_object.assert_called_once()
    mock_s3.put_object.assert_called_once()

def test_lambda_handler_handles_cached_metadata(s3_event, flow_log_record, mock_redis, mock_s3):
    # Setup
    mock_s3.get_object.return_value = {
        'Body': MagicMock(read=lambda: flow_log_record.encode())
    }
    cached_metadata = json.dumps({
        'InstanceId': 'i-1234567890',
        'InstanceType': 't3.micro'
    })
    mock_redis.get.return_value = cached_metadata
    
    # Execute
    response = lambda_handler(s3_event, None)
    
    # Verify
    assert response['statusCode'] == 200
    mock_redis.get.assert_called_once()
    mock_redis.set.assert_not_called()

@pytest.mark.parametrize("invalid_record", [
    "",
    "invalid format",
    "1 2 3"  # Too few fields
])
def test_lambda_handler_handles_invalid_flow_log_record(s3_event, invalid_record, mock_redis, mock_s3):
    # Setup
    mock_s3.get_object.return_value = {
        'Body': MagicMock(read=lambda: invalid_record.encode())
    }
    
    # Execute
    response = lambda_handler(s3_event, None)
    
    # Verify
    assert response['statusCode'] == 400

def test_lambda_handler_handles_s3_error(s3_event, mock_redis, mock_s3):
    # Setup
    mock_s3.get_object.side_effect = Exception("S3 Error")
    
    # Execute
    response = lambda_handler(s3_event, None)
    
    # Verify
    assert response['statusCode'] == 500

def test_lambda_handler_handles_redis_error(s3_event, flow_log_record, mock_redis, mock_s3):
    # Setup
    mock_s3.get_object.return_value = {
        'Body': MagicMock(read=lambda: flow_log_record.encode())
    }
    mock_redis.get.side_effect = Exception("Redis Error")
    
    # Execute
    response = lambda_handler(s3_event, None)
    
    # Verify
    assert response['statusCode'] == 500
