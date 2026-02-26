import boto3
from django.conf import settings
from botocore.exceptions import ClientError
from botocore.config import Config
import logging

logger = logging.getLogger(__name__)

def generate_presigned_upload_url(file_path, content_type=None, expiration=3600):
    """
    Generate a presigned URL to upload a file directly to S3.
    :param file_path: the path in S3 where the file will be saved
    :param content_type: the MIME type of the file (e.g. 'video/mp4')
    :param expiration: time in seconds the presigned URL remains valid
    :return: A dictionary containing the URL and the intended S3 path
    """
    s3_client = boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
        config=Config(signature_version='s3v4')
    )
    
    try:
        # Include ContentType in the signature if it's provided so S3 strictly expects it
        params = {
            'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
            'Key': file_path,
        }
        if content_type:
             params['ContentType'] = content_type

        response = s3_client.generate_presigned_url(
            'put_object',
            Params=params,
            ExpiresIn=expiration
        )
        return {
             'url': response,
             'path': file_path
        }
    except ClientError as e:
        logger.error(f"Error generating presigned URL: {e}")
        return None
