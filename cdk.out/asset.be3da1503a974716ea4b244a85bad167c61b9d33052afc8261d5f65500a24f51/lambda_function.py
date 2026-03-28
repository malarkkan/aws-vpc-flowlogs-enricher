# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import boto3
import os
import time
from datetime import datetime, timedelta
from collections import OrderedDict
from botocore.exceptions import ClientError
from typing import Dict, List, Optional, Any
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
ec2_client = boto3.client('ec2')
ec2 = boto3.resource('ec2')
dynamodb = boto3.resource('dynamodb')
cloudwatch = boto3.client('cloudwatch')
table = dynamodb.Table(os.environ['DYNAMODB_TABLE_NAME'])

# Configuration from environment variables
INCLUDE_SECURITY_GROUPS = os.environ.get('INCLUDE_SECURITY_GROUPS', 'false').lower() == 'true'
SRC_TAG_PREFIX = os.environ.get('SRC_TAG_PREFIX', 'src-tag-')
DST_TAG_PREFIX = os.environ.get('DST_TAG_PREFIX', 'dst-tag-')
DST_PREFIX = os.environ.get('DST_PREFIX', 'dst-')

class ProcessingStats:
    def __init__(self):
        self.successful_records = 0
        self.failed_records = 0
        self.validation_errors = 0
        self.dynamodb_errors = 0
        self.unexpected_errors = 0

class FlowLogEnricher:
    def __init__(self):
        self.stats = ProcessingStats()
    
    def put_metric(self, name: str, value: int) -> None:
        """Put CloudWatch metric with error handling"""
        try:
            cloudwatch.put_metric_data(
                Namespace='FirehoseTransformation',
                MetricData=[{
                    'MetricName': name,
                    'Value': value,
                    'Unit': 'Count'
                }]
            )
        except Exception as e:
            logger.error(f"Error putting metric {name}: {str(e)}")

    def validate_record_schema(self, record_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize record schema"""
        # Handle null values
        for key in record_dict:
            if record_dict[key] == '-' or record_dict[key] == '':
                record_dict[key] = None

        # Convert numeric fields
        numeric_fields = ['bytes', 'dstport', 'srcport', 'packets']
        for field in numeric_fields:
            if field in record_dict and record_dict[field] is not None:
                try:
                    record_dict[field] = int(record_dict[field])
                except ValueError:
                    logger.warning(f"Invalid numeric value for {field}: {record_dict[field]}")
                    record_dict[field] = None

        return record_dict

    def get_instance_metadata_from_dynamodb(self, ip_address: str) -> Optional[Dict[str, Any]]:
        """Get instance metadata from DynamoDB with error handling"""
        try:
            response = table.query(
                KeyConditionExpression='ip_address = :ip',
                ExpressionAttributeValues={':ip': ip_address},
                ScanIndexForward=False,
                Limit=1
            )

            if response['Items']:
                item = response['Items'][0]
                if 'tags' in item:
                    item['resource_tags'] = item['tags']
                return item
            return None
        except ClientError as e:
            logger.error(f"Error reading from DynamoDB for IP {ip_address}: {str(e)}")
            self.stats.dynamodb_errors += 1
            return None

    def sanitize_tag_key(self, key: str) -> str:
        """Sanitize tag keys to be safe as field names"""
        import re
        # Remove special characters and limit length
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', key.strip())
        return sanitized[:64]  # Limit length for field names

    def enrich_with_metadata(self, record_dict: Dict[str, Any], ip_address: str, 
                           prefix: str, tag_prefix: str) -> None:
        """Enrich record with metadata for given IP address"""
        if not ip_address or ip_address.strip() == "-":
            return

        metadata = self.get_instance_metadata_from_dynamodb(ip_address)
        if not metadata:
            return

        # Add basic metadata
        record_dict[f"{prefix}vpc-id"] = metadata.get("vpc_id", "")
        record_dict[f"{prefix}az-id"] = metadata.get("az_id", "")
        record_dict[f"{prefix}subnet-id"] = metadata.get("subnet_id", "")
        record_dict[f"{prefix}interface-id"] = metadata.get("eni_id", "")
        record_dict[f"{prefix}instance-id"] = metadata.get("instance_id", "")
        record_dict[f"{prefix}resource-owner"] = metadata.get("resource_owner", "")

        # Add resource tags
        if 'resource_tags' in metadata:
            for tag in metadata['resource_tags']:
                key = tag.get('Key')
                value = tag.get('Value')
                if key and value is not None:
                    safe_key = self.sanitize_tag_key(key)
                    record_dict[f"{tag_prefix}{safe_key}"] = value

        # Add security groups if enabled
        if INCLUDE_SECURITY_GROUPS and 'security_groups' in metadata:
            for idx, sg in enumerate(metadata['security_groups']):
                record_dict[f"{prefix}sg-{idx+1}-id"] = sg.get('GroupId', '')
                record_dict[f"{prefix}sg-{idx+1}-name"] = sg.get('GroupName', '')

    def process_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Process individual flow log record"""
        try:
            payload = base64.b64decode(record['data']).decode('utf-8')
            json_payload = json.loads(payload)
            flow_log_record = json_payload["message"].split(" ")

            # Validate we have enough fields
            if len(flow_log_record) < 29:
                raise ValueError(f"Insufficient flow log fields: {len(flow_log_record)}")

            record_dict = OrderedDict({
                "account-id": flow_log_record[0],
                "action": flow_log_record[1],
                "az-id": flow_log_record[2],
                "bytes": flow_log_record[3],
                "dstaddr": flow_log_record[4],
                "dstport": flow_log_record[5],
                "end": flow_log_record[6],
                "flow-direction": flow_log_record[7],
                "instance-id": flow_log_record[8],
                "interface-id": flow_log_record[9],
                "log-status": flow_log_record[10],
                "packets": flow_log_record[11],
                "pkt-dst-aws-service": flow_log_record[12],
                "pkt-dstaddr": flow_log_record[13],
                "pkt-src-aws-service": flow_log_record[14],
                "pkt-srcaddr": flow_log_record[15],
                "protocol": flow_log_record[16],
                "region": flow_log_record[17],
                "srcaddr": flow_log_record[18],
                "srcport": flow_log_record[19],
                "start": flow_log_record[20],
                "sublocation-id": flow_log_record[21],
                "sublocation-type": flow_log_record[22],
                "subnet-id": flow_log_record[23],
                "tcp-flags": flow_log_record[24],
                "traffic-path": flow_log_record[25],
                "type": flow_log_record[26],
                "version": flow_log_record[27],
                "vpc-id": flow_log_record[28]
            })

            # Enrich with source metadata
            self.enrich_with_metadata(
                record_dict, 
                record_dict['srcaddr'], 
                'src-', 
                SRC_TAG_PREFIX
            )

            # Enrich with destination metadata
            self.enrich_with_metadata(
                record_dict, 
                record_dict['dstaddr'], 
                DST_PREFIX, 
                DST_TAG_PREFIX
            )

            # Validate and normalize schema
            record_dict = self.validate_record_schema(record_dict)

            self.stats.successful_records += 1
            logger.debug(f"Successfully processed record: {record['recordId']}")

            return {
                'recordId': record['recordId'],
                'result': 'Ok',
                'data': base64.b64encode(json.dumps(record_dict).encode('utf-8')).decode('utf-8')
            }

        except json.JSONDecodeError as e:
            self.stats.validation_errors += 1
            logger.error(f"JSON decode error for record {record['recordId']}: {str(e)}")
            return self._create_failed_record(record, "JSON_DECODE_ERROR")
        
        except ValueError as e:
            self.stats.validation_errors += 1
            logger.error(f"Validation error for record {record['recordId']}: {str(e)}")
            return self._create_failed_record(record, "VALIDATION_ERROR")
        
        except Exception as e:
            self.stats.unexpected_errors += 1
            logger.error(f"Unexpected error processing record {record['recordId']}: {str(e)}")
            return self._create_failed_record(record, "PROCESSING_ERROR")

    def _create_failed_record(self, record: Dict[str, Any], error_type: str) -> Dict[str, Any]:
        """Create a failed record response"""
        self.stats.failed_records += 1
        return {
            'recordId': record['recordId'],
            'result': 'ProcessingFailed',
            'data': record['data']
        }

    def publish_metrics(self) -> None:
        """Publish processing metrics to CloudWatch"""
        metrics = [
            ('SuccessfulRecords', self.stats.successful_records),
            ('FailedRecords', self.stats.failed_records),
            ('ValidationErrors', self.stats.validation_errors),
            ('DynamoDBErrors', self.stats.dynamodb_errors),
            ('UnexpectedErrors', self.stats.unexpected_errors)
        ]
        
        for metric_name, value in metrics:
            if value > 0:  # Only publish non-zero metrics
                self.put_metric(metric_name, value)

def lambda_handler(event, context):
    """Main Lambda handler"""
    enricher = FlowLogEnricher()
    output = []

    try:
        for record in event['records']:
            output_record = enricher.process_record(record)
            output.append(output_record)

        # Publish metrics
        enricher.publish_metrics()

        logger.info(
            f'Processing complete - Success: {enricher.stats.successful_records}, '
            f'Failed: {enricher.stats.failed_records}, '
            f'Validation Errors: {enricher.stats.validation_errors}, '
            f'DynamoDB Errors: {enricher.stats.dynamodb_errors}, '
            f'Unexpected Errors: {enricher.stats.unexpected_errors}'
        )

        return {'records': output}

    except Exception as e:
        logger.error(f"Fatal error in lambda_handler: {str(e)}")
        raise