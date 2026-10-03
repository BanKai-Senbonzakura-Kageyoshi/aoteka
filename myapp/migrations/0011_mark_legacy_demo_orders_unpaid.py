from django.db import migrations


def mark_legacy_orders(apps, schema_editor):
    PharmacyOrder = apps.get_model('myapp', 'PharmacyOrder')
    PharmacyOrder.objects.filter(payment_method='DEMO').update(
        payment_method='LEGACY',
        payment_status='UNPAID',
    )


def restore_demo_orders(apps, schema_editor):
    PharmacyOrder = apps.get_model('myapp', 'PharmacyOrder')
    PharmacyOrder.objects.filter(payment_method='LEGACY').update(
        payment_method='DEMO',
        payment_status='DEMO_PAID',
    )


class Migration(migrations.Migration):
    dependencies = [('myapp', '0010_alter_pharmacyorder_payment_method_and_more')]

    operations = [migrations.RunPython(mark_legacy_orders, restore_demo_orders)]