from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("consultant_onboarding", "0006_usersession_mcq_answers"),
    ]

    operations = [
        migrations.AddField(
            model_name="consultantapplication",
            name="is_phone_verified",
            field=models.BooleanField(default=False),
        ),
    ]

