from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LogoColumnMigrationTests(TransactionTestCase):
    def test_forward_preserves_missing_and_newer_logos_and_reverse_restores_columns(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("core", "0006_dataversion")])
            old_apps = executor.loader.project_state([("core", "0006_dataversion")]).apps
            Brand = old_apps.get_model("core", "Brand")
            BrandLogo = old_apps.get_model("core", "BrandLogo")
            missing = Brand.objects.create(name="Missing copied logo", logo="brand/old.png", logo_data=b"old-only", logo_content_type="image/png")
            newer = Brand.objects.create(name="New uploaded logo", logo="brand/new.png", logo_data=b"old-version", logo_content_type="image/png")
            empty = Brand.objects.create(name="Empty copied logo", logo="brand/empty.png", logo_data=b"recoverable", logo_content_type="image/png")
            BrandLogo.objects.create(brand_id=newer.pk, data=b"newer-version", content_type="image/jpeg")
            BrandLogo.objects.create(brand_id=empty.pk, data=b"", content_type="image/png")

            executor = MigrationExecutor(connection)
            executor.migrate([("core", "0007_remove_brand_logo_data")])
            new_apps = executor.loader.project_state([("core", "0007_remove_brand_logo_data")]).apps
            NewLogo = new_apps.get_model("core", "BrandLogo")
            self.assertEqual(bytes(NewLogo.objects.get(brand_id=missing.pk).data), b"old-only")
            self.assertEqual(bytes(NewLogo.objects.get(brand_id=newer.pk).data), b"newer-version")
            self.assertEqual(NewLogo.objects.get(brand_id=newer.pk).content_type, "image/jpeg")
            self.assertEqual(bytes(NewLogo.objects.get(brand_id=empty.pk).data), b"recoverable")
            with connection.cursor() as cursor:
                columns = {c.name for c in connection.introspection.get_table_description(cursor, "core_brand")}
            self.assertNotIn("logo_data", columns)
            self.assertNotIn("logo_content_type", columns)

            executor = MigrationExecutor(connection)
            executor.migrate([("core", "0006_dataversion")])
            old_apps = executor.loader.project_state([("core", "0006_dataversion")]).apps
            RestoredBrand = old_apps.get_model("core", "Brand")
            restored = RestoredBrand.objects.get(pk=newer.pk)
            self.assertEqual(bytes(restored.logo_data), b"newer-version")
            self.assertEqual(restored.logo_content_type, "image/jpeg")
        finally:
            MigrationExecutor(connection).migrate(latest)
