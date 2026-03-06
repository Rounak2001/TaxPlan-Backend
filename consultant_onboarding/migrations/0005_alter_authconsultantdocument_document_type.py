from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('consultant_onboarding', '0004_remove_usersession_cam_violation_count_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='authconsultantdocument',
            name='document_type',
            field=models.CharField(
                choices=[
                    ('Qualification', 'Qualification Degree'),
                    ('Certificate', 'Certificate'),
                    ('experience_letter', 'Experience Letter'),
                    ('bachelors_degree', "Bachelor's Degree"),
                    ('masters_degree', "Master's Degree"),
                    ('certificate', 'Certificate (Additional)'),
                ],
                max_length=50,
            ),
        ),
    ]
