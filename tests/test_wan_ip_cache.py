"""The WAN address lookup behind a `read`-scoped route is not repeated on every call."""

import unittest
from unittest.mock import patch

import app.public_target as public_target


class TheLookupIsCachedOnlyWhenAskedTests(unittest.TestCase):
    def setUp(self) -> None:
        public_target._detected.clear()
        self.addCleanup(public_target._detected.clear)

    def _count_lookups(self):
        calls: list[tuple] = []

        def lookup(sources, timeout_seconds):
            calls.append((tuple(sources or ()), timeout_seconds))
            return "203.0.113.7"

        return calls, patch.object(public_target, "_detect_uncached", lookup)

    def test_a_second_call_inside_the_window_is_answered_from_the_cache(self) -> None:
        calls, patcher = self._count_lookups()
        with patcher:
            for _ in range(3):
                self.assertEqual(
                    public_target.detect_server_public_ip(
                        sources=["https://r.example"], timeout_seconds=1, max_age=60
                    ),
                    "203.0.113.7",
                )
        self.assertEqual(len(calls), 1)

    def test_an_expired_answer_is_fetched_again(self) -> None:
        calls, patcher = self._count_lookups()
        with patcher, patch.object(public_target.time, "monotonic", side_effect=[0, 61, 61]):
            public_target.detect_server_public_ip(sources=["https://r.example"], max_age=60)
            public_target.detect_server_public_ip(sources=["https://r.example"], max_age=60)
        self.assertEqual(len(calls), 2)

    def test_without_max_age_every_call_looks_up(self) -> None:
        calls, patcher = self._count_lookups()
        with patcher:
            public_target.detect_server_public_ip(sources=["https://r.example"])
            public_target.detect_server_public_ip(sources=["https://r.example"])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
