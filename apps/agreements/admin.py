from django.contrib import admin

from .models import Agreement, AgreementProductRate


class AgreementProductRateInline(admin.TabularInline):
    model = AgreementProductRate
    extra = 0
    fields = ("product", "discount_percentage")


@admin.register(Agreement)
class AgreementAdmin(admin.ModelAdmin):
    list_display = (
        "agreement_number",
        "distributor_profile",
        "discount_percentage",
        "start_date",
        "end_date",
        "status",
    )
    list_filter = ("status",)
    search_fields = ("agreement_number", "distributor_profile__name")
    inlines = [AgreementProductRateInline]
    readonly_fields = (
        "agreement_number",
        "owner_signed_by",
        "owner_signed_at",
        "distributor_signature",
        "distributor_signed_by",
        "distributor_signed_at",
    )
