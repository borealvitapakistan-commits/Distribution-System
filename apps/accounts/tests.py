from django.test import TestCase

from .models import User


class UserManagerTests(TestCase):
    def test_create_user_requires_a_valid_role(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(
                email="bad-role@accounts.test",
                password="Password123!",
                role="NOT_A_ROLE",
            )

    def test_email_is_normalized_to_lowercase(self):
        user = User.objects.create_user(
            email="Mixed.Case@Accounts.Test",
            password="Password123!",
            role=User.Role.OWNER,
        )
        self.assertEqual(user.email, "Mixed.Case@accounts.test")
