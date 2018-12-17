
import boto3
from botocore.exceptions import ClientError

import json
import os
import time
import datetime
from dateutil import tz

from lib.account import *
from lib.common import *

import logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('boto3').setLevel(logging.WARNING)

DOMAIN_RESOURCE_PATH = "route53/domains"
ZONE_RESOURCE_PATH = "route53/hostedzone"

def lambda_handler(event, context):
    logger.debug("Received event: " + json.dumps(event, sort_keys=True))
    message = json.loads(event['Records'][0]['Sns']['Message'])
    logger.info("Received message: " + json.dumps(message, sort_keys=True))

    try:
        target_account = AWSAccount(message['account_id'])
        discover_domains(target_account)
        discover_zones(target_account)

    except AssumeRoleError as e:
        logger.error("Unable to assume role into account {}({})".format(target_account.account_name, target_account.account_id))
        return()
    except ClientError as e:
        logger.error("AWS Error getting info for {}: {}".format(target_account.account_name, e))
        return()
    except Exception as e:
        logger.error("{}\nMessage: {}\nContext: {}".format(e, message, vars(context)))
        raise

def discover_domains(account):
    '''
        Gathers all the Route53Domains registered domains
    '''
    domains = []

    # Not all Public IPs are attached to instances. So we use ec2 describe_network_interfaces()
    # All results are saved to S3. Public IPs and metadata go to DDB (based on the the presense of PublicIp in the Association)
    route53_client = account.get_client('route53domains', region="us-east-1") # Route53 Domains is only available in us-east-1
    response = route53_client.list_domains()
    while 'NextPageMarker' in response:  # Gotta Catch 'em all!
        domains += response['Domains']
        response = route53_client.list_domains(Marker=response['NextPageMarker'])
    domains += response['Domains']

    for d in domains:

        resource_item = {}
        resource_item['awsAccountId']                   = account.account_id
        resource_item['awsAccountName']                 = account.account_name
        resource_item['resourceType']                   = "AWS::Route53::Domain"
        resource_item['source']                         = "Antiope"

        # Get the real juicy details
        domain = route53_client.get_domain_detail(DomainName=d['DomainName'])
        del domain['ResponseMetadata'] # Remove response metadata. Not needed

        resource_item['configurationItemCaptureTime']   = str(datetime.datetime.now(tz.gettz('US/Eastern')))
        resource_item['configuration']                  = domain
        resource_item['supplementaryConfiguration']     = {}
        resource_item['resourceId']                     = domain['DomainName']
        resource_item['resourceName']                   = domain['DomainName']
        resource_item['resourceCreationTime']           = domain['CreationDate']
        resource_item['errors']                         = {}

        # And the one bit of info that only list domains had.
        resource_item['supplementaryConfiguration']['TransferLock']     = d['TransferLock']

        # Not sure why Route53 product team makes me do a special call for tags.
        response = route53_client.list_tags_for_domain(DomainName=d['DomainName'])
        if 'TagList' in response:
            resource_item['Tags'] = response['TagList']

        # Need to make sure the resource name is unique and service identifiable.
        save_resource_to_s3(DOMAIN_RESOURCE_PATH, resource_item['resourceId'], resource_item)

def discover_zones(account):
    '''
        Queries AWS to determine what Route53 Zones are hosted in an AWS Account
    '''
    zones = []

    # Not all Public IPs are attached to instances. So we use ec2 describe_network_interfaces()
    # All results are saved to S3. Public IPs and metadata go to DDB (based on the the presense of PublicIp in the Association)
    route53_client = account.get_client('route53')
    response = route53_client.list_hosted_zones()
    while 'IsTruncated' in response and response['IsTruncated'] is True:  # Gotta Catch 'em all!
        zones += response['HostedZones']
        response = route53_client.list_hosted_zones(Marker=response['NextMarker'])
    zones += response['HostedZones']

    for zone in zones:

        resource_item = {}
        resource_item['awsAccountId']                   = account.account_id
        resource_item['awsAccountName']                 = account.account_name
        resource_item['resourceType']                   = "AWS::Route53::HostedZone"
        resource_item['source']                         = "Antiope"

        resource_item['configurationItemCaptureTime']   = str(datetime.datetime.now(tz.gettz('US/Eastern')))
        resource_item['configuration']                  = zone
        # resource_item['tags']                           = FIXME
        resource_item['supplementaryConfiguration']     = {}
        # Need to make sure the resource name is unique and service identifiable.
        # Zone Ids look like "/hostedzone/Z2UFNORDFDSFTZ"
        resource_item['resourceId']                     = zone['Id'].split("/")[2]
        resource_item['resourceName']                   = zone['Name']
        resource_item['errors']                         = {}

        # # Not sure why Route53 product team makes me do a special call for tags.
        # response = route53_client.list_tags_for_resource(
        #     ResourceType='hostedzone',
        #     ResourceId=zone['Id']
        # )
        # if 'ResourceTagSet' in response and 'Tags' in response['ResourceTagSet']:
        #     zone['Tags'] = response['ResourceTagSet']['Tags']

        # This also looks interesting from a data-leakage perspective
        response = route53_client.list_vpc_association_authorizations(HostedZoneId=zone['Id'])
        if 'VPCs' in response:
            resource_item['supplementaryConfiguration']['AuthorizedVPCs'] = response['VPCs']

        save_resource_to_s3(ZONE_RESOURCE_PATH, resource_item['resourceId'], resource_item)



def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError ("Type %s not serializable" % type(obj))