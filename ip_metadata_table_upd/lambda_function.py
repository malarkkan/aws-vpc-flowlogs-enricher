import boto3
import os
import json
import logging
from datetime import datetime, timezone
from botocore.exceptions import ClientError
import time

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO if os.environ.get('LOG_LEVEL') != 'DEBUG' else logging.DEBUG)

# Resource processing functions
def process_nat_gateway(nat_gw, table, current_time, ttl, stats):
    """Process NAT Gateway and store its metadata"""
    nat_ip = nat_gw.get('NatGatewayAddresses', [{}])[0].get('PublicIp')
    if nat_ip:
        item = {
            'ip_address': nat_ip,
            'timestamp': current_time.isoformat(),
            'resource_type': 'nat_gateway',
            'resource_id': nat_gw['NatGatewayId'],
            'vpc_id': nat_gw.get('VpcId', ''),
            'subnet_id': nat_gw.get('SubnetId', ''),
            'tags': nat_gw.get('Tags', []),
            'status': nat_gw.get('State'),
            'ttl': ttl
        }
        table.put_item(Item=item)
        stats['updated_items'] += 1
        stats['processed_items'] += 1

def process_load_balancer(lb, elbv2_client, table, current_time, ttl, stats):
    """Process Load Balancer and store its metadata"""
    try:
        # Get LB tags
        tags_response = elbv2_client.describe_tags(ResourceArns=[lb['LoadBalancerArn']])
        tags = tags_response.get('TagDescriptions', [{}])[0].get('Tags', [])
        
        # Store metadata for each IP address
        for az_mapping in lb.get('AvailabilityZones', []):
            for address in az_mapping.get('LoadBalancerAddresses', []):
                if 'IpAddress' in address:
                    item = {
                        'ip_address': address['IpAddress'],
                        'timestamp': current_time.isoformat(),
                        'resource_type': 'load_balancer',
                        'resource_id': lb['LoadBalancerArn'],
                        'load_balancer_name': lb.get('LoadBalancerName'),
                        'vpc_id': lb.get('VpcId'),
                        'availability_zone': az_mapping.get('ZoneName'),
                        'subnet_id': az_mapping.get('SubnetId'),
                        'tags': tags,
                        'state': lb.get('State', {}).get('Code'),
                        'ttl': ttl
                    }
                    table.put_item(Item=item)
                    stats['updated_items'] += 1
                    stats['processed_items'] += 1
    except Exception as e:
        logger.error(f"Error processing Load Balancer {lb.get('LoadBalancerArn')}: {str(e)}")
        stats['errors'] += 1

def process_rds_instance(instance, rds_client, table, current_time, ttl, stats):
    """Process RDS instance and store its metadata"""
    try:
        # Get RDS tags
        arn = instance['DBInstanceArn']
        tags_response = rds_client.list_tags_for_resource(ResourceName=arn)
        tags = tags_response.get('TagList', [])
        
        # Process endpoints
        endpoints = []
        if 'Endpoint' in instance:
            endpoints.append({
                'address': instance['Endpoint']['Address'],
                'port': instance['Endpoint']['Port'],
                'type': 'primary'
            })
        
        # Process read replicas endpoints
        if 'ReadReplicaDBInstanceIdentifiers' in instance:
            for replica_id in instance['ReadReplicaDBInstanceIdentifiers']:
                try:
                    replica = rds_client.describe_db_instances(DBInstanceIdentifier=replica_id)['DBInstances'][0]
                    if 'Endpoint' in replica:
                        endpoints.append({
                            'address': replica['Endpoint']['Address'],
                            'port': replica['Endpoint']['Port'],
                            'type': 'replica'
                        })
                except Exception as e:
                    logger.error(f"Error fetching replica {replica_id}: {str(e)}")

        # Store DNS entries in DynamoDB
        for endpoint in endpoints:
            item = {
                'ip_address': endpoint['address'],  # This is actually a DNS name for RDS
                'timestamp': current_time.isoformat(),
                'resource_type': 'rds',
                'resource_id': instance['DBInstanceIdentifier'],
                'endpoint_type': endpoint['type'],
                'port': endpoint['port'],
                'vpc_id': instance.get('DBSubnetGroup', {}).get('VpcId'),
                'subnet_group': instance.get('DBSubnetGroup', {}).get('DBSubnetGroupName'),
                'availability_zone': instance.get('AvailabilityZone'),
                'engine': instance.get('Engine'),
                'engine_version': instance.get('EngineVersion'),
                'status': instance.get('DBInstanceStatus'),
                'tags': tags,
                'ttl': ttl,
                'is_dns_entry': True  # Flag to indicate this is a DNS entry
            }
            table.put_item(Item=item)
            stats['updated_items'] += 1
            stats['processed_items'] += 1

    except Exception as e:
        logger.error(f"Error processing RDS instance {instance.get('DBInstanceIdentifier')}: {str(e)}")
        stats['errors'] += 1

def process_transit_gateway(tgw, table, current_time, ttl, stats):
    """Process Transit Gateway and store its metadata"""
    try:
        # Store metadata for Transit Gateway
        item = {
            'resource_id': tgw['TransitGatewayId'],
            'timestamp': current_time.isoformat(),
            'resource_type': 'transit_gateway',
            'state': tgw.get('State'),
            'owner_id': tgw.get('OwnerId'),
            'description': tgw.get('Description'),
            'tags': tgw.get('Tags', []),
            'ttl': ttl
        }
        
        # Store association with VPCs
        attachments = get_transit_gateway_attachments(tgw['TransitGatewayId'])
        if attachments:
            item['vpc_attachments'] = attachments

        table.put_item(Item=item)
        stats['updated_items'] += 1
        stats['processed_items'] += 1

    except Exception as e:
        logger.error(f"Error processing Transit Gateway {tgw.get('TransitGatewayId')}: {str(e)}")
        stats['errors'] += 1

def process_network_interface(eni, ec2_client, table, current_time, ttl, stats):
    """Process Network Interface and store its metadata"""
    try:
        interface_type = eni.get('InterfaceType', '')
        description = eni.get('Description', '').lower()
        
        # Determine resource type
        resource_type = determine_resource_type(description)
        resource_id = extract_resource_id(description, resource_type)
        
        base_metadata = {
            'timestamp': current_time.isoformat(),
            'resource_type': resource_type,
            'vpc_id': eni.get('VpcId', ''),
            'subnet_id': eni.get('SubnetId', ''),
            'availability_zone': eni.get('AvailabilityZone', ''),
            'interface_type': interface_type,
            'network_interface_id': eni['NetworkInterfaceId'],
            'tags': eni.get('Tags', []),
            'security_groups': [sg['GroupId'] for sg in eni.get('Groups', [])],
            'ttl': ttl
        }

        # Add instance information if available
        if eni.get('Attachment', {}).get('InstanceId'):
            base_metadata['instance_id'] = eni['Attachment']['InstanceId']
            instance_tags = get_instance_tags(ec2_client, eni['Attachment']['InstanceId'])
            if instance_tags:
                base_metadata['instance_tags'] = instance_tags

        # Process primary private IP
        if 'PrivateIpAddress' in eni:
            metadata = base_metadata.copy()
            metadata['ip_address'] = eni['PrivateIpAddress']
            metadata['ip_type'] = 'private'
            table.put_item(Item=metadata)
            stats['updated_items'] += 1
            stats['processed_items'] += 1

        # Process public IP if available
        if 'Association' in eni and 'PublicIp' in eni['Association']:
            metadata = base_metadata.copy()
            metadata['ip_address'] = eni['Association']['PublicIp']
            metadata['ip_type'] = 'public'
            table.put_item(Item=metadata)
            stats['updated_items'] += 1
            stats['processed_items'] += 1

        # Process secondary private IPs
        for ip_config in eni.get('PrivateIpAddresses', []):
            if not ip_config.get('Primary', False):
                metadata = base_metadata.copy()
                metadata['ip_address'] = ip_config['PrivateIpAddress']
                metadata['ip_type'] = 'private'
                table.put_item(Item=metadata)
                stats['updated_items'] += 1
                stats['processed_items'] += 1

                if 'Association' in ip_config and 'PublicIp' in ip_config['Association']:
                    metadata = base_metadata.copy()
                    metadata['ip_address'] = ip_config['Association']['PublicIp']
                    metadata['ip_type'] = 'public'
                    table.put_item(Item=metadata)
                    stats['updated_items'] += 1
                    stats['processed_items'] += 1

    except Exception as e:
        logger.error(f"Error processing ENI {eni.get('NetworkInterfaceId')}: {str(e)}")
        stats['errors'] += 1

# Helper functions for resource fetching
def get_nat_gateways(ec2_client):
    """Fetch all NAT Gateways"""
    nat_gateways = []
    try:
        paginator = ec2_client.get_paginator('describe_nat_gateways')
        for page in paginator.paginate():
            nat_gateways.extend(page['NatGateways'])
    except Exception as e:
        logger.error(f"Error fetching NAT Gateways: {str(e)}")
    return nat_gateways

def get_load_balancers(elbv2_client):
    """Fetch all Load Balancers"""
    load_balancers = []
    try:
        paginator = elbv2_client.get_paginator('describe_load_balancers')
        for page in paginator.paginate():
            load_balancers.extend(page['LoadBalancers'])
    except Exception as e:
        logger.error(f"Error fetching Load Balancers: {str(e)}")
    return load_balancers

def get_rds_instances(rds_client):
    """Fetch all RDS instances"""
    instances = []
    try:
        paginator = rds_client.get_paginator('describe_db_instances')
        for page in paginator.paginate():
            instances.extend(page['DBInstances'])
    except Exception as e:
        logger.error(f"Error fetching RDS instances: {str(e)}")
    return instances

def get_transit_gateways(ec2_client):
    """Fetch all Transit Gateways"""
    transit_gateways = []
    try:
        paginator = ec2_client.get_paginator('describe_transit_gateways')
        for page in paginator.paginate():
            transit_gateways.extend(page['TransitGateways'])
    except Exception as e:
        logger.error(f"Error fetching Transit Gateways: {str(e)}")
    return transit_gateways

def get_transit_gateway_attachments(tgw_id):
    """Fetch Transit Gateway VPC attachments"""
    try:
        ec2_client = boto3.client('ec2')
        response = ec2_client.describe_transit_gateway_attachments(
            Filters=[{'Name': 'transit-gateway-id', 'Values': [tgw_id]}]
        )
        return response.get('TransitGatewayAttachments', [])
    except Exception as e:
        logger.error(f"Error fetching Transit Gateway attachments: {str(e)}")
        return []

def get_instance_tags(ec2_client, instance_id):
    """Fetch EC2 instance tags"""
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        if response['Reservations'] and response['Reservations'][0]['Instances']:
            return response['Reservations'][0]['Instances'][0].get('Tags', [])
        return []
    except Exception as e:
        logger.error(f"Error fetching instance tags for {instance_id}: {str(e)}")
        return []

def determine_resource_type(description):
    """Determine resource type from ENI description"""
    description = description.lower()
    if 'nat gateway' in description:
        return 'nat_gateway'
    elif 'transit gateway' in description:
        return 'transit_gateway'
    elif 'loadbalancer' in description or 'elb' in description:
        return 'load_balancer'
    elif 'rds' in description:
        return 'rds'
    elif 'vpc endpoint' in description:
        return 'vpc_endpoint'
    return 'other'

def extract_resource_id(description, resource_type):
    """Extract resource ID from description"""
    try:
        description = description.lower()
        if resource_type == 'nat_gateway' and 'nat gateway' in description:
            return description.split('nat gateway')[1].strip()
        elif resource_type == 'transit_gateway' and 'transit gateway' in description:
            return description.split('transit gateway')[1].strip()
        elif resource_type == 'vpc_endpoint' and 'vpc endpoint' in description:
            return description.split('vpc endpoint')[1].strip()
    except Exception:
        pass
    return None

def prepare_and_publish_metrics(stats, execution_time, current_time):
    """Prepare and publish CloudWatch metrics"""
    try:
        metrics = [
            {
                'MetricName': 'ProcessedItems',
                'Value': stats['processed_items'],
                'Unit': 'Count',
                'Timestamp': current_time
            },
            {
                'MetricName': 'UpdatedItems',
                'Value': stats['updated_items'],
                'Unit': 'Count',
                'Timestamp': current_time
            },
            {
                'MetricName': 'Errors',
                'Value': stats['errors'],
                'Unit': 'Count',
                'Timestamp': current_time
            },
            {
                'MetricName': 'ExecutionTime',
                'Value': execution_time,
                'Unit': 'Seconds',
                'Timestamp': current_time
            }
        ]

        # Add resource type metrics
        for resource_type, count in stats['resource_types'].items():
            metrics.append({
                'MetricName': f'{resource_type.capitalize()}Count',
                'Value': count,
                'Unit': 'Count',
                'Timestamp': current_time
            })

        cloudwatch = boto3.client('cloudwatch')
        cloudwatch.put_metric_data(
            Namespace='VPCFlowLogs/Metadata',
            MetricData=metrics
        )
    except Exception as e:
        logger.error(f"Error publishing metrics: {str(e)}")

def lambda_handler(event, context):
    """
    Update IP metadata in DynamoDB for all VPC resources.
    """
    start_time = time.time()
    metrics = []
    
    try:
        # Initialize AWS clients
        ec2_client = boto3.client('ec2')
        elbv2_client = boto3.client('elbv2')
        rds_client = boto3.client('rds')
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.environ['DYNAMODB_TABLE_NAME'])
        
        # Get current timestamp for TTL
        current_time = datetime.now(timezone.utc)
        ttl = int((current_time.timestamp() + (90 * 24 * 60 * 60)))  # 90 days TTL
        
        # Track statistics
        stats = {
            'processed_items': 0,
            'updated_items': 0,
            'errors': 0,
            'resource_types': {}
        }

        def increment_resource_stat(resource_type):
            stats['resource_types'][resource_type] = stats['resource_types'].get(resource_type, 0) + 1

        # Process NAT Gateways
        try:
            nat_gateways = get_nat_gateways(ec2_client)
            for nat_gw in nat_gateways:
                try:
                    process_nat_gateway(nat_gw, table, current_time, ttl, stats)
                except Exception as e:
                    logger.error(f"Error processing NAT Gateway {nat_gw.get('NatGatewayId')}: {str(e)}")
                    stats['errors'] += 1
        except Exception as e:
            logger.error(f"Error fetching NAT Gateways: {str(e)}")
            stats['errors'] += 1

        # Process Load Balancers
        try:
            load_balancers = get_load_balancers(elbv2_client)
            for lb in load_balancers:
                try:
                    process_load_balancer(lb, elbv2_client, table, current_time, ttl, stats)
                except Exception as e:
                    logger.error(f"Error processing Load Balancer {lb.get('LoadBalancerArn')}: {str(e)}")
                    stats['errors'] += 1
        except Exception as e:
            logger.error(f"Error fetching Load Balancers: {str(e)}")
            stats['errors'] += 1

        # Process RDS Instances
        try:
            rds_instances = get_rds_instances(rds_client)
            for instance in rds_instances:
                try:
                    process_rds_instance(instance, rds_client, table, current_time, ttl, stats)
                except Exception as e:
                    logger.error(f"Error processing RDS Instance {instance.get('DBInstanceIdentifier')}: {str(e)}")
                    stats['errors'] += 1
        except Exception as e:
            logger.error(f"Error fetching RDS Instances: {str(e)}")
            stats['errors'] += 1

        # Process Transit Gateways
        try:
            transit_gateways = get_transit_gateways(ec2_client)
            for tgw in transit_gateways:
                try:
                    process_transit_gateway(tgw, table, current_time, ttl, stats)
                except Exception as e:
                    logger.error(f"Error processing Transit Gateway {tgw.get('TransitGatewayId')}: {str(e)}")
                    stats['errors'] += 1
        except Exception as e:
            logger.error(f"Error fetching Transit Gateways: {str(e)}")
            stats['errors'] += 1

        # Process Network Interfaces
        paginator = ec2_client.get_paginator('describe_network_interfaces')
        for page in paginator.paginate():
            for eni in page['NetworkInterfaces']:
                try:
                    process_network_interface(eni, ec2_client, table, current_time, ttl, stats)
                except Exception as e:
                    logger.error(f"Error processing ENI {eni.get('NetworkInterfaceId')}: {str(e)}")
                    stats['errors'] += 1

        # Calculate execution time
        execution_time = time.time() - start_time
        
        # Prepare CloudWatch metrics
        prepare_and_publish_metrics(stats, execution_time, current_time)
        
        logger.info(f"Successfully processed {stats['processed_items']} items, "
                   f"updated {stats['updated_items']} items, "
                   f"encountered {stats['errors']} errors")
        logger.info(f"Resource type breakdown: {json.dumps(stats['resource_types'])}")
        logger.info(f"Execution time: {execution_time:.2f} seconds")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'stats': stats,
                'execution_time': execution_time
            })
        }
        
    except Exception as e:
        error_message = f"Fatal error in update_ip_metadata: {str(e)}"
        logger.error(error_message)
        return {
            'statusCode': 500,
            'body': json.dumps({'error': error_message})
        }
