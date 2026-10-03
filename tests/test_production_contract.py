from __future__ import annotations

import unittest

from sprocket_access_server import ApiError, PROTOCOL_VERSION, ServerInfo
from sprocket_access_server.domain.errors import ERROR_CATALOG


class ProductionContractTests(unittest.TestCase):
    def test_error_payload_is_stable_and_excludes_internal_fields(self) -> None:
        error = ApiError(401, "invalid_session", "localized message")
        self.assertEqual(error.payload(), {"code": "invalid_session", "message": "localized message"})

    def test_error_rejects_invalid_contract_values(self) -> None:
        with self.assertRaises(ValueError):
            ApiError(200, "invalid", "message")
        with self.assertRaises(ValueError):
            ApiError(400, "invalid-code", "message")
        with self.assertRaises(ValueError):
            ApiError(400, "unknown_error", "message")

    def test_error_catalog_has_valid_http_statuses(self) -> None:
        self.assertGreaterEqual(len(ERROR_CATALOG), 20)
        self.assertTrue(all(400 <= status <= 599 for status in ERROR_CATALOG.values()))

    def test_server_info_has_frozen_protocol_version(self) -> None:
        info = ServerInfo("server-1", "Example", None, {"trust_methods": ["https"]})
        self.assertEqual(PROTOCOL_VERSION, 2)
        self.assertEqual(info.payload()["protocol_version"], 2)


if __name__ == "__main__":
    unittest.main()
