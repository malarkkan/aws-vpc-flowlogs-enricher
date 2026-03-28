# VPC Flow Logs Enrichment and Analysis System

This project implements a scalable system for enriching and analyzing VPC Flow Logs using AWS services and DynamoDB caching.

The VPC Flow Logs Enrichment and Analysis System is designed to process and enrich VPC Flow Logs data, providing enhanced visibility into network traffic within your AWS infrastructure. It leverages AWS services such as Lambda, DynamoDB, Kinesis Firehose, and S3 to efficiently process, enrich, and store flow log data for further analysis.

The system is built using the AWS Cloud Development Kit (CDK) and can be deployed to different environments (development and production) with environment-specific configurations. It includes features such as:

- VPC Flow Logs collection and storage in S3
- Lambda function for log enrichment
- DynamoDB caching for improved performance
- Configurable retention policies
- CloudWatch alarms for monitoring

## Repository Structure

The repository is organized as follows:

```
.
├── app.py                          # Main CDK application entry point
├── cdk.json                        # CDK configuration file
├── lambda_function/                # Lambda function code for enrichment
│   ├── lambda_function.py          # Main enrichment function
│   ├── lambda_function_parq.py     # Parquet optimized version
│   ├── lambda__fn_0526.py          # Version with security groups
│   ├── requirements.txt            # Lambda dependencies
│   └── verifyier.py               # Firehose verification function
├── ip_metadata_table_upd/          # IP metadata update Lambda
│   └── lambda_function.py          # Metadata collection function
├── functions/                      # Alternative enricher implementation
│   └── aws-vpc-flowlogs-enricher.py
├── glue/                          # Glue table schema
│   └── table-schema.json
├── vpc_flowlogs_enrich/           # CDK stack definition
│   ├── __init__.py
│   └── stack.py                   # Main infrastructure stack
├── requirements.txt               # Project dependencies
├── README.md                      # This file
└── README-solution.md             # Technical solution overview
```

Key files:
- `app.py`: The main entry point for the CDK application
- `vpc_flowlogs_enrich/stack.py`: Defines the AWS resources for the VPC Flow Logs stack
- `lambda_function/lambda_function.py`: Contains the Lambda function code for enriching VPC Flow Logs
- `ip_metadata_table_upd/lambda_function.py`: Updates DynamoDB with IP metadata from AWS resources

## Usage Instructions

### Prerequisites

- Python 3.8 or later
- AWS CDK CLI v2.178.1 or later
- AWS CLI configured with appropriate credentials
- Docker (for Lambda layer creation)

### Installation

1. Clone the repository:
   ```
   git clone <repository-url>
   cd aws-vpc-flowlogs-enricher
   ```

2. Create and activate a virtual environment:
   ```
   python -m venv .venv
   source .venv/bin/activate  # On Windows, use `.venv\Scripts\activate`
   ```

3. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Bootstrap the CDK environment (if not already done):
   ```
   cdk bootstrap aws://<account-id>/<region>
   ```

### Deployment

1. Configure the environment-specific settings in `app.py`:
   - Update the `EnvironmentConfig` class with appropriate values for your development and production environments.

2. Deploy the stack:
   ```
   cdk deploy VPCFlowLogs-dev  # For development environment
   # or
   cdk deploy VPCFlowLogs-prod  # For production environment
   ```

3. Note the outputs from the deployment, which will include the DynamoDB table name and Lambda function ARN.

### Configuration

The system can be configured by modifying the `EnvironmentConfig` class in `app.py`. Key configuration options include:

- DynamoDB table configuration and TTL settings
- VPC Flow Log source VPCs
- Lambda function memory and timeout
- Firehose buffer size and interval
- Metadata update frequency

### Testing

To run the unit tests:

```
pytest tests/
```

### Troubleshooting

1. Lambda function errors:
   - Check CloudWatch Logs for the Lambda function
   - Verify that the DynamoDB table is correctly configured in the Lambda environment variables
   - Ensure the Lambda function has the necessary permissions to access VPC resources and S3

2. VPC Flow Logs not appearing:
   - Verify that VPC Flow Logs are enabled for the specified VPCs
   - Check the S3 bucket permissions

3. DynamoDB connection issues:
   - Verify that the Lambda function has proper IAM permissions for DynamoDB access
   - Check the DynamoDB table status in the AWS console
   - Ensure the table name environment variable is correctly set

## Data Flow

### IP Metadata Collection
The system includes an automated IP metadata collection process:

1. **EventBridge Schedule**: Triggers the IP metadata update Lambda every 10 minutes
2. **Resource Discovery**: The Lambda scans AWS resources (EC2, RDS, NAT Gateways, Load Balancers, etc.)
3. **Metadata Extraction**: Collects IP addresses, tags, and resource information
4. **DynamoDB Storage**: Stores the metadata with TTL for automatic cleanup

### Flow Log Enrichment
1. VPC Flow Logs are generated and sent to Kinesis Firehose
2. Firehose triggers the enrichment Lambda function with batched records
3. The Lambda function processes each log entry:
   - It extracts relevant information from the log entry
   - It queries the DynamoDB table for existing metadata about the IP addresses
   - The log entry is enriched with the additional metadata (tags, resource info)
4. The enriched log entry is converted to Parquet format and stored in S3

```
[EventBridge] --> [IP Metadata Lambda] --> [DynamoDB Table]
                                                    ↑
[VPC Flow Logs] --> [Firehose] --> [Enrichment Lambda] --> [S3 (Parquet)]
```

## Deployment

The system is deployed using AWS CDK. The `cdk deploy` command synthesizes a CloudFormation template and deploys the stack to your AWS account.

Key components deployed:
- VPC with public and private subnets
- S3 bucket for storing VPC Flow Logs
- DynamoDB table for IP metadata caching
- Lambda function for log enrichment
- IAM roles and security groups
- CloudWatch alarms

## Infrastructure

The infrastructure is defined in the `VPCFlowLogsStack` class in `vpc_flowlogs_enrich/stack.py`. Key resources include:

- VPC:
  - Type: AWS::EC2::VPC
  - Purpose: Networking infrastructure for the system

- S3 Bucket:
  - Type: AWS::S3::Bucket
  - Purpose: Store raw and enriched VPC Flow Logs

- DynamoDB Table:
  - Type: AWS::DynamoDB::Table
  - Purpose: DynamoDB table will be used as a caching layer for network interface tagging metadata

- Lambda:
  - Type: AWS::Lambda::Function
  - Purpose: Enrich VPC Flow Logs with additional metadata

- IAM Role:
  - Type: AWS::IAM::Role
  - Purpose: Permissions for Lambda to access necessary AWS resources

- CloudWatch Alarm:
  - Type: AWS::CloudWatch::Alarm
  - Purpose: Monitor Lambda function errors


## VPC Flow Logs Enrichment Lambda Function

### Overview
This Lambda function enriches VPC Flow Logs with additional metadata by looking up source and destination IP addresses in a DynamoDB table. It adds instance metadata, resource tags, and other relevant information to the flow log records.

### Features
- Enriches VPC Flow Logs with metadata from DynamoDB
- Adds source and destination instance information
- Includes resource tags for both source and destination
- Validates and normalizes record schema
- Handles error cases gracefully
- Provides CloudWatch metrics for monitoring

### Prerequisites
- AWS Lambda environment
- DynamoDB table containing IP address metadata
- IAM permissions for:
  - DynamoDB read access
  - CloudWatch metrics
  - EC2 describe instances
  - VPC Flow Logs access

### Environment Variables
- `DYNAMODB_TABLE_NAME`: Name of the DynamoDB table containing IP metadata

### Input
The function expects Kinesis Firehose records containing VPC Flow Logs in the following format:
```json
{
    "records": [
        {
            "recordId": "string",
            "data": "base64-encoded-flow-log-data"
        }
    ]
}
```

### Functions used in Lambda

   `lambda_handler(event, context)` --> Main entry point for the Lambda function.
   `process_record(record, src_tag_prefix, dst_tag_prefix, dst_prefix)` --> Processes individual flow log records and enriches them with metadata.
   `get_instance_metadata_from_dynamodb(ip_address)` --> Retrieves metadata for an IP address from DynamoDB.
   `validate_record_schema(record_dict)` --> Validates and normalizes record fields.

#### Error Handling

    Failed records are marked as 'ProcessingFailed'
    Successful records are marked as 'Ok'
    CloudWatch metrics track successful and failed records
    Extensive error logging

#### Monitoring

CloudWatch metrics available:

    SuccessfulRecords
    FailedRecords

#### Performance Considerations

    Uses DynamoDB for fast metadata lookups
    Implements efficient error handling
    Processes records in batch
    Uses OrderedDict for consistent field ordering
