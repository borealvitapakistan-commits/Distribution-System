# Second half of 0005 (after 0006, which is safe to apply first): drops the old logo columns from Brand. Kept separate
# so 0005 can be applied while the previous release (which still reads
# these columns) is live; apply this one once the new code is deployed.

from django.db import migrations, models


def preserve_legacy_logos(apps, schema_editor):
    """A logo missed by 0005 must survive dropping the old columns.

    Existing nonempty BrandLogo data wins: a user may have uploaded a
    newer logo since 0005 copied the original bytes.
    """
    Brand = apps.get_model("core", "Brand")
    BrandLogo = apps.get_model("core", "BrandLogo")
    alias = schema_editor.connection.alias
    for brand in Brand.objects.using(alias).exclude(logo_data__isnull=True).iterator():
        if not brand.logo_data:
            continue
        logo, created = BrandLogo.objects.using(alias).get_or_create(
            brand_id=brand.pk,
            defaults={"data": brand.logo_data,
                      "content_type": brand.logo_content_type or "application/octet-stream"},
        )
        if not created and not logo.data:
            logo.data = brand.logo_data
            logo.content_type = brand.logo_content_type or "application/octet-stream"
            logo.save(using=alias, update_fields=["data", "content_type", "updated_at"])
    if schema_editor.connection.vendor == "postgresql":
        # Flush deferred FK events before the following ALTER TABLEs.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        schema_editor.execute("SET CONSTRAINTS ALL DEFERRED")


def restore_legacy_logos(apps, schema_editor):
    Brand = apps.get_model("core", "Brand")
    BrandLogo = apps.get_model("core", "BrandLogo")
    alias = schema_editor.connection.alias
    for logo in BrandLogo.objects.using(alias).iterator():
        Brand.objects.using(alias).filter(pk=logo.brand_id).update(
            logo_data=logo.data, logo_content_type=logo.content_type,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_dataversion'),
    ]

    operations = [
        migrations.RunPython(preserve_legacy_logos, restore_legacy_logos),
        migrations.RemoveField(
            model_name='brand',
            name='logo_content_type',
        ),
        migrations.RemoveField(
            model_name='brand',
            name='logo_data',
        ),
        migrations.AlterField(
            model_name='brand',
            name='logo',
            field=models.ImageField(blank=True, help_text='Only the file name is kept here; the image itself is a BrandLogo row.', null=True, upload_to='brand/logos/'),
        ),
    ]
