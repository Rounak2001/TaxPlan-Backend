from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("consultant_onboarding", "0009_proctoringaudiotelemetry"),
    ]

    operations = [
        migrations.AddField(
            model_name="proctoringaudioclip",
            name="transcript",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="proctoringaudioclip",
            name="stt_status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="proctoringaudioclip",
            name="stt_provider",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="proctoringaudioclip",
            name="stt_language",
            field=models.CharField(blank=True, default="", max_length=10),
        ),
        migrations.AddField(
            model_name="proctoringaudioclip",
            name="stt_raw",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="proctoringaudioclip",
            name="cheat_flag",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="proctoringaudioclip",
            name="cheat_matches",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

