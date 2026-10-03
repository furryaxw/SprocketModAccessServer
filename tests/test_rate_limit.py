from __future__ import annotations

import unittest

from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.infrastructure.utilities.rate_limit import InMemoryRateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_scope_and_subject_are_isolated_and_window_expires(self) -> None:
        limiter = InMemoryRateLimiter()
        limiter.check(scope="redeem", subject="user:1", limit=1, window=10, now=100)
        limiter.check(scope="redeem", subject="user:2", limit=1, window=10, now=100)
        with self.assertRaises(ApiError) as raised:
            limiter.check(scope="redeem", subject="user:1", limit=1, window=10, now=101)
        self.assertEqual(raised.exception.code, "rate_limit_exceeded")
        self.assertEqual(dict(raised.exception.headers)["Retry-After"], "9")
        limiter.check(scope="redeem", subject="user:1", limit=1, window=10, now=110)


if __name__ == "__main__":
    unittest.main()
