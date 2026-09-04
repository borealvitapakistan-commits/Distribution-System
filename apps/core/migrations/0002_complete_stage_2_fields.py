import calendar

from datetime import date

import django.db.models.deletion

from django.db import migrations, models


def populate_fiscal_period_dates(
    apps,
    schema_editor,
):
    FiscalPeriod = apps.get_model(
        "core",
        "FiscalPeriod",
    )

    for period in (
        FiscalPeriod.objects
        .all()
        .iterator()
    ):
        period.start_date = date(
            period.year,
            period.month,
            1,
        )

        period.end_date = date(
            period.year,
            period.month,
            calendar.monthrange(
                period.year,
                period.month,
            )[1],
        )

        period.save(
            update_fields=[
                "start_date",
                "end_date",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        (
            "core",
            "0001_initial",
        ),
    ]

    operations = [
        migrations.RenameField(
            model_name="company",
            old_name="enlist_number",
            new_name="enlistment_number",
        ),
        migrations.RenameField(
            model_name="company",
            old_name="stn",
            new_name="strn",
        ),
        migrations.RenameField(
            model_name="company",
            old_name="default_conutry",
            new_name="default_country",
        ),
        migrations.AlterField(
            model_name="equitypartner",
            name="company",
            field=models.ForeignKey(
                on_delete=(
                    django.db.models
                    .deletion.PROTECT
                ),
                related_name=(
                    "equity_partners"
                ),
                to="core.company",
            ),
        ),
        migrations.AddField(
            model_name="fiscalperiod",
            name="start_date",
            field=models.DateField(
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="fiscalperiod",
            name="end_date",
            field=models.DateField(
                null=True,
            ),
        ),
        migrations.RunPython(
            populate_fiscal_period_dates,
            reverse_code=(
                migrations.RunPython.noop
            ),
        ),
        migrations.AlterField(
            model_name="fiscalperiod",
            name="start_date",
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name="fiscalperiod",
            name="end_date",
            field=models.DateField(),
        ),
        migrations.AddConstraint(
            model_name="fiscalperiod",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    start_date__lte=models.F(
                        "end_date"
                    )
                ),
                name=(
                    "fiscal_period_valid_dates"
                ),
            ),
        ),
    ]