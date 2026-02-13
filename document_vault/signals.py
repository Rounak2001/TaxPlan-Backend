from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import Document, SharedReport, LegalNotice
import os

@receiver(post_delete, sender=Document)
def auto_delete_file_on_delete_document(sender, instance, **kwargs):
    """
    Deletes file from storage when corresponding Document object is deleted.
    """
    if instance.file:
        instance.file.delete(save=False)
        print(f"Deleted storage file (Document): {instance.title}")

@receiver(post_delete, sender=SharedReport)
def auto_delete_file_on_delete_report(sender, instance, **kwargs):
    """
    Deletes file from storage when corresponding SharedReport object is deleted.
    """
    if instance.file:
        instance.file.delete(save=False)
        print(f"Deleted storage file (Report): {instance.title}")

@receiver(post_delete, sender=LegalNotice)
def auto_delete_file_on_delete_notice(sender, instance, **kwargs):
    """
    Deletes file from storage when corresponding LegalNotice object is deleted.
    """
    if instance.file:
        instance.file.delete(save=False)
        print(f"Deleted storage file (Notice): {instance.title}")
