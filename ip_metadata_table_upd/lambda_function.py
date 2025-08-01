import boto3
import os
import json
from botocore.exceptions import ClientError
from datetime import datetime, timezone, timedelta


def lambda_handler(event, context):
    # Initialize AWS clients
    ec2_client = boto3.client('ec2')
    elbv2_client = boto3.client('elbv2')
    rds_client = boto3.client('rds')
    dynamodb = boto3.resource('dynamodb')
    cloudwatch = boto3.client('cloudwatch')
    
    table = dynamodb.Table(os.environ['DYNAMODB_TABLE_NAME'])

    # Initialize counters
    stats = {
        'processed_count': 0,
        'error_count': 0,
        'tag_count': 0,
        'cleaned_count': 0,
        'resource_types': {}
    }

    current_time = datetime.now(timezone.utc)
    ttl_value = int((current_time + timedelta(hours=2)).timestamp())

    def get_natgateway_tags(nat_gateway_id):
        """Fetch NAT Gateway tags and status"""
        try:
            if not nat_gateway_id:
                return [], None
            
            response = ec2_client.describe_nat_gateways(NatGatewayIds=[nat_gateway_id])
            if response['NatGateways']:
                nat_gateway = response['NatGateways'][0]
                return nat_gateway.get('Tags', []), nat_gateway.get('State')
            return [], None
        except ClientError as e:
            print(f"Error fetching NAT Gateway tags for {nat_gateway_id}: {e}")
            return [], None

    def get_transit_gateway_tags(tgw_id):
        """Fetch Transit Gateway tags"""
        try:
            if not tgw_id:
                return []
            
            response = ec2_client.describe_transit_gateways(TransitGatewayIds=[tgw_id])
            if response['TransitGateways']:
                return response['TransitGateways'][0].get('Tags', [])
            return []
        except ClientError as e:
            print(f"Error fetching Transit Gateway tags for {tgw_id}: {e}")
            return []

    def get_load_balancer_tags(lb_arn):
        """Fetch Load Balancer tags and health"""
        try:
            if not lb_arn:
                return [], None
            
            tags_response = elbv2_client.describe_tags(ResourceArns=[lb_arn])
            health_response = elbv2_client.describe_load_balancers(LoadBalancerArns=[lb_arn])
            
            tags = tags_response.get('TagDescriptions', [{}])[0].get('Tags', [])
            state = health_response['LoadBalancers'][0]['State']['Code'] if health_response['LoadBalancers'] else None
            
            return tags, state
        except ClientError as e:
            print(f"Error fetching Load Balancer data for {lb_arn}: {e}")
            return [], None

    def get_vpc_endpoint_tags(endpoint_id):
        """Fetch VPC Endpoint tags"""
        try:
            if not endpoint_id:
                return []
            
            response = ec2_client.describe_vpc_endpoints(VpcEndpointIds=[endpoint_id])
            if response['VpcEndpoints']:
                return response['VpcEndpoints'][0].get('Tags', [])
            return []
        except ClientError as e:
            print(f"Error fetching VPC Endpoint tags for {endpoint_id}: {e}")
            return []

    #@retry(stop_max_attempt_number=3, wait_exponential_multiplier=1000)
    def get_rds_tags(db_identifier):
        """Fetch RDS instance tags and status"""
        try:
            if not db_identifier:
                return [], None
            
            response = rds_client.describe_db_instances(DBInstanceIdentifier=db_identifier)
            if response['DBInstances']:
                instance = response['DBInstances'][0]
                arn = instance['DBInstanceArn']
                tags_response = rds_client.list_tags_for_resource(ResourceName=arn)
                return tags_response.get('TagList', []), instance.get('DBInstanceStatus')
            return [], None
        except ClientError as e:
            print(f"Error fetching RDS data for {db_identifier}: {e}")
            return [], None

    def get_resource_tags(instance_id):
        """Fetch EC2 instance tags"""
        try:
            if not instance_id:
                return []
            
            response = ec2_client.describe_instances(InstanceIds=[instance_id])
            if response['Reservations'] and response['Reservations'][0]['Instances']:
                instance = response['Reservations'][0]['Instances'][0]
                return instance.get('Tags', [])
            return []
        except ClientError as e:
            print(f"Error fetching EC2 tags for instance {instance_id}: {e}")
            return []

    def determine_resource_type(description):
        """Determine the type of resource based on ENI description"""
        description = description.lower()
        if 'nat gateway' in description:
            return 'nat_gateway'
        elif 'transit gateway' in description:
            return 'transit_gateway'
        elif 'elb' in description:
            return 'load_balancer'
        elif 'vpc endpoint' in description:
            return 'vpc_endpoint'
        elif 'rds' in description:
            return 'rds'
        return 'other'

    def extract_resource_id(description, resource_type):
        """Extract resource ID from description"""
        description = description.lower()
        if resource_type == 'nat_gateway':
            return description.split('nat gateway')[1].strip()
        elif resource_type == 'transit_gateway':
            return description.split('transit gateway')[1].strip()
        elif resource_type == 'load_balancer':
            # Implement proper extraction logic for LB ARN
            return None
        elif resource_type == 'vpc_endpoint':
            return description.split('vpc endpoint')[1].strip()
        elif resource_type == 'rds':
            # Implement proper extraction logic for RDS identifier
            return None
        return None

    def publish_metrics(stats):
        """Publish metrics to CloudWatch"""
        try:
            metrics = [
                {
                    'MetricName': 'ProcessedCount',
                    'Value': stats['processed_count'],
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'ErrorCount',
                    'Value': stats['error_count'],
                    'Unit': 'Count'
                }
            ]
            
            for resource_type, count in stats['resource_types'].items():
                metrics.append({
                    'MetricName': f'{resource_type}Count',
                    'Value': count,
                    'Unit': 'Count'
                })

            cloudwatch.put_metric_data(
                Namespace='IPEnrichment',
                MetricData=metrics
            )
        except ClientError as e:
            print(f"Error publishing metrics: {e}")

    def clean_old_entries():
        """Clean entries using TTL instead of manual deletion"""
        try:
            response = table.scan(
                ProjectionExpression='ip_address,#ts',
                ExpressionAttributeNames={'#ts': 'timestamp'}
            )
            
            for item in response.get('Items', []):
                if 'timestamp' not in item:
                    table.delete_item(Key={'ip_address': item['ip_address']})
                    stats['cleaned_count'] += 1
                    
        except ClientError as e:
            print(f"Error cleaning old entries: {e}")
            raise

    def process_interface(interface):
        """Process a single network interface"""
        try:
            eni_id = interface['NetworkInterfaceId']
            requester_id = interface.get('RequesterId', '')
            owner_id = interface.get('OwnerId', '')
            instance_id = interface.get('Attachment', {}).get('InstanceId', '')
            description = interface.get('Description', '').lower()
            
            # Determine resource owner and type
            resource_owner = owner_id if requester_id in ('-', '') else requester_id
            resource_type = determine_resource_type(description)
            resource_id = extract_resource_id(description, resource_type)

            # Update resource type stats
            stats['resource_types'][resource_type] = stats['resource_types'].get(resource_type, 0) + 1

            # Tag the ENI with resource_owner
            try:
                ec2_client.create_tags(
                    Resources=[eni_id],
                    Tags=[
                        {'Key': 'resource_owner', 'Value': resource_owner},
                        {'Key': 'resource_type', 'Value': resource_type}
                    ]
                )
                stats['tag_count'] += 1
            except ClientError as e:
                print(f"Error tagging ENI {eni_id}: {e}")
                stats['error_count'] += 1
                return

            # Initialize metadata
            metadata = {
                'resource_owner': resource_owner,
                'resource_type': resource_type,
                'vpc_id': interface.get('VpcId', ''),
                'az_id': interface.get('AvailabilityZone', ''),
                'subnet_id': interface.get('SubnetId', ''),
                'instance_id': instance_id,
                'eni_id': eni_id,
                'timestamp': current_time.isoformat(),
                'ttl': ttl_value
            }

            # Get resource-specific tags and status
            if resource_type == 'nat_gateway':
                tags, status = get_natgateway_tags(resource_id)
                if tags:
                    metadata['nat_gateway_tags'] = tags
                    metadata['nat_gateway_id'] = resource_id
                    metadata['nat_gateway_status'] = status
            
            elif resource_type == 'transit_gateway':
                tags = get_transit_gateway_tags(resource_id)
                if tags:
                    metadata['transit_gateway_tags'] = tags
                    metadata['transit_gateway_id'] = resource_id
            
            elif resource_type == 'load_balancer':
                tags, status = get_load_balancer_tags(resource_id)
                if tags:
                    metadata['load_balancer_tags'] = tags
                    metadata['load_balancer_arn'] = resource_id
                    metadata['load_balancer_status'] = status
            
            elif resource_type == 'vpc_endpoint':
                tags = get_vpc_endpoint_tags(resource_id)
                if tags:
                    metadata['vpc_endpoint_tags'] = tags
                    metadata['vpc_endpoint_id'] = resource_id
            
            elif resource_type == 'rds':
                tags, status = get_rds_tags(resource_id)
                if tags:
                    metadata['rds_tags'] = tags
                    metadata['rds_identifier'] = resource_id
                    metadata['rds_status'] = status

            # Add EC2 instance tags if applicable
            if instance_id:
                resource_tags = get_resource_tags(instance_id)
                if resource_tags:
                    metadata['resource_tags'] = resource_tags

            # Add interface tags
            if interface.get('Tags'):
                metadata['eni_tags'] = interface['Tags']

            # Add security groups
            if interface.get('Groups'):
                metadata['security_groups'] = [
                    {
                        'GroupId': sg['GroupId'],
                        'GroupName': sg['GroupName']
                    } for sg in interface['Groups']
                ]

            # Store IP information
            store_ip_metadata(interface.get('PrivateIpAddress'), metadata)
            stats['processed_count'] += 1

            # Store public IP if exists
            public_ip = interface.get('Association', {}).get('PublicIp')
            if public_ip:
                store_ip_metadata(public_ip, metadata)
                stats['processed_count'] += 1

            # Store additional private IPs
            for private_ip_info in interface.get('PrivateIpAddresses', []):
                if private_ip_info.get('PrivateIpAddress') != interface.get('PrivateIpAddress'):
                    store_ip_metadata(private_ip_info['PrivateIpAddress'], metadata)
                    stats['processed_count'] += 1
                    if 'Association' in private_ip_info and 'PublicIp' in private_ip_info['Association']:
                        store_ip_metadata(private_ip_info['Association']['PublicIp'], metadata)
                        stats['processed_count'] += 1

        except Exception as e:
            print(f"Error processing interface {eni_id}: {str(e)}")
            stats['error_count'] += 1

    def store_ip_metadata(ip_address, metadata):
        """Store IP metadata in DynamoDB"""
        if not ip_address:
            return
            
        try:
            item = {
                'ip_address': ip_address,
                **metadata,
                'last_updated': current_time.isoformat()
            }
            table.put_item(Item=item)
        except ClientError as e:
            print(f"Error storing metadata for IP {ip_address}: {e}")
            stats['error_count'] += 1
            raise

    try:
        # Clean old entries
        clean_old_entries()

        # Process all network interfaces
        paginator = ec2_client.get_paginator('describe_network_interfaces')
        for page in paginator.paginate():
            for interface in page.get('NetworkInterfaces', []):
                process_interface(interface)

        # Publish metrics
        publish_metrics(stats)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing complete',
                **stats
            })
        }

    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f"Error: {str(e)}",
                **stats
            })
        }
