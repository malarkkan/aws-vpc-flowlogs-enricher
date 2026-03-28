# VPC Flow Logs Enrichment Solution - Technical Overview

## Solution Architecture

This solution provides a comprehensive system for enriching AWS VPC Flow Logs with metadata from various AWS resources, enabling enhanced network traffic analysis and security monitoring.

### High-Level Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   VPC Resources │    │   EventBridge    │    │   DynamoDB      │
│   (EC2, RDS,    │    │   (10 min cron)  │    │   IP Metadata   │
│   NAT, ALB)     │    └──────────────────┘    │   Cache         │
└─────────────────┘             │              └─────────────────┘
         │                      │                       ▲
         │                      ▼                       │
         │            ┌──────────────────┐              │
         │            │  IP Metadata     │──────────────┘
         │            │  Update Lambda   │
         │            └──────────────────┘
         │
         ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   VPC Flow      │    │   Kinesis        │    │   Enrichment    │
│   Logs          │───▶│   Firehose       │───▶│   Lambda        │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                       │
                                │                       ▼
                                │              ┌─────────────────┐
                                │              │   DynamoDB      │
                                │              │   Lookup        │
                                │              └─────────────────┘
                                ▼
                       ┌─────────────────┐
                       │   S3 Bucket     │
                       │   (Parquet)     │
                       │   + Glue Table  │
                       └─────────────────┘
```

## Core Components

### 1. IP Metadata Collection System
**File**: `ip_metadata_table_upd/lambda_function.py`

**Purpose**: Periodically scans AWS resources and maintains an IP-to-metadata mapping in DynamoDB.

**Key Features**:
- **Resource Coverage**: EC2 instances, NAT gateways, load balancers, RDS instances, transit gateways
- **Automated Updates**: EventBridge triggers every 10 minutes
- **Data Freshness**: TTL-based cleanup (90 days)
- **Performance**: Batch processing with error isolation
- **Monitoring**: CloudWatch metrics for processed/updated/failed items

**Supported Resources**:
```python
# Resource types handled
- EC2 Instances (private/public IPs)
- NAT Gateways (public IPs)
- Application/Network Load Balancers (all AZ IPs)
- RDS Instances (DNS endpoints)
- Transit Gateways (attachment metadata)
- VPC Endpoints
- Network Interfaces (ENIs)
```

### 2. Flow Log Enrichment Engine
**Files**: 
- `lambda_function/lambda_function.py` (main version)
- `lambda_function/lambda__fn_0526.py` (with security groups)
- `lambda_function/lambda_function_parq.py` (parquet optimized)

**Purpose**: Real-time enrichment of VPC Flow Logs with metadata from DynamoDB cache.

**Enrichment Process**:
1. **Parse Flow Log**: Extract 29 standard VPC Flow Log fields
2. **Source IP Lookup**: Query DynamoDB for source IP metadata
3. **Destination IP Lookup**: Query DynamoDB for destination IP metadata
4. **Tag Enrichment**: Add resource tags with prefixes (`src-tag-`, `dst-tag-`)
5. **Metadata Addition**: Include VPC, subnet, AZ, instance, and ENI information
6. **Schema Validation**: Convert data types and handle null values

**Output Schema**:
```json
{
  "account-id": "123456789012",
  "action": "ACCEPT",
  "srcaddr": "10.0.1.100",
  "dstaddr": "10.0.2.200",
  "src-tag-Environment": "production",
  "src-tag-Application": "web-server",
  "dst-tag-Environment": "production", 
  "dst-tag-Database": "mysql",
  "src-vpc-id": "vpc-12345",
  "dst-vpc-id": "vpc-67890",
  "src-instance-id": "i-1234567890abcdef0",
  "dst-instance-id": "i-0987654321fedcba0"
}
```

### 3. Infrastructure as Code (CDK)
**File**: `vpc_flowlogs_enrich/stack.py`

**Key Infrastructure Components**:

#### Networking
- **Custom VPC**: 3-tier architecture (public, private, isolated subnets)
- **VPC Endpoints**: S3, DynamoDB, Firehose, SSM for private connectivity
- **NAT Gateway**: Outbound internet access for private subnets

#### Storage & Processing
- **S3 Bucket**: Encrypted storage with lifecycle policies (IA → Glacier → Delete)
- **DynamoDB Table**: IP metadata cache with TTL and point-in-time recovery
- **Kinesis Firehose**: Stream processing with Lambda transformation
- **AWS Glue**: Data catalog and automated schema discovery

#### Security
- **KMS Encryption**: Customer-managed keys for all data at rest
- **IAM Roles**: Least-privilege access for all services
- **VPC Security**: Private subnets, security groups, NACLs

#### Monitoring
- **CloudWatch**: Metrics, logs, and alarms
- **EventBridge**: Scheduled metadata updates

## Data Flow

### 1. Metadata Collection Flow
```
EventBridge (10min) → IP Metadata Lambda → AWS APIs → DynamoDB
                                      ↓
                               CloudWatch Metrics
```

### 2. Flow Log Processing Flow
```
VPC Flow Logs → Firehose → Enrichment Lambda → DynamoDB Lookup
                    ↓              ↓
               S3 (Parquet)   CloudWatch Logs
                    ↓
              Glue Catalog
```

## Key Features

### Performance Optimizations
- **DynamoDB Caching**: Sub-millisecond IP lookups vs. EC2 API calls
- **Batch Processing**: Firehose buffers records for efficient processing
- **Parquet Format**: Columnar storage for analytics workloads
- **Compression**: SNAPPY compression reduces storage costs

### Reliability & Monitoring
- **Error Isolation**: Individual record failures don't break batches
- **Retry Logic**: Built-in Firehose retry mechanisms
- **Dead Letter Queues**: Failed records routed to error prefixes
- **Comprehensive Logging**: CloudWatch integration for debugging

### Security
- **Encryption**: KMS encryption for data in transit and at rest
- **Network Isolation**: Private subnets with VPC endpoints
- **IAM**: Fine-grained permissions with service-specific roles
- **Compliance**: Supports audit trails and data governance

### Scalability
- **Auto-scaling**: Lambda and Firehose scale automatically
- **Partitioning**: Time-based S3 partitioning for query performance
- **Resource Limits**: Configurable memory, timeout, and buffer settings

## Configuration Options

### Environment-Specific Settings
```python
# Configurable parameters
lambda_timeout: 300          # Lambda execution timeout
lambda_memory: 256          # Lambda memory allocation
firehose_buffer_size: 128   # MB buffer before S3 write
firehose_buffer_interval: 180 # Seconds before forced write
metadata_update_frequency: 10 # Minutes between metadata updates
```

### Resource Tagging Strategy
- **Source Tags**: `src-tag-{TagKey}` format
- **Destination Tags**: `dst-tag-{TagKey}` format
- **Metadata Fields**: `src-{field}` and `dst-{field}` format

## Use Cases

### Security Monitoring
- **Threat Detection**: Identify unusual traffic patterns
- **Compliance**: Track data flows between environments
- **Incident Response**: Correlate network events with resource metadata

### Cost Optimization
- **Traffic Analysis**: Identify high-bandwidth resources
- **Resource Utilization**: Track inter-service communication
- **Data Transfer Costs**: Monitor cross-AZ and cross-region traffic

### Network Operations
- **Troubleshooting**: Correlate network issues with resource tags
- **Capacity Planning**: Analyze traffic growth patterns
- **Performance Monitoring**: Track application-level network metrics

## Deployment Requirements

### Prerequisites
- AWS CDK v2.178.1+
- Python 3.12+
- AWS CLI configured
- Appropriate IAM permissions

### Resource Requirements
- **Lambda**: 256MB memory, 300s timeout (configurable)
- **DynamoDB**: Pay-per-request billing mode
- **S3**: Standard storage with lifecycle transitions
- **Firehose**: 128MB/180s buffering (configurable)

### Estimated Costs
- **Lambda**: ~$0.20 per million requests
- **DynamoDB**: ~$1.25 per million read/write requests
- **S3**: ~$0.023 per GB (Standard), ~$0.0125 per GB (IA)
- **Firehose**: ~$0.029 per GB processed

## Monitoring & Alerting

### Key Metrics
- **Processing Success Rate**: Percentage of successfully enriched records
- **Metadata Cache Hit Rate**: DynamoDB lookup success rate
- **Lambda Duration**: Processing time per batch
- **Error Rates**: Failed enrichment attempts
- **Storage Growth**: S3 bucket size trends

### Recommended Alarms
- Lambda error rate > 5%
- DynamoDB throttling events
- Firehose delivery failures
- S3 PUT errors
- KMS key usage anomalies

This solution provides a production-ready, scalable system for VPC Flow Log enrichment with comprehensive monitoring, security, and cost optimization features.