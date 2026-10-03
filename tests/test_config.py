import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sprocket_access_server.infrastructure.configuration.paths import REPOSITORY_ROOT, environment_file, \
    installation_root
from sprocket_access_server.infrastructure.configuration.settings import ServerSettings, load_environment_file


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


class EnvironmentFileTests(unittest.TestCase):
    def test_file_values_fill_the_environment_and_process_values_win(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("SMAS_SERVER_NAME=from-file\nSMAS_SERVER_ID=from-file\n", encoding="utf-8")
            with patch.dict("os.environ", {"SMAS_SERVER_ID": "from-process"}, clear=True):
                load_environment_file(path)

                self.assertEqual(os.environ["SMAS_SERVER_NAME"], "from-file")
                self.assertEqual(os.environ["SMAS_SERVER_ID"], "from-process")

    def test_secret_values_keep_dollar_signs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("SMAS_SESSION_PEPPER=pep$per$1\n", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                load_environment_file(path)

                self.assertEqual(os.environ["SMAS_SESSION_PEPPER"], "pep$per$1")

    def test_missing_file_leaves_the_environment_untouched(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"SMAS_SERVER_ID": "from-process"}, clear=True):
            load_environment_file(Path(directory) / ".env")

            self.assertEqual(dict(os.environ), {"SMAS_SERVER_ID": "from-process"})

    def test_repository_environment_file_sits_at_the_checkout_root(self):
        self.assertEqual(environment_file(), REPOSITORY_ROOT / ".env")


class DeploymentRootTests(unittest.TestCase):
    def test_root_does_not_follow_the_working_directory(self):
        previous = os.getcwd()
        with TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                with patch.dict("os.environ", {}, clear=True):
                    self.assertEqual(installation_root(), REPOSITORY_ROOT)
            finally:
                os.chdir(previous)

    def test_configured_root_replaces_the_checkout_root(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"SMAS_ROOT": directory}, clear=True):
            self.assertEqual(installation_root(), Path(directory))

    def test_relative_configured_root_resolves_against_the_checkout_root(self):
        previous = os.getcwd()
        with TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                with patch.dict("os.environ", {"SMAS_ROOT": "src"}, clear=True):
                    self.assertEqual(installation_root(), REPOSITORY_ROOT / "src")
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
