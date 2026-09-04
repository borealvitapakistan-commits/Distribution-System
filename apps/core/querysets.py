from django.db import models


def _has_active_company_user(user):
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "company_id", None)
    )


class CompanyQuerySet(models.QuerySet):
    def for_company(self, company):
        company_id = getattr(company, "pk", company)

        if not company_id:
            return self.none()

        return self.filter(pk=company_id)

    def for_user(self, user):
        if not _has_active_company_user(user):
            return self.none()

        if not getattr(user, "is_owner", False):
            return self.none()

        return self.filter(
            pk=user.company_id,
            active=True,
        )


class CompanyScopedQuerySet(models.QuerySet):
    def for_company(self, company):
        company_id = getattr(company, "pk", company)

        if not company_id:
            return self.none()

        return self.filter(company_id=company_id)

    def for_user(self, user):
        if not _has_active_company_user(user):
            return self.none()

        company_records = self.filter(
            company_id=user.company_id,
            company__active=True,
        )

        if getattr(user, "is_owner", False):
            return company_records

        # Distributor party is added in Stage 3.
        # Until then, company-wide queries fail closed.
        return self.none()