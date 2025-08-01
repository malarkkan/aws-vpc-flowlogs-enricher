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

# Initialize AWS clients
ec2_client = boto3.client('ec2')
ec2 = boto3.resource('ec2')
dynamodb = boto3.resource('dynamodb')
cloudwatch = boto3.client('cloudwatch')
table = dynamodb.Table(os.environ['DYNAMODB_TABLE_NAME'])

def put_metric(name, value):
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
        print(f"Error putting metric {name}: {str(e)}")

def validate_record_schema(record_dict):
    for key in record_dict:
        if record_dict[key] == '-' or record_dict[key] == '':
            record_dict[key] = None

    numeric_fields = ['bytes', 'dstport', 'srcport', 'packets']
    for field in numeric_fields:
        if field in record_dict and record_dict[field] is not None:
            try:
                record_dict[field] = int(record_dict[field])
            except ValueError:
                record_dict[field] = None

    return record_dict

def get_instance_metadata_from_dynamodb(ip_address):
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
        print(f"Error reading from DynamoDB: {str(e)}")
        return None

def sanitize_tag_key(key):
    """Sanitize tag keys to be safe as field names."""
    return key.strip().replace(" ", "_").replace(":", "_")

def process_record(record, src_tag_prefix, dst_tag_prefix, dst_prefix):
    payload = base64.b64decode(record['data']).decode('utf-8')
    json_payload = json.loads(payload)
    flow_log_record = json_payload["message"].split(" ")

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

    source_addr = record_dict['srcaddr']
    if source_addr and source_addr.strip() != "-":
        src_metadata = get_instance_metadata_from_dynamodb(source_addr)
        if src_metadata:
            record_dict["src-vpc-id"] = src_metadata.get("vpc_id", "")
            record_dict["src-az-id"] = src_metadata.get("az_id", "")
            record_dict["src-subnet-id"] = src_metadata.get("subnet_id", "")
            record_dict["src-interface-id"] = src_metadata.get("eni_id", "")
            record_dict["src-instance-id"] = src_metadata.get("instance_id", "")
            record_dict["src-resource-owner"] = src_metadata.get("resource_owner", "")

            if 'resource_tags' in src_metadata:
                for tag in src_metadata['resource_tags']:
                    key = tag.get('Key')
                    value = tag.get('Value')
                    if key and value is not None:
                        safe_key = sanitize_tag_key(key)
                        record_dict[f"{src_tag_prefix}{safe_key}"] = value


    destination_addr = record_dict['dstaddr']
    if destination_addr and destination_addr.strip() != "-":
        dst_metadata = get_instance_metadata_from_dynamodb(destination_addr)
        if dst_metadata:
            record_dict[f"{dst_prefix}vpc-id"] = dst_metadata.get("vpc_id", "")
            record_dict[f"{dst_prefix}az-id"] = dst_metadata.get("az_id", "")
            record_dict[f"{dst_prefix}subnet-id"] = dst_metadata.get("subnet_id", "")
            record_dict[f"{dst_prefix}interface-id"] = dst_metadata.get("eni_id", "")
            record_dict[f"{dst_prefix}instance-id"] = dst_metadata.get("instance_id", "")
            record_dict[f"{dst_prefix}resource-owner"] = dst_metadata.get("resource_owner", "")

            if 'resource_tags' in dst_metadata:
                for tag in dst_metadata['resource_tags']:
                    key = tag.get('Key')
                    value = tag.get('Value')
                    if key and value is not None:
                        safe_key = sanitize_tag_key(key)
                        record_dict[f"{dst_tag_prefix}{safe_key}"] = value


    return record_dict

def lambda_handler(event, context):
    output = []
    successful_records = 0
    failed_records = 0

    src_tag_prefix = 'src-tag-'
    dst_tag_prefix = 'dst-tag-'
    dst_prefix = 'dst-'

    try:
        for record in event['records']:
            try:
                record_dict = process_record(record, src_tag_prefix, dst_tag_prefix, dst_prefix)
                record_dict = validate_record_schema(record_dict)

                output_record = {
                    'recordId': record['recordId'],
                    'result': 'Ok',
                    'data': base64.b64encode(json.dumps(record_dict).encode('utf-8')).decode('utf-8')
                }

                successful_records += 1
                print(f"Successfully processed record: {record['recordId']}")

            except Exception as e:
                failed_records += 1
                print(f"Error processing record {record['recordId']}: {str(e)}")
                output_record = {
                    'recordId': record['recordId'],
                    'result': 'ProcessingFailed',
                    'data': record['data']
                }

            output.append(output_record)

        # put_metric('SuccessfulRecords', successful_records)
        # put_metric('FailedRecords', failed_records)

        print(f'Successfully processed {successful_records} records. Failed: {failed_records}')
        return {'records': output}

    except Exception as e:
        print(f"Fatal error in lambda_handler: {str(e)}")
        raise


        print(f'Successfully processed {successful_records} records. Failed: {failed_records}')
        return {'records': output}

    except Exception as e:
        print(f"Fatal error in lambda_handler: {str(e)}")
        raise
