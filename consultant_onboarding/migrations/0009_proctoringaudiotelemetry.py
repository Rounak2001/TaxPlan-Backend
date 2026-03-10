from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("consultant_onboarding", "0008_proctoringaudioclip"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProctoringAudioTelemetry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("window_start", models.DateTimeField(blank=True, null=True)),
                ("window_end", models.DateTimeField(blank=True, null=True)),
                ("speech_ms", models.IntegerField(default=0)),
                ("bursts", models.IntegerField(default=0)),
                ("sample_count", models.IntegerField(default=0)),
                ("avg_level", models.FloatField(blank=True, null=True)),
                ("max_level", models.FloatField(blank=True, null=True)),
                ("threshold", models.FloatField(blank=True, null=True)),
                ("mic_status", models.CharField(blank=True, default="", max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="proctoring_audio_telemetry",
                        to="consultant_onboarding.usersession",
                    ),
                ),
            ],
            options={
                "db_table": "application_assessment_proctoringaudiotelemetry",
                "ordering": ["-created_at"],
            },
        ),
    ]

