import boto3
import time
import cfnresponse

def handler(event, context):
    try:
        if event['RequestType'] in ['Create', 'Update']:
            firehose = boto3.client('firehose')
            stream_name = event['ResourceProperties']['DeliveryStreamName']
            max_attempts = 20  # Increased to  minutes (45 seconds * 20)
            for attempt in range(max_attempts):
                response = firehose.describe_delivery_stream(
                    DeliveryStreamName=stream_name
                )
                status = response['DeliveryStreamDescription']['DeliveryStreamStatus']
                if status == 'ACTIVE':
                    cfnresponse.send(event, context, cfnresponse.SUCCESS, {'Status': status})
                    return
                
                time.sleep(30)
            cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': 'Timeout waiting for Firehose to become ACTIVE'})

        else:
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})

    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)})
