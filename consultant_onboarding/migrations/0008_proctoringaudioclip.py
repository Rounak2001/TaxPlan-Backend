from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("consultant_onboarding", "0007_consultantapplication_is_phone_verified"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProctoringAudioClip",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("snapshot_id", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("file_path", models.TextField()),
                ("mime_type", models.CharField(blank=True, default="", max_length=80)),
                ("duration_ms", models.IntegerField(blank=True, null=True)),
                ("audio_level", models.FloatField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="proctoring_audio_clips",
                        to="consultant_onboarding.usersession",
                    ),
                ),
            ],
            options={
                "db_table": "application_assessment_proctoringaudioclip",
                "ordering": ["-created_at"],
            },
        ),
    ]

