from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('consultant_onboarding', '0007_consultantapplication_is_phone_verified'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersession',
            name='expertise_seeded',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='usersession',
            name='selected_test_details',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
