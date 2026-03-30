from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('document_vault', '0007_documentaccess'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentDownloadLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('purpose', models.TextField()),
                ('downloaded_at', models.DateTimeField(auto_now_add=True)),
                ('consultant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='document_download_logs', to=settings.AUTH_USER_MODEL)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='download_logs', to='document_vault.document')),
            ],
            options={
                'db_table': 'vault_document_download_log',
                'ordering': ['-downloaded_at'],
            },
        ),
        migrations.AddIndex(
            model_name='documentdownloadlog',
            index=models.Index(fields=['document', 'downloaded_at'], name='vault_docum_documen_910da8_idx'),
        ),
        migrations.AddIndex(
            model_name='documentdownloadlog',
            index=models.Index(fields=['consultant', 'downloaded_at'], name='vault_docum_consult_7836ae_idx'),
        ),
    ]
