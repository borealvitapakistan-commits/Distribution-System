# Second half of 0005 (after 0006, which is safe to apply first): drops the old logo columns from Brand. Kept separate
# so 0005 can be applied while the previous release (which still reads
# these columns) is live; apply this one once the new code is deployed.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_dataversion'),
    ]

    operations = [
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
