from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('consultant_onboarding', '0005_alter_authconsultantdocument_document_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersession',
            name='mcq_answers',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

