import os
import boto3
from django.conf import settings
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

def sync_media_to_s3():
    s3 = boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME
    )
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    media_root = settings.MEDIA_ROOT

    print(f"Syncing {media_root} to S3 bucket {bucket}...")

    for root, dirs, files in os.walk(media_root):
        for file in files:
            local_path = os.path.join(root, file)
            relative_path = os.path.relpath(local_path, media_root)
            
            # S3 keys should not start with /
            s3_key = relative_path.replace("\\", "/")
            
            print(f"Uploading {s3_key}...")
            try:
                s3.upload_file(local_path, bucket, s3_key)
                print(f"Successfully uploaded {s3_key}")
            except Exception as e:
                print(f"Failed to upload {s3_key}: {e}")

if __name__ == "__main__":
    sync_media_to_s3()
