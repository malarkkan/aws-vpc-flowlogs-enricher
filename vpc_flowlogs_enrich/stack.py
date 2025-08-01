import json
from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_kinesisfirehose as firehose,
    aws_glue as glue,
    aws_s3 as s3,
    aws_ec2 as ec2,
    aws_logs as logs,
    RemovalPolicy,
    Fn,
    custom_resources,
    Duration,
    CfnOutput,
    aws_kms as kms
)
from constructs import Construct
import datetime

class VPCFlowLogsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Get the account ID
        account_id = Stack.of(self).account
        # VPC Configuration
        vpc = ec2.Vpc(
            self, 'CustomVPC',
            max_azs=2,
            cidr='10.0.0.0/16',
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name='Public',
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name='Private',
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name='Isolated',
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24
                )
            ],
            nat_gateways=1,
            enable_dns_hostnames=True,
            enable_dns_support=True,
        )

        # Add Gateway Endpoints
        vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3
        )

        vpc.add_gateway_endpoint(
            "DynamoDBEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB
        )

        vpc.add_interface_endpoint(
            "FirehoseDataStreamEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.KINESIS_FIREHOSE,
            subnets=ec2.SubnetSelection(
                subnets=vpc.isolated_subnets
            )
        )

        # Extract configuration values
        lambda_timeout = config.get('lambda_timeout', 300)
        lambda_memory = config.get('lambda_memory', 256)
        environment = config.get('environment', 'dev')
        stream_name = config.get('vpc_flow_logs_stream_name', 'vpc-flow-logs-stream')

        # Create KMS key for encryption
        kms_key = kms.Key(
            self, 'VpcFlowLogsKey',
            enable_key_rotation=True,
            description='KMS key for VPC Flow Logs encryption',
            policy=iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        sid='Enable IAM User Permissions',
                        actions=['kms:*'],
                        resources=['*'],
                        principals=[iam.AccountRootPrincipal()]
                    ),
                    iam.PolicyStatement(
                        sid='Allow CloudWatch Logs',
                        actions=[
                            'kms:Encrypt*',
                            'kms:Decrypt*',
                            'kms:ReEncrypt*',
                            'kms:GenerateDataKey*',
                            'kms:Describe*'
                        ],
                        resources=['*'],
                        principals=[iam.ServicePrincipal(f'logs.{self.region}.amazonaws.com')]
                    ),
                    iam.PolicyStatement(
                        sid='Allow Firehose',
                        actions=[
                            'kms:Decrypt',
                            'kms:GenerateDataKey'
                        ],
                        resources=['*'],
                        principals=[iam.ServicePrincipal('firehose.amazonaws.com')]
                    ),
                    iam.PolicyStatement(
                        sid='Allow Lambda',
                        actions=[
                            'kms:Decrypt',
                            'kms:GenerateDataKey'
                        ],
                        resources=['*'],
                        principals=[iam.ServicePrincipal('lambda.amazonaws.com')]
                    )
                ]
            )
        )


            # Create S3 bucket for VPC Flow Logs
        vpc_flow_logs_bucket = s3.Bucket(
                self, 'VpcFlowLogsBucket',
                removal_policy=RemovalPolicy.RETAIN,
                encryption=s3.BucketEncryption.KMS,
                encryption_key=kms_key,
                enforce_ssl=True,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                versioned=True,
                lifecycle_rules=[
                    s3.LifecycleRule(
                        transitions=[
                            s3.Transition(
                                storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                                transition_after=Duration.days(30)
                            ),
                            s3.Transition(
                                storage_class=s3.StorageClass.GLACIER,
                                transition_after=Duration.days(90)
                            )
                        ],
                        expiration=Duration.days(365)
                    )
                ]
            )

        # Create DynamoDB table for ENI-IP metadata
        eni_ip_metadata_table = dynamodb.Table(
            self, 'EniIpMetadataTable',
            partition_key=dynamodb.Attribute(
                name='ip_address',
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name='timestamp',
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            point_in_time_recovery=True,
            time_to_live_attribute='ttl'
        )

        # Create Lambda role
        lambda_role = iam.Role(
            self, 'LambdaExecutionRole',
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal('lambda.amazonaws.com'),
                iam.ServicePrincipal('firehose.amazonaws.com')
            )
        )

        # Add Lambda policies
        lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaBasicExecutionRole')
        )

        # Add VPC execution permissions
        lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaVPCAccessExecutionRole')
        )

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    'ec2:CreateTags',
                    'ec2:DescribeTags',
                    'ec2:DescribeNetworkInterfaces',
                    'ec2:DescribeInstances',
                    'ec2:CreateNetworkInterface',
                    'ec2:DeleteNetworkInterface',
                    'ec2:DescribeNetworkInterfaces',
                    'ec2:AttachNetworkInterface'
                ],
                resources=['*']
            )
        )
        # Add DynamoDB permissions to Lambda role
        eni_ip_metadata_table.grant_read_write_data(lambda_role)

        # Create Lambda function
        vpc_logs_enrichment = _lambda.Function(
            self, 'VpcLogsEnrichmentFunction',
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler='lambda_function.lambda_handler',
            code=_lambda.Code.from_asset('lambda_function'),
            role=lambda_role,
            timeout=Duration.seconds(lambda_timeout),
            memory_size=lambda_memory,
            environment={
                'DYNAMODB_TABLE_NAME': eni_ip_metadata_table.table_name,
                'ENVIRONMENT': environment,
                'KMS_KEY_ARN': kms_key.key_arn
            },
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        )

        ip_metadata_dynamodb_upd = _lambda.Function(
            self, 'IpMetadataDynamoTableUpdateFunction',
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler='lambda_function.lambda_handler',
            code=_lambda.Code.from_asset('ip_metadata_table_upd'),
            role=lambda_role,
            timeout=Duration.seconds(lambda_timeout),
            memory_size=lambda_memory,
            environment={
                'DYNAMODB_TABLE_NAME': eni_ip_metadata_table.table_name,
                'ENVIRONMENT': environment,
                'KMS_KEY_ARN': kms_key.key_arn
            },
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        )
        
        # Create Firehose role
        firehose_role = iam.Role(
            self, 'FirehoseRole',
            assumed_by=iam.ServicePrincipal('firehose.amazonaws.com')
        )

        # Add Firehose policies
        firehose_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    's3:AbortMultipartUpload',
                    's3:GetBucketLocation',
                    's3:GetObject',
                    's3:ListBucket',
                    's3:ListBucketMultipartUploads',
                    's3:PutObject'
                ],
                resources=[
                    vpc_flow_logs_bucket.bucket_arn,
                    f"{vpc_flow_logs_bucket.bucket_arn}/*"
                ]
            )
        )

        firehose_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    'kms:Decrypt',
                    'kms:GenerateDataKey'
                ],
                resources=[kms_key.key_arn]
            )
        )

        # Add Firehose policies
        firehose_role.add_to_policy(
        iam.PolicyStatement(
        actions=[
            # Glue permissions
            'glue:GetTableVersions',
            'glue:GetTable',
            'glue:GetDatabase',
            'glue:GetPartition',
            'glue:BatchCreatePartition',
            'glue:CreatePartition',
            'glue:UpdatePartition',
            # KMS permissions
            'kms:Decrypt',
            'kms:GenerateDataKey',
            # Lambda permissions
            'lambda:InvokeFunction',
            'lambda:GetFunctionConfiguration',
            # CloudWatch Logs permissions
            'logs:PutLogEvents',
            'logs:CreateLogStream',
            'logs:CreateLogGroup'
            ],
            resources=[
            
            # Glue resources
            f"arn:aws:glue:{self.region}:{self.account}:catalog",
            f"arn:aws:glue:{self.region}:{self.account}:database/*",
            f"arn:aws:glue:{self.region}:{self.account}:table/*",  # Added comma here
            # Lambda resources
            vpc_logs_enrichment.function_arn,
            # KMS resources
            kms_key.key_arn,
            # CloudWatch Logs resources
            f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/kinesisfirehose/*"
            ]
          )
        )
        # Grant Lambda invoke permissions to Firehose
        vpc_logs_enrichment.grant_invoke(firehose_role)

        # Create CloudWatch Log Group for Firehose
        log_group = logs.LogGroup(
            self, 'FirehoseLogGroup',
            log_group_name=f"/aws/kinesisfirehose/{stream_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
            encryption_key=kms_key
        )

        # Grant CloudWatch Logs permission to use the KMS key
        kms_key.grant_encrypt_decrypt(iam.ServicePrincipal(f'logs.{self.region}.amazonaws.com'))

        # Create Glue Database  
        glue_database = glue.CfnDatabase(
            self, 'VpcFlowLogsDatabase',
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name='vpcflowlogs',
                description='Database for VPC Flow Logs'
            )
        )
        # Load table schema from JSON file
        with open('glue/table-schema.json', 'r') as file:
            table_schema = json.load(file)

        # Convert JSON columns to Glue CfnTable.ColumnProperty format
        glue_columns = [
            glue.CfnTable.ColumnProperty(
                name=col["Name"],
                type=col["Type"]
            ) for col in table_schema["TableInput"]["StorageDescriptor"]["Columns"]
        ]

        # Convert partition keys to Glue format
        partition_keys = [
            glue.CfnTable.ColumnProperty(
                name=part["Name"],
                type=part["Type"]
            ) for part in table_schema["TableInput"]["PartitionKeys"]
        ]
    #Create Glue Table with explicit keyword arguments
        glue_table = glue.CfnTable(
            scope=self,
            id='enriched_vpcflowlogs',
            catalog_id=self.account,
            database_name=glue_database.ref,
            table_input=glue.CfnTable.TableInputProperty(
                name=table_schema["TableInput"]["Name"],
                description="Enriched VPC Flow Logs",
                table_type=table_schema["TableInput"]["TableType"],
                parameters=table_schema["TableInput"]["Parameters"],
                partition_keys=partition_keys,
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=glue_columns,
                    location=f"s3://{vpc_flow_logs_bucket.bucket_name}/AWSLogs/vpc-flow-logs/",
                    input_format=table_schema["TableInput"]["StorageDescriptor"]["InputFormat"],
                    output_format=table_schema["TableInput"]["StorageDescriptor"]["OutputFormat"],
                    compressed=table_schema["TableInput"]["StorageDescriptor"]["Compressed"],
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library=table_schema["TableInput"]["StorageDescriptor"]["SerdeInfo"]["SerializationLibrary"],
                        parameters=table_schema["TableInput"]["StorageDescriptor"]["SerdeInfo"]["Parameters"]
                    )
                )
            )
        )

        # Create Firehose Delivery Stream
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        firehose_stream = firehose.CfnDeliveryStream(
            self, 'VpcFlowLogsStream',
            delivery_stream_name=f'{stream_name}-{current_date}',
            delivery_stream_type='DirectPut',
            delivery_stream_encryption_configuration_input=firehose.CfnDeliveryStream.DeliveryStreamEncryptionConfigurationInputProperty(
                key_type='CUSTOMER_MANAGED_CMK',
                key_arn=kms_key.key_arn
            ),
            extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=vpc_flow_logs_bucket.bucket_arn,
                role_arn=firehose_role.role_arn,
                buffering_hints={
                    'intervalInSeconds': 300,
                    'sizeInMBs': 64
                },
                compression_format='UNCOMPRESSED',
                prefix='AWSLogs/vpc-flow-logs/year=!{timestamp:YYYY}/month=!{timestamp:MM}/day=!{timestamp:DD}/hour=!{timestamp:HH}/',
                error_output_prefix='errors/year=!{timestamp:YYYY}/month=!{timestamp:MM}/day=!{timestamp:DD}/hour=!{timestamp:HH}/!{firehose:error-output-type}/',
                cloud_watch_logging_options={
                    'enabled': True,
                    'logGroupName': log_group.log_group_name,
                    'logStreamName': 'S3Delivery'
                },
                processing_configuration=firehose.CfnDeliveryStream.ProcessingConfigurationProperty(
                    enabled=True,
                    processors=[
                        firehose.CfnDeliveryStream.ProcessorProperty(
                            type='AppendDelimiterToRecord'
                        ),
                        firehose.CfnDeliveryStream.ProcessorProperty(
                            type='Lambda',
                            parameters=[
                                firehose.CfnDeliveryStream.ProcessorParameterProperty(
                                    parameter_name='LambdaArn',
                                    parameter_value=vpc_logs_enrichment.function_arn
                                ),
                                firehose.CfnDeliveryStream.ProcessorParameterProperty(
                                    parameter_name='BufferSizeInMBs',
                                    parameter_value='3'
                                ),
                                firehose.CfnDeliveryStream.ProcessorParameterProperty(
                                    parameter_name='BufferIntervalInSeconds',
                                    parameter_value='60'
                                )
                            ]
                        )
                    ]
                ),
                data_format_conversion_configuration=firehose.CfnDeliveryStream.DataFormatConversionConfigurationProperty(
                    enabled=True,
                    input_format_configuration=firehose.CfnDeliveryStream.InputFormatConfigurationProperty(
                        deserializer=firehose.CfnDeliveryStream.DeserializerProperty(
                            open_x_json_ser_de=firehose.CfnDeliveryStream.OpenXJsonSerDeProperty(
                                case_insensitive=True,
                                convert_dots_in_json_keys_to_underscores=False
                            )
                        )
                    ),
                    output_format_configuration=firehose.CfnDeliveryStream.OutputFormatConfigurationProperty(
                        serializer=firehose.CfnDeliveryStream.SerializerProperty(
                            parquet_ser_de=firehose.CfnDeliveryStream.ParquetSerDeProperty(
                                compression='SNAPPY',
                                enable_dictionary_compression=True
                            )
                        )
                    ),
                    schema_configuration=firehose.CfnDeliveryStream.SchemaConfigurationProperty(
                        database_name='vpcflowlogs',
                        role_arn=firehose_role.role_arn,
                        table_name='enriched_vpcflowlogs',
                        version_id='LATEST'
                    )
                )
                )
            )


        # Role for VPC Flow Logs delivery to Firehose
        delivery_role = iam.Role(
            self, 'VPCFlowLogsDeliveryRole',
            assumed_by=iam.ServicePrincipal('delivery.logs.amazonaws.com'),
            inline_policies={
                'cross-account-trust': iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                'firehose:PutRecord',
                                'firehose:PutRecordBatch'
                            ],
                            resources=[firehose_stream.attr_arn]
                        )
                    ]
                )
            }
        )

        # Add cross-account trust relationship
        delivery_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountPrincipal(account_id)],
                actions=["sts:AssumeRole"]
            )
        )
        iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    'firehose:PutRecord',
                    'firehose:PutRecordBatch'
                ],
                resources=[firehose_stream.attr_arn]
            )


        # Create VPC Flow Logs service role without cross-account settings
        vpc_flow_logs_role = iam.Role(
            self, 'VPCFlowLogsRole',
            assumed_by=iam.ServicePrincipal('vpc-flow-logs.amazonaws.com'),
            description='Role for VPC Flow Logs to publish to Kinesis Firehose'
        )

        # Add permissions to the role
        vpc_flow_logs_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    'firehose:PutRecord',
                    'firehose:PutRecordBatch'
                ],
                resources=[firehose_stream.attr_arn]
            )
        )
        vpc_flow_logs_role.add_to_policy(
        	iam.PolicyStatement(
                actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams"
                ],
            	resources=["*"]  # Restrict to specific CloudWatch log group if needed
        	)
        )
        # Add cross-account trust relationship
        vpc_flow_logs_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountPrincipal(account_id)],
                actions=["sts:AssumeRole"]
            )
        )


        # # Create flow logs for the VPC
        # flow_log = ec2.CfnFlowLog(
        # self, 'VpcFlowLog',
        # resource_id=vpc.vpc_id,
        # resource_type='VPC',
        # traffic_type='ALL',
        # log_destination_type='kinesis-data-firehose',
        # log_destination=firehose_stream.attr_arn,
        # deliver_logs_permission_arn=delivery_role.role_arn,  # Only this role is needed for same-account delivery
        # max_aggregation_interval=60,
        # log_format='${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} ${action} ${log-status} ${vpc-id} ${subnet-id} ${instance-id} ${type} ${az-id} ${pkt-srcaddr} ${pkt-dstaddr} ${region} ${flow-direction} ${traffic-path}'
        # )

        # Get all VPCs in the region using a Custom Resource
        # get_vpcs = custom_resources.AwsCustomResource(
        #     self, 'GetVPCs',
        #     on_create=custom_resources.AwsSdkCall(
        #         service='EC2',
        #         action='describeVpcs',
        #         physical_resource_id=custom_resources.PhysicalResourceId.of('get-vpcs'),
        #         region=self.region
        #     ),
        #     policy=custom_resources.AwsCustomResourcePolicy.from_statements([
        #     iam.PolicyStatement(
        #         effect=iam.Effect.ALLOW,
        #         actions=['ec2:DescribeVpcs'],
        #         resources=['*']
        #     ),
        #     # Add additional statements if needed
        #     iam.PolicyStatement(
        #         effect=iam.Effect.ALLOW,
        #         actions=['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
        #         resources=['arn:aws:logs:*:*:*']  # For CloudWatch Logs
        #     )
        # ])

        # )

        # Custom VPC flowlogs format
        custom_format = (
            "${account-id} ${action} ${az-id} ${bytes} ${dstaddr} ${dstport} "
            "${end} ${flow-direction} ${instance-id} ${interface-id} ${log-status} "
            "${packets} ${pkt-dst-aws-service} ${pkt-dstaddr} ${pkt-src-aws-service} "
            "${pkt-srcaddr} ${protocol} ${region} ${reject-reason} ${srcaddr} "
            "${srcport} ${start} ${sublocation-id} ${sublocation-type} ${subnet-id} "
            "${tcp-flags} ${traffic-path} ${type} ${version} ${vpc-id}"
        )
        
        # Create flow logs for each VPC dynamically        
        flow_log = ec2.CfnFlowLog(
                self,
                "VpcFlowLog-enriched",
                log_destination_type='kinesis-data-firehose',
                resource_id=vpc.vpc_id,
                resource_type='VPC',
                traffic_type='ALL',
                log_destination=firehose_stream.attr_arn,
                max_aggregation_interval=600,
                log_format=custom_format
        )

        # Add dependency to ensure role is created before flow logs
        flow_log.node.add_dependency(firehose_stream)

        # Add dependencies to ensure proper creation order
        firehose_stream.add_depends_on(glue_table)
        glue_table.add_depends_on(glue_database)


        # Stack Outputs
        CfnOutput(
            self, 'VpcId',
            value=vpc.vpc_id,
            description='ID of the created VPC'
        )

        CfnOutput(
            self, 'PublicSubnets',
            value=','.join([subnet.subnet_id for subnet in vpc.public_subnets]),
            description='IDs of public subnets'
        )

        CfnOutput(
            self, 'PrivateSubnets',
            value=','.join([subnet.subnet_id for subnet in vpc.private_subnets]),
            description='IDs of private subnets'
        )

        CfnOutput(
            self, 'IsolatedSubnets',
            value=','.join([subnet.subnet_id for subnet in vpc.isolated_subnets]),
            description='IDs of isolated subnets'
        )

        CfnOutput(
            self, 'DynamoDBTableName',
            value=eni_ip_metadata_table.table_name,
            description='DynamoDB table name for ENI-IP metadata'
        )

        CfnOutput(
            self, 'S3BucketName',
            value=vpc_flow_logs_bucket.bucket_name,
            description='S3 bucket name for VPC Flow Logs'
        )

        CfnOutput(
            self, 'FirehoseStreamName',
            value=firehose_stream.ref,
            description='Kinesis Firehose Delivery Stream name'
        )

        CfnOutput(
            self, 'LambdaFunctionName',
            value=vpc_logs_enrichment.function_name,
            description='Lambda function name for VPC Flow Logs enrichment'
        )

        CfnOutput(
            self, 'KmsKeyArn',
            value=kms_key.key_arn,
            description='KMS Key ARN for encryption'
        )
