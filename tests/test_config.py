import unittest
from unittest.mock import patch

from sprocket_access_server.infrastructure.configuration.settings import ServerSettings


class StorageConfigTests(unittest.TestCase):
    def test_s3_compatible_url_extracts_endpoint_bucket_and_prefix(self):
        settings = ServerSettings(object_storage_url="s3+https://minio.example.test/mods/private")
        self.assertEqual(settings.s3_storage_config(), ("mods", "private", "https://minio.example.test"))

    def test_registration_policy_reads_environment(self):
        with patch.dict("os.environ", {
            "SMAS_AUTO_REGISTER_USERS": "false",
            "SMAS_FIRST_LOGIN_PERMISSION_TEMPLATE": "template.owner",
            "SMAS_NEW_USER_PERMISSION_TEMPLATE": "template.user",
        }, clear=True):
            settings = ServerSettings.from_environment()

        self.assertFalse(settings.auto_register_users)
        self.assertEqual(settings.first_login_permission_template, "template.owner")
        self.assertEqual(settings.new_user_permission_template, "template.user")

    def test_download_origins_read_environment(self):
        with patch.dict("os.environ", {
            "SMAS_DOWNLOAD_ORIGINS": "https://cdn.example.test/, storage.example.test ,https://cdn.example.test",
        }, clear=True):
            settings = ServerSettings.from_environment()

        self.assertEqual(settings.download_origins, ("https://cdn.example.test", "storage.example.test"))

    def test_download_origins_are_empty_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = ServerSettings.from_environment()

        self.assertEqual(settings.download_origins, ())


if __name__ == "__main__":
    unittest.main()
