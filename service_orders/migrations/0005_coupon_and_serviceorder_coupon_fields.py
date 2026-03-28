"""
Migration 0005: Register Coupon model and ServiceOrder coupon fields with Django ORM.

The database tables/columns matching this migration already exist (applied before the
code revert). This migration uses SeparateDatabaseAndState so that Django knows about
the schema without issuing any DDL, preventing 'table already exists' or 'duplicate
column' errors when running `migrate`.
"""
from decimal import Decimal
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_orders', '0004_serviceorder_additional_fields'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # No DDL operations – tables/columns already exist in the DB.
            database_operations=[],
            state_operations=[
                # ── 1. Create the Coupon model in Django state ───────────────────
                migrations.CreateModel(
                    name='Coupon',
                    fields=[
                        ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('code', models.CharField(max_length=50, unique=True)),
                        ('description', models.CharField(blank=True, max_length=255)),
                        ('discount_type', models.CharField(choices=[('percentage', 'Percentage'), ('flat', 'Flat Amount')], max_length=10)),
                        ('discount_value', models.DecimalField(decimal_places=2, max_digits=10)),
                        ('min_purchase_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                        ('max_discount_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                        ('valid_from', models.DateTimeField()),
                        ('valid_until', models.DateTimeField()),
                        ('usage_limit', models.PositiveIntegerField(default=0)),
                        ('used_count', models.PositiveIntegerField(default=0)),
                        ('is_active', models.BooleanField(default=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                    ],
                ),
                # ── 2. Add coupon FK to ServiceOrder ────────────────────────────
                migrations.AddField(
                    model_name='serviceorder',
                    name='coupon',
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='orders',
                        to='service_orders.coupon',
                    ),
                ),
                # ── 3. Add discount_amount ───────────────────────────────────────
                migrations.AddField(
                    model_name='serviceorder',
                    name='discount_amount',
                    field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
                ),
                # ── 4. Add original_amount ───────────────────────────────────────
                migrations.AddField(
                    model_name='serviceorder',
                    name='original_amount',
                    field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
                ),
            ],
        ),
    ]
