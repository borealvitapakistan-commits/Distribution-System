# The "data version" behind the page cache (apps.core.page_cache).

from django.db import migrations, models


def create_row(apps, schema_editor):
    apps.get_model("core", "DataVersion").objects.get_or_create(id=1)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_brand_logo_table'),
    ]

    operations = [
        migrations.CreateModel(
            name='DataVersion',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)),
                ('number', models.BigIntegerField(default=0)),
            ],
        ),
        migrations.RunPython(create_row, migrations.RunPython.noop),
    ]
