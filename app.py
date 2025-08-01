#!/usr/bin/env python3
from aws_cdk import App
from vpc_flowlogs_enrich import VPCFlowLogsStack

app = App()

config = {
    'lambda_timeout': 300,
    'lambda_memory': 256,
    'environment': 'dev',
    'vpc_flow_logs_stream_name': 'vpc-flow-logs-stream'
}

VPCFlowLogsStack(app, "VpcFlowLogsStack", config=config)

app.synth()

