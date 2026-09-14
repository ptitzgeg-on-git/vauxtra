"""The three readers of a certificate's `expires_on`, and what happens when they disagree.

A provider hands back one string. Three things in Vauxtra read it:

  * `app.api.certificates._parse_expiry`, which produces `days_remaining`, `expiring_soon`
    and `expired`, and through them `expiring_soon_count` -- the number behind the sidebar
    badge, the dashboard tile and the "needs attention" entry;
  * the scheduler's expiry scan, which decides whether to write a CRITICAL or a WARNING
    line into the activity log;
  * `parseBackendTimestamp` in the browser, which draws the countdown on the certificates
    page itself.

The first one accepted three spellings. The other two accept an ISO 8601 date in
essentially any shape. So a certificate expiring tomorrow whose date arrived as
`2027-01-01T00:00:00+00:00` -- the form `datetime.isoformat()` writes and the one RFC 3339
specifies -- was counted by nobody: the badge stayed dark and the dashboard reported a
clean estate, while the log filled with CRITICAL lines about it and the certificates page
drew the countdown in red. Three surfaces of one product, three verdicts, one string.

These tests pin the agreement rather than any one rule, by running the route and the
scheduler over the same payload and requiring them to reach the same conclusion.
"""

import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models, scheduler
from app.api import certificates as certificates_api
from app.config import encrypt_secret


def _request() -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/api/certificates/expiry",
        "headers": [], "query_string": b"",
    })


class _OneCertificate:
    """A store holding a single certificate, whose expiry string the test chooses."""

    def __init__(self, expires_on):
        self.expires_on = expires_on

    def get_certificates(self) -> list:
        return [{
            "id": 1,
            "nice_name": "wildcard",
            "domains": ["*.example.com"],
            "expires_on": self.expires_on,
        }]


# Every spelling of the same instant that at least one reader in this repo accepts. The
# `Z`, the offset and the space separator all appear in the wild: NPM serves whatever its
# database layer formats, and a database layer is exactly where the separator changes.
SPELLINGS = (
    "%Y-%m-%dT%H:%M:%S.000Z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S+00:00",
    "%Y-%m-%dT%H:%M:%S.000+00:00",
    "%Y-%m-%d %H:%M:%S.000",
)


class ExpiryDateCase(unittest.TestCase):
    """One enabled certificate provider against a temporary database."""

    def setUp(self):
        import app.db as _app_db
        self._tmpdir = tempfile.TemporaryDirectory()
        self._saved = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "expiry.test.db")
        models.init_db()
        scheduler._cert_alert_state.clear()
        with models.get_db_ctx() as conn:
            conn.execute(
                "INSERT INTO providers (name, type, url, username, password, enabled) "
                "VALUES ('npm-a', 'npm', 'http://npm:81', 'a@example.com', ?, 1)",
                (encrypt_secret("secret"),),
            )
            conn.commit()

    def tearDown(self):
        import app.db as _app_db
        scheduler._cert_alert_state.clear()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._saved
        self._tmpdir.cleanup()

    def route_answer(self, expires_on) -> dict:
        with patch.object(certificates_api, "require_auth", lambda _r, scope=None: None), \
             patch.object(certificates_api, "create_provider", lambda _row: _OneCertificate(expires_on)):
            return certificates_api.certificate_expiry(_request())

    def scheduler_lines(self, expires_on) -> list:
        """Every `[CertExpiry]` line one scan writes, including a scan that gave up.

        Three connections rather than one, because the scan is entitled to leave a
        write uncommitted on the connection it was handed, and a reader holding its own
        open transaction across that call would deadlock against it -- a fact about this
        helper, not about the scan, and one that must not be able to pass for one.

        The alert memory is cleared alongside the log table, because the scan is right
        not to repeat itself: it re-states a certificate at the same severity only once
        a day. Every call here is a first sighting of that certificate, so the count of
        lines answers what this scan decided rather than what an earlier one remembered.
        """
        scheduler._cert_alert_state.clear()
        with models.get_db_ctx() as conn:
            conn.execute("DELETE FROM logs")
            conn.commit()
        with patch.object(scheduler, "create_provider", lambda _row: _OneCertificate(expires_on)):
            with models.get_db_ctx() as conn:
                scheduler._run_cert_expiry_alerts(conn)
                conn.commit()
        with models.get_db_ctx() as conn:
            return [r["message"] for r in conn.execute(
                "SELECT message FROM logs WHERE message LIKE '[CertExpiry]%'").fetchall()]


class TheRouteAndTheSchedulerReadTheSameDate(ExpiryDateCase):

    def test_a_certificate_expiring_tomorrow_is_counted_however_its_date_is_spelled(self):
        """The defect, stated as the agreement it broke.

        `expiring_soon_count` is the sidebar badge and the dashboard tile. Five of these
        seven spellings left it at zero for a certificate the scheduler was at the same
        moment calling CRITICAL.
        """
        tomorrow = datetime.datetime.utcnow() + datetime.timedelta(days=1, hours=2)
        for fmt in SPELLINGS:
            raw = tomorrow.strftime(fmt)
            with self.subTest(expires_on=raw):
                answer = self.route_answer(raw)
                lines = self.scheduler_lines(raw)
                self.assertEqual(answer["certificates"][0]["days_remaining"], 1, raw)
                self.assertTrue(answer["certificates"][0]["expiring_soon"], raw)
                self.assertEqual(answer["expiring_soon_count"], 1, raw)
                self.assertEqual(len(lines), 1, raw)
                self.assertIn("CRITICAL", lines[0])

    def test_a_certificate_with_a_year_left_is_quiet_on_both_sides(self):
        later = datetime.datetime.utcnow() + datetime.timedelta(days=365)
        for fmt in SPELLINGS:
            raw = later.strftime(fmt)
            with self.subTest(expires_on=raw):
                answer = self.route_answer(raw)
                self.assertEqual(answer["expiring_soon_count"], 0, raw)
                self.assertFalse(answer["certificates"][0]["expired"], raw)
                self.assertEqual(self.scheduler_lines(raw), [])

    def test_a_date_only_string_still_reads_as_midnight(self):
        tomorrow = datetime.datetime.utcnow() + datetime.timedelta(days=2)
        answer = self.route_answer(tomorrow.strftime("%Y-%m-%d"))
        self.assertEqual(answer["expiring_soon_count"], 1)


class AnOffsetIsConvertedNotDiscarded(ExpiryDateCase):
    """A date one hour from lapsing, written in a zone two hours ahead, has one hour left."""

    def test_a_non_utc_offset_moves_the_instant(self):
        # Same instant, three ways of writing it. If the offset were dropped rather than
        # applied, the +02:00 spelling would read two hours later than the other two.
        moment = datetime.datetime(2027, 6, 1, 12, 0, 0)
        for raw in ("2027-06-01T12:00:00Z",
                    "2027-06-01T14:00:00+02:00",
                    "2027-06-01T09:00:00-03:00"):
            with self.subTest(expires_on=raw):
                self.assertEqual(certificates_api.parse_expiry(raw), moment)

    def test_what_it_returns_can_be_subtracted_from_utcnow(self):
        """The reason the offset is stripped rather than kept.

        Every comparison downstream is against a naive `utcnow()`. An aware value would
        not be quietly wrong here, it would raise `TypeError` and take the route down, so
        the conversion belongs in the parser and nowhere else.
        """
        parsed = certificates_api.parse_expiry("2027-06-01T12:00:00+02:00")
        self.assertIsNone(parsed.tzinfo)
        self.assertIsInstance(parsed - datetime.datetime.utcnow(), datetime.timedelta)


class ADateThatIsNotADateStaysUnknown(ExpiryDateCase):

    def test_nothing_usable_reads_as_unknown_rather_than_as_valid(self):
        # Zoraxy writes "Unknown" for a certificate it could not parse itself, and a
        # provider that has no date at all sends "". Neither is a certificate in trouble
        # and neither is a certificate that is fine: `days_remaining` is None, which is
        # what the page draws as unknown.
        for raw in (None, "", "   ", "Unknown", "not a date", "0000-00-00", "2027-13-01"):
            with self.subTest(expires_on=raw):
                self.assertIsNone(certificates_api.parse_expiry(raw))

    def test_a_provider_that_sends_something_other_than_a_string_loses_one_row(self):
        """Not the route.

        `strptime` raises `TypeError` on a non-string, not `ValueError`, so the old loop's
        `except ValueError` did not catch it and a single malformed entry answered the
        whole page with a 500. One unreadable certificate is one unknown row.
        """
        for raw in (0, 1767225600, ["2027-01-01"], {"date": "2027-01-01"}, object()):
            with self.subTest(expires_on=repr(raw)):
                self.assertIsNone(certificates_api.parse_expiry(raw))
                answer = self.route_answer(raw)
                self.assertIsNone(answer["certificates"][0]["days_remaining"])
                self.assertEqual(answer["expiring_soon_count"], 0)


class OneUnreadableDateDoesNotEndTheScan(ExpiryDateCase):
    """The scheduler's own half, which failed wider than the route's.

    The route's guard is per certificate. The scheduler's `try` covered only the parsing,
    and the subtraction that came after it was outside: an aware datetime -- what a
    negative offset produced, since only `+` was being stripped -- raised `TypeError`
    there, which the scan's outer handler caught. That handler wraps the whole sweep, so
    one certificate stamped `-03:00` cost every alert for every provider behind it.
    """

    def test_a_negative_offset_is_read_rather_than_ending_the_sweep(self):
        soon = datetime.datetime.utcnow() + datetime.timedelta(days=3)
        raw = (soon - datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S") + "-03:00"
        lines = self.scheduler_lines(raw)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("CRITICAL", lines[0])
        self.assertNotIn("Check failed", " ".join(lines))
