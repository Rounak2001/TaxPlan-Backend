from django.db import migrations


class Migration(migrations.Migration):
    """
    Compatibility migration.

    The coupon + discount schema is already introduced in
    0004_coupon_serviceorder_discount_amount_and_more. This migration now acts
    as a no-op to keep migration history stable across environments where this
    filename already exists.
    """

    dependencies = [
        ("service_orders", "0005_merge_20260327_1901"),
    ]

    operations = []

