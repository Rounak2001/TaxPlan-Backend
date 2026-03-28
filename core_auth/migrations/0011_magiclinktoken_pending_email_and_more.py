from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core_auth', '0010_merge_20260321_1448'),
    ]

    operations = [
        migrations.AddField(
            model_name='magiclinktoken',
            name='pending_email',
            field=models.EmailField(blank=True, null=True, max_length=254),
        ),
        migrations.AlterField(
            model_name='magiclinktoken',
            name='purpose',
            field=models.CharField(
                choices=[
                    ('LOGIN', 'Magic Link Login'),
                    ('PASSWORD_RESET', 'Password Reset'),
                    ('EMAIL_CHANGE', 'Email Change Verification'),
                ],
                default='LOGIN',
                max_length=20,
            ),
        ),
    ]
