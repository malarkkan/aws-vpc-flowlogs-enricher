# VPC Flow Logs Enrichment and Analysis System

This project implements a scalable system for enriching and analyzing VPC Flow Logs using AWS services and Redis caching.

The VPC Flow Logs Enrichment and Analysis System is designed to process and enrich VPC Flow Logs data, providing enhanced visibility into network traffic within your AWS infrastructure. It leverages AWS services such as Lambda, ElastiCache Redis, and S3 to efficiently process, enrich, and store flow log data for further analysis.

The system is built using the AWS Cloud Development Kit (CDK) and can be deployed to different environments (development and production) with environment-specific configurations. It includes features such as:

- VPC Flow Logs collection and storage in S3
- Lambda function for log enrichment
- Dyanamo DB caching for improved performance
- Configurable retention policies
- CloudWatch alarms for monitoring

## Repository Structure

The repository is organized as follows:

```
.
├── vpc-flowlogs-test/
│   ├── app.py                 # Main CDK application entry point
│   ├── cdk.json               # CDK configuration file
│   ├── lambda_function/       # Lambda function code
│   │   ├── lambda_function.py
│   │   └── requirements.txt
│   ├── requirements.txt       # Project dependencies
│   ├── tests/                 # Unit tests
│   └── vpc_flowlogs_enrich/   # CDK stack definition
│       ├── __init__.py
│       └── stack.py
└── vpcflowlogs-enrich/        # Duplicate project structure (to be consolidated)
```

Key files:
- `app.py`: The main entry point for the CDK application
- `vpc_flowlogs_enrich/stack.py`: Defines the AWS resources for the VPC Flow Logs stack
- `lambda_function/lambda_function.py`: Contains the Lambda function code for enriching VPC Flow Logs

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
   cd vpc-flowlogs-test
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

3. Note the outputs from the deployment, which will include the Redis endpoint and Lambda function ARN.

### Configuration

The system can be configured by modifying the `EnvironmentConfig` class in `app.py`. Key configuration options include:

- Redis instance type and node count
- VPC Flow Log source VPCs
- Lambda function memory and timeout
- Firehose buffer size and interval
- Cache expiry time

### Testing

To run the unit tests:

```
pytest tests/
```

### Troubleshooting

1. Lambda function errors:
   - Check CloudWatch Logs for the Lambda function
   - Verify that the Redis endpoint is correctly configured in the Lambda environment variables
   - Ensure the Lambda function has the necessary permissions to access VPC resources and S3

2. VPC Flow Logs not appearing:
   - Verify that VPC Flow Logs are enabled for the specified VPCs
   - Check the S3 bucket permissions

3. Redis connection issues:
   - Verify that the security group allows inbound traffic from the Lambda function
   - Check the Redis cluster status in the ElastiCache console

## Data Flow

1. VPC Flow Logs are generated and stored in the configured S3 bucket.
2. The Lambda function is triggered by new log files in S3.
3. The Lambda function processes each log entry:
   - It extracts relevant information from the log entry.
   - It checks the Redis cache for existing metadata about the network interfaces.
   - If not in cache, it queries the AWS EC2 API for additional metadata and caches the result.
   - The log entry is enriched with the additional metadata.
4. The enriched log entry is stored back in S3 in a processed/ prefix.

```
[VPC] --> [Flow Logs] --> [S3 Bucket] --> [Lambda Function] <--> [DynamoDB table]
                                               |
                                               v
                                      [Enriched Logs in S3]
```

## Deployment

The system is deployed using AWS CDK. The `cdk deploy` command synthesizes a CloudFormation template and deploys the stack to your AWS account.

Key components deployed:
- VPC with public and private subnets
- S3 bucket for storing VPC Flow Logs
- ElastiCache Redis cluster
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
