from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('consultants', '0012_consultantserviceprofile_gstin_and_more'),
    ]

    operations = [
        # 1. Add consultant_dropped to the status choices
        migrations.AlterField(
            model_name='clientservicerequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending Assignment'),
                    ('assigned', 'Consultant Assigned'),
                    ('doc_pending', 'Documents Pending'),
                    ('under_review', 'Under Review'),
                    ('wip', 'Work In Progress'),
                    ('under_query', 'Clarification Needed'),
                    ('final_review', 'Final Review'),
                    ('filed', 'Work Filed/Submitted'),
                    ('revision_pending', 'Revision Requested'),
                    ('completed', 'Completed'),
                    ('cancelled', 'Cancelled'),
                    ('consultant_dropped', 'Consultant Dropped – Reassigning'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        # 2. Add dropped_consultant FK
        migrations.AddField(
            model_name='clientservicerequest',
            name='dropped_consultant',
            field=models.ForeignKey(
                blank=True,
                help_text='Consultant who last dropped this request',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='dropped_requests',
                to='consultants.consultantserviceprofile',
            ),
        ),
        # 3. Add drop_reason text field
        migrations.AddField(
            model_name='clientservicerequest',
            name='drop_reason',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        # 4. Add dropped_at timestamp
        migrations.AddField(
            model_name='clientservicerequest',
            name='dropped_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # 5. Add drop_count counter
        migrations.AddField(
            model_name='clientservicerequest',
            name='drop_count',
            field=models.IntegerField(
                default=0,
                help_text='Total number of times this request has been dropped by consultants',
            ),
        ),
        # 6. Add reassignment_deadline
        migrations.AddField(
            model_name='clientservicerequest',
            name='reassignment_deadline',
            field=models.DateTimeField(
                blank=True,
                help_text='Client must pick a new consultant by this time; then auto-assign fires',
                null=True,
            ),
        ),
    ]
