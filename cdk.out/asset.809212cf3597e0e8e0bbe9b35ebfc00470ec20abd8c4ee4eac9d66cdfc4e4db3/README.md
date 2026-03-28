# IP Address Metadata Enrichment Lambda Function

This Lambda function enriches IP address metadata by processing network interfaces and storing the information in a DynamoDB table. It handles various AWS resources including EC2 instances, NAT Gateways, Transit Gateways, Load Balancers, VPC Endpoints, and RDS instances.

## Features

- Processes all network interfaces in the AWS account
- Enriches metadata with resource tags, owner information, and status
- Handles public and private IP addresses
- Cleans old entries using TTL
- Publishes metrics to CloudWatch

```mermaid
graph TD
    A[EventBridge Scheduler] -->|Triggers| B[Lambda: eni-ip-address-metadata]
    
    B -->|1. Fetch| C[EC2 Network Interfaces]
    B -->|2. Fetch| D[Resource Tags & Metadata]
    
    subgraph "Resource Types"
        D --> E1[EC2 Instance]
        D --> E2[NAT Gateway]
        D --> E3[Transit Gateway]
        D --> E4[Load Balancer]
        D --> E5[VPC Endpoint]
        D --> E6[RDS Instance]
    end
    
    C --> F[Process & Enrich Metadata]
    E1 --> F
    E2 --> F
    E3 --> F
    E4 --> F
    E5 --> F
    E6 --> F
    
    F -->|Store| G[DynamoDB Table]
    
    H[CloudWatch] -->|Collect| I[Metrics & Logs]
    B -->|Report| I
```

## Prerequisites

- AWS Lambda environment
- Proper IAM permissions for EC2, ELB, RDS, DynamoDB, and CloudWatch
- DynamoDB table for storing metadata

## Environment Variables

- `DYNAMODB_TABLE_NAME`: Name of the DynamoDB table to store metadata

## Main Components

1. `lambda_handler`: Main entry point for the Lambda

```mermaid
graph TD
    A[Start] --> B[Clean Old Entries]
    B --> C[Fetch Network Interfaces]
    C --> D{For Each Interface}
    D --> E[Determine Resource Type]
    E --> F[Fetch Resource Tags]
    F --> G[Process Interface]
    G --> H[Store IP Metadata]
    H --> I{More Interfaces?}
    I -->|Yes| D
    I -->|No| J[Publish Metrics]
    J --> K[End]
    
    G --> L[Handle Public IP]
    G --> M[Handle Additional Private IPs]
    
    N[Error Handling] --> D
    N --> G
    N --> H
    N --> J
```
