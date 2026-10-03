from __future__ import annotations

import logging
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.infrastructure.logging.service import configure_logging


class LoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        self._close_handlers()

    @staticmethod
    def _close_handlers() -> None:
        logging.shutdown()
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()

    def test_configure_logging_writes_timestamped_file(self) -> None:
        with TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            logger = configure_logging("DEBUG", log_dir=log_dir)
            logger.debug("diagnostic marker")
            for handler in logging.getLogger().handlers:
                handler.flush()

            files = list(log_dir.glob("*.log"))
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.log$")
            self.assertIn("diagnostic marker", files[0].read_text(encoding="utf-8"))
            self._close_handlers()

    def test_configure_logging_unifies_uvicorn_loggers(self) -> None:
        with TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            configure_logging("INFO", log_dir=log_dir)
            logging.getLogger("uvicorn.error").info("uvicorn lifecycle marker")
            logging.getLogger("uvicorn.access").info("uvicorn access marker")
            for handler in logging.getLogger().handlers:
                handler.flush()

            files = list(log_dir.glob("*.log"))
            self.assertEqual(len(files), 1)
            content = files[0].read_text(encoding="utf-8")
            self.assertIn("uvicorn.error", content)
            self.assertIn("uvicorn lifecycle marker", content)
            self.assertIn("uvicorn.access", content)
            self.assertIn("uvicorn access marker", content)
            self.assertEqual(logging.getLogger("uvicorn.error").handlers, [])
            self.assertTrue(logging.getLogger("uvicorn.error").propagate)
            self._close_handlers()

    def test_configure_logging_redacts_sensitive_uvicorn_query_values(self) -> None:
        with TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            configure_logging("DEBUG", log_dir=log_dir)
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/1.1" %d',
                "127.0.0.1:12345",
                "GET",
                "/v1/auth/github/callback?code=oauth-code&state=oauth-state&iss=https://github.com",
                200,
            )
            for handler in logging.getLogger().handlers:
                handler.flush()

            content = next(log_dir.glob("*.log")).read_text(encoding="utf-8")
            self._close_handlers()
            self.assertIn("code=<redacted>", content)
            self.assertIn("state=<redacted>", content)
            self.assertNotIn("oauth-code", content)
            self.assertNotIn("oauth-state", content)

    def test_configure_logging_redacts_sensitive_protocol_headers(self) -> None:
        with TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            configure_logging("DEBUG", log_dir=log_dir)
            logging.getLogger("uvicorn.error").debug(
                "< Cookie: WAC-SESSION=session-secret; XSRF-TOKEN=xsrf-secret\n"
                "< Authorization: Bearer auth-secret\n"
                "< X-Api-Key: api-key-secret"
            )
            for handler in logging.getLogger().handlers:
                handler.flush()

            content = next(log_dir.glob("*.log")).read_text(encoding="utf-8")
            self._close_handlers()
            self.assertIn("Cookie: <redacted>", content)
            self.assertIn("Authorization: <redacted>", content)
            self.assertIn("X-Api-Key: <redacted>", content)
            self.assertNotIn("session-secret", content)
            self.assertNotIn("xsrf-secret", content)
            self.assertNotIn("auth-secret", content)
            self.assertNotIn("api-key-secret", content)

    def test_configure_logging_suppresses_websocket_ping_pong_noise(self) -> None:
        with TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            configure_logging("DEBUG", log_dir=log_dir)
            logging.getLogger("websockets.server").debug("%% sending keepalive ping")
            logging.getLogger("websockets.server").debug("< PONG 12 34 [binary, 2 bytes]")
            logging.getLogger("uvicorn.error").debug("> PING 'abc' [text, 3 bytes]")
            logging.getLogger("uvicorn.error").debug("websocket accepted")
            for handler in logging.getLogger().handlers:
                handler.flush()

            content = next(log_dir.glob("*.log")).read_text(encoding="utf-8")
            self._close_handlers()
            self.assertIn("websocket accepted", content)
            self.assertNotIn("keepalive ping", content)
            self.assertNotIn("PONG", content)
            self.assertNotIn("PING", content)

    def test_configure_logging_keeps_at_most_one_hundred_log_files(self) -> None:
        with TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            log_dir.mkdir()
            for index in range(105):
                path = log_dir / f"old-{index:03d}.log"
                path.write_text("old", encoding="utf-8")
                timestamp = time.time() - (105 - index)
                path.touch()
                path.chmod(0o666)
                os.utime(path, (timestamp, timestamp))

            configure_logging("INFO", log_dir=log_dir)

            self.assertLessEqual(len(list(log_dir.glob("*.log*"))), 100)
            self._close_handlers()


if __name__ == "__main__":
    unittest.main()
