"""
Data migration: Copy consultation_fee from core_auth.ConsultantProfile
to consultants.ConsultantServiceProfile for each consultant.
"""
from django.db import migrations


def copy_consultation_fees(apps, schema_editor):
    ConsultantProfile = apps.get_model('core_auth', 'ConsultantProfile')
    ConsultantServiceProfile = apps.get_model('consultants', 'ConsultantServiceProfile')
    
    updated = 0
    for old_profile in ConsultantProfile.objects.all():
        try:
            new_profile = ConsultantServiceProfile.objects.get(user_id=old_profile.user_id)
            if old_profile.consultation_fee and old_profile.consultation_fee != 200.00:
                new_profile.consultation_fee = old_profile.consultation_fee
                new_profile.save(update_fields=['consultation_fee'])
                updated += 1
        except ConsultantServiceProfile.DoesNotExist:
            pass
    
    print(f"  → Migrated consultation_fee for {updated} consultants")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('consultants', '0006_remove_consultantserviceprofile_email_and_more'),
        ('core_auth', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(copy_consultation_fees, noop),
    ]
