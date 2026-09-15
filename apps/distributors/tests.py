from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User

from .models import DistributorProfile
from .querysets import is_approved_distributor
from .services import (
    approve_distributor,
    create_distributor,
    reject_distributor,
    suspend_distributor,
)


class DistributorApprovalWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@alpha.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def invite(self, email="distributor@alpha.test", name="Alpha Distributor"):
        return create_distributor(
            actor=self.owner,
            email=email,
            temporary_password="TemporaryPassword123!",
            name=name,
            first_name="Alpha",
        )

    def test_invitation_is_pending_and_inactive(self):
        distributor = self.invite()
        profile = DistributorProfile.objects.get(user=distributor)

        self.assertFalse(distributor.is_active)
        self.assertEqual(
            profile.approval_status,
            DistributorProfile.ApprovalStatus.PENDING,
        )
        self.assertEqual(profile.user_id, distributor.pk)
        self.assertFalse(is_approved_distributor(distributor))

    def test_owner_approval_activates_user_and_profile(self):
        distributor = self.invite()

        approve_distributor(
            user=self.owner,
            distributor_id=distributor.pk,
        )
        distributor.refresh_from_db()
        profile = DistributorProfile.objects.get(user=distributor)

        self.assertTrue(distributor.is_active)
        self.assertEqual(
            profile.approval_status,
            DistributorProfile.ApprovalStatus.APPROVED,
        )
        self.assertEqual(profile.approved_by_id, self.owner.pk)
        self.assertIsNotNone(profile.approved_at)
        self.assertTrue(is_approved_distributor(distributor))

    def test_suspend_and_reject_remove_access(self):
        distributor = self.invite()
        approve_distributor(user=self.owner, distributor_id=distributor.pk)

        suspend_distributor(
            user=self.owner,
            distributor_id=distributor.pk,
            reason="Compliance review",
        )
        distributor.refresh_from_db()
        profile = DistributorProfile.objects.get(user=distributor)
        self.assertFalse(distributor.is_active)
        self.assertEqual(
            profile.approval_status,
            DistributorProfile.ApprovalStatus.SUSPENDED,
        )
        self.assertFalse(is_approved_distributor(distributor))

        second_distributor = self.invite(
            email="second-distributor@alpha.test",
            name="Second Distributor",
        )
        second_profile = DistributorProfile.objects.get(user=second_distributor)
        reject_distributor(
            user=self.owner,
            distributor_id=second_profile.pk,
            reason="Onboarding rejected",
        )
        second_profile.refresh_from_db()
        second_distributor.refresh_from_db()
        self.assertEqual(
            second_profile.approval_status,
            DistributorProfile.ApprovalStatus.REJECTED,
        )
        self.assertFalse(second_distributor.is_active)

    def test_approval_requires_an_invited_user(self):
        profile = DistributorProfile.objects.create(
            name="No User Yet",
            created_by=self.owner,
            updated_by=self.owner,
        )

        with self.assertRaises(ValidationError):
            approve_distributor(
                user=self.owner,
                distributor_id=profile.pk,
            )


class DistributorProfileScopingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@scoping.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def test_create_distributor_creates_pending_profile_with_commission(self):
        distributor = create_distributor(
            actor=self.owner,
            email="dist@scoping.test",
            temporary_password="TemporaryPassword123!",
            name="Distributor A",
            commission_percentage=Decimal("12.50"),
        )

        profile = DistributorProfile.objects.get(user=distributor)
        self.assertEqual(
            profile.approval_status,
            DistributorProfile.ApprovalStatus.PENDING,
        )
        self.assertEqual(profile.commission_percentage, Decimal("12.50"))
        self.assertFalse(distributor.is_active)

    def test_distributor_data_is_isolated_to_its_own_profile(self):
        distributor_a = create_distributor(
            actor=self.owner,
            email="a@scoping.test",
            temporary_password="TemporaryPassword123!",
            name="Distributor A",
        )
        distributor_b = create_distributor(
            actor=self.owner,
            email="b@scoping.test",
            temporary_password="TemporaryPassword123!",
            name="Distributor B",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_a.pk)
        approve_distributor(user=self.owner, distributor_id=distributor_b.pk)
        distributor_a.refresh_from_db()

        self.assertEqual(
            list(DistributorProfile.objects.for_user(distributor_a)),
            [DistributorProfile.objects.get(user=distributor_a)],
        )
        self.assertEqual(
            DistributorProfile.objects.for_user(distributor_a)
            .filter(user=distributor_b)
            .count(),
            0,
        )
