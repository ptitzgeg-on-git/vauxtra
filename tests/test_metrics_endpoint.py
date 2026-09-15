"""Tests for the Prometheus metrics endpoint.

These only read, so pointing at the wrong database cost nothing here beyond counting the
caller's rows instead of a known set. It was still the wrong database: `DATA_DIR` and
`DB_PATH` were set in `os.environ`, and `app/config.py` builds both from its own location
without ever reading the environment. Rebinding the module attributes is what the rest of
this suite does and what actually redirects the connection.
"""

import os
import re
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient


class TestMetricsEndpoint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import app.db as _app_db
        from app import models

        cls._tmpdir = tempfile.mkdtemp()
        cls._db_path = os.path.join(cls._tmpdir, "test.db")

        cls._orig = (models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH)
        models.DATA_DIR = _app_db.DATA_DIR = cls._tmpdir
        models.DB_PATH = _app_db.DB_PATH = cls._db_path

        # See the note in `test_templates_api.py`: read when `app.config` is first imported.
        os.environ["SECRET_KEY"] = "test-secret-key-for-metrics"

        models.init_db()

        from app.main import app
        cls.client = TestClient(app, raise_server_exceptions=True)

    @classmethod
    def tearDownClass(cls):
        import shutil

        import app.db as _app_db
        from app import models

        models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH = cls._orig
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_metrics_endpoint_returns_200(self):
        r = self.client.get("/metrics")
        self.assertEqual(r.status_code, 200)

    def test_metrics_content_type_is_text(self):
        r = self.client.get("/metrics")
        self.assertIn("text/plain", r.headers.get("content-type", ""))

    def test_metrics_contains_service_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_services_total", r.text)

    def test_metrics_contains_provider_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_providers_total", r.text)

    def test_metrics_contains_log_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_logs_24h", r.text)

    def test_metrics_contains_template_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_templates_total", r.text)

    def test_metrics_contains_schema_version(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_schema_version", r.text)

    def test_metrics_format_valid_prometheus_lines(self):
        r = self.client.get("/metrics")
        for line in r.text.splitlines():
            if not line or line.startswith("#"):
                continue
            # Each data line must have a numeric value as last token
            parts = line.rsplit(" ", 1)
            self.assertEqual(len(parts), 2, f"Invalid line: {line!r}")
            try:
                float(parts[1])
            except ValueError:
                self.fail(f"Non-numeric value in metrics line: {line!r}")

    def test_metrics_status_labels(self):
        r = self.client.get("/metrics")
        text = r.text
        self.assertIn('status="ok"', text)
        self.assertIn('status="error"', text)
        self.assertIn('status="unknown"', text)

    def test_metrics_enabled_state_labels(self):
        r = self.client.get("/metrics")
        text = r.text
        self.assertIn('state="enabled"', text)
        self.assertIn('state="disabled"', text)

    def test_metrics_webhook_gauge(self):
        r = self.client.get("/metrics")
        self.assertIn("vauxtra_webhooks_total", r.text)

    def test_metrics_no_auth_required(self):
        """Prometheus scrape path must be accessible without auth."""
        from fastapi.testclient import TestClient

        from app.main import app
        # Remove auth header entirely
        with TestClient(app) as plain_client:
            r = plain_client.get("/metrics")
        self.assertEqual(r.status_code, 200)



class TheEndpointIsReadBackAsPrometheusWouldReadIt(unittest.TestCase):
    """The tests above ask whether a name appears in the body. That is not the same question.

    `vauxtra_logs_24h{level="warn"}` appeared in every one of those bodies and was always
    zero, because `add_log` folds "warn" into "warning" before the insert and this endpoint
    asked SQLite for the spelling nothing writes. A substring check cannot see that: the
    name it looks for is precisely the one that lies. So this class writes rows of known
    shape and reads the numbers back, and parses the body the way a scrape does rather than
    searching it.
    """

    @classmethod
    def setUpClass(cls):
        import app.db as _app_db
        from app import models

        cls._tmpdir = tempfile.mkdtemp()
        cls._orig = (models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH)
        models.DATA_DIR = _app_db.DATA_DIR = cls._tmpdir
        models.DB_PATH = _app_db.DB_PATH = os.path.join(cls._tmpdir, "test.db")
        os.environ.setdefault("SECRET_KEY", "test-secret-key-for-metrics")
        models.init_db()

        conn = models.get_db()
        # One warning through the front door, and two rows in the spelling used before the
        # fold existed -- `app/api/settings.py` still folds those for `GET /api/logs`.
        models.add_log("warning", "through add_log", conn)
        conn.execute("INSERT INTO logs (level, message) VALUES ('warn', 'written before')")
        conn.execute("INSERT INTO logs (level, message) VALUES ('WARN', 'and shouting')")
        conn.execute("INSERT INTO logs (level, message) VALUES ('debug', 'no app/ writes this')")
        conn.execute("INSERT INTO logs (level, message) VALUES ('', 'no level at all')")
        for sub, status in (("a", "ok"), ("b", "ok"), ("c", "error"), ("d", "unknown")):
            conn.execute(
                "INSERT INTO services (subdomain, domain, target_ip, target_port, status)"
                " VALUES (?,?,?,?,?)",
                (sub, "example.test", "10.0.0.1", 80, status),
            )
        conn.execute(
            "INSERT INTO providers (name, type, url, enabled) VALUES ('p1','cloudflare','u',1)"
        )
        conn.execute(
            "INSERT INTO providers (name, type, url, enabled) VALUES ('p2','cloudflare','u',0)"
        )
        # A type is a column, not a literal, so it can hold the one character that ends a
        # label set. Nothing in the product writes this; the escaping is what is under test.
        conn.execute(
            "INSERT INTO providers (name, type, url, enabled) VALUES ('p3',?,'u',1)",
            ('we' + chr(34) + 'ird',),
        )
        # Every conditional family gets a row too, so the body below carries the whole
        # catalogue and the documentation can be checked against it in both directions.
        conn.execute("INSERT INTO webhooks (name, url, enabled) VALUES ('w1', 'u', 1)")
        conn.execute("INSERT INTO webhooks (name, url, enabled) VALUES ('w2', 'u', 0)")
        for status in ("pending", "delivered", "failed"):
            conn.execute(
                "INSERT INTO webhook_delivery_log (webhook_id, url, title, body, status,"
                " attempt, error_msg) VALUES (1, 'u', 't', 'b', ?, 1, '')",
                (status,),
            )
        conn.execute(
            "INSERT INTO service_templates (name, description, domain) VALUES ('t', 'd', 'e')"
        )
        conn.commit()
        conn.close()

        from app.main import app
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.body = cls.client.get("/metrics").text

    @classmethod
    def tearDownClass(cls):
        import shutil

        import app.db as _app_db
        from app import models

        models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH = cls._orig
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    # ── reading the body the way a scrape does ────────────────────────────────
    def _samples(self):
        """Every sample line as (family, label string, value), in the order published."""
        out = []
        for line in self.body.splitlines():
            if not line or line.startswith("#"):
                continue
            head, _, value = line.rpartition(" ")
            family, brace, labels = head.partition("{")
            out.append((family, labels[:-1] if brace else "", value))
        return out

    def _declared(self, keyword):
        return {line.split()[2] for line in self.body.splitlines() if line.startswith("# " + keyword)}

    def _value(self, family, labels):
        for f, lbl, value in self._samples():
            if f == family and lbl == labels:
                return float(value)
        return None

    # ── the level the column actually holds ───────────────────────────────────
    def test_a_warning_is_counted_under_the_spelling_the_column_holds(self):
        self.assertEqual(self._value("vauxtra_logs_24h", 'level="warning"'), 3.0)

    def test_the_spelling_nothing_writes_is_no_longer_published(self):
        self.assertIsNone(self._value("vauxtra_logs_24h", 'level="warn"'))

    def test_a_level_nothing_in_the_product_writes_is_reported_not_dropped(self):
        self.assertEqual(self._value("vauxtra_logs_24h", 'level="debug"'), 1.0)

    def test_a_row_with_no_level_gets_a_name_instead_of_an_empty_label(self):
        self.assertEqual(self._value("vauxtra_logs_24h", 'level="unspecified"'), 1.0)
        self.assertIsNone(self._value("vauxtra_logs_24h", 'level=""'))

    def test_a_quiet_level_reads_zero_rather_than_going_absent(self):
        from app.models import LOG_LEVELS

        for level in LOG_LEVELS:
            with self.subTest(level=level):
                self.assertIsNotNone(self._value("vauxtra_logs_24h", f'level="{level}"'))

    # ── the shape of the document ─────────────────────────────────────────────
    def test_every_family_published_declares_its_help_and_its_type(self):
        helps, types = self._declared("HELP"), self._declared("TYPE")
        for family in sorted({f for f, _, _ in self._samples()}):
            with self.subTest(family=family):
                self.assertIn(family, helps)
                self.assertIn(family, types)

    def test_no_family_is_interleaved_with_another(self):
        """One group per family: OpenMetrics refuses interleaving, and the text format asks
        for every sample of a family together behind a single HELP and TYPE."""
        seen, closed, previous = [], set(), None
        for family, _, _ in self._samples():
            if family != previous:
                self.assertNotIn(family, closed, f"{family} resumes after another family")
                if previous is not None:
                    closed.add(previous)
                seen.append(family)
                previous = family
        self.assertEqual(len(seen), len(set(seen)))

    def test_a_quote_in_a_label_value_is_escaped_rather_than_ending_the_label_set(self):
        quoted = [lbl for f, lbl, _ in self._samples() if f == "vauxtra_providers_total"]
        escaped = 'type="we' + chr(92) + chr(34) + 'ird"'
        self.assertIn(escaped, quoted)
        for _, labels, _ in self._samples():
            with self.subTest(labels=labels):
                # Every quote is either a delimiter or escaped; an odd one anywhere means a
                # label value closed the set early and the rest of the line is not a label.
                unescaped = labels.replace(chr(92) + chr(34), "")
                self.assertEqual(unescaped.count(chr(34)) % 2, 0)

    def test_the_roll_up_member_really_is_the_sum_of_the_parts_beside_it(self):
        """`status="all"` sits in the same family as the parts it contains, which is why the
        HELP text says so. It is only harmless while it stays exact: a status that reaches
        the table without reaching the tuple above would leave the parts short of it."""
        parts = sum(
            self._value("vauxtra_services_total", f'status="{s}"')
            for s in ("ok", "error", "unknown")
        )
        self.assertEqual(self._value("vauxtra_services_total", 'status="all"'), parts)
        self.assertEqual(parts, 4.0)

    def test_the_two_roll_ups_say_so_in_their_help_text(self):
        for family in ("vauxtra_services_total", "vauxtra_webhooks_total"):
            with self.subTest(family=family):
                line = next(
                    ln for ln in self.body.splitlines()
                    if ln.startswith("# HELP " + family + " ")
                )
                self.assertIn('"all"', line)

    def test_enabled_providers_are_a_subset_of_the_providers_counted(self):
        for family, labels, value in self._samples():
            if family != "vauxtra_providers_enabled":
                continue
            with self.subTest(labels=labels):
                total = self._value("vauxtra_providers_total", labels)
                self.assertIsNotNone(total)
                self.assertLessEqual(float(value), total)

    def test_every_value_published_is_a_number_prometheus_would_accept(self):
        for family, labels, value in self._samples():
            with self.subTest(family=family, labels=labels):
                float(value)


class TheDocumentedCatalogueIsTheOnePublished(unittest.TestCase):
    """`docs/HOWTO.md` lists the metrics an operator is meant to build dashboards on.

    Nothing compared that list to the endpoint, and it had drifted in almost every row: a
    `state` label on `vauxtra_providers_total` that has never existed, `vauxtra_logs_24h`
    offering `warn` and `debug` but neither `ok` nor `warning`, a family spelt
    `vauxtra_webhook_deliveries_total` where the code says `delivery`, a label called
    `event` carrying `up` and `down` where the body says `status` with `ok` and `error`,
    and two whole families absent from the page. Each one is a query that quietly returns
    nothing, for a reason the operator cannot see from either side.

    The fixture above was extended until every family the endpoint can publish appears in
    one body, so the two can be compared in both directions rather than spot-checked.
    """

    TABLE_HEADER = "| Metric | Labels | Description |"

    @classmethod
    def setUpClass(cls):
        # unittest orders classes alphabetically and this one sorts before the class that
        # builds the body, so the fixture may not have run yet; pytest takes them in file
        # order, where it has. Build it here only if nobody has.
        owner = TheEndpointIsReadBackAsPrometheusWouldReadIt
        if not getattr(owner, "body", None):
            owner.setUpClass()
        cls.body = owner.body

        here = os.path.dirname(os.path.abspath(__file__))
        doc_path = os.path.join(here, os.pardir, "docs", "HOWTO.md")
        with open(doc_path, encoding="utf-8") as fh:
            doc = fh.read()
        start = doc.index(cls.TABLE_HEADER)
        rows = []
        for line in doc[start:].splitlines()[2:]:
            if not line.startswith("|"):
                break
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
        cls.rows = rows

    @staticmethod
    def _ticked(text):
        """Everything the page put in backticks, in order -- its markup for an identifier."""
        return re.findall(r"`([^`]+)`", text)

    def _documented(self):
        """Family -> the set of label names the table claims it carries."""
        out = {}
        for metric_cell, labels_cell, _ in self.rows:
            family = self._ticked(metric_cell)[0]
            if "*(none)*" in labels_cell:
                out[family] = set()
            else:
                out[family] = set(self._ticked(labels_cell.split("(")[0]))
        return out

    def _published(self):
        """Family -> the set of label names actually written, read back off the body."""
        out = {}
        for line in self.body.splitlines():
            if not line or line.startswith("#"):
                continue
            family, brace, labels = line.rpartition(" ")[0].partition("{")
            out.setdefault(family, set())
            if brace:
                out[family] |= {pair.split("=")[0] for pair in labels[:-1].split(",")}
        return out

    def test_the_table_has_a_row_for_every_family_published(self):
        documented = self._documented()
        for family in sorted(self._published()):
            with self.subTest(family=family):
                self.assertIn(family, documented)

    def test_every_family_the_table_names_is_really_published(self):
        published = self._published()
        for family in sorted(self._documented()):
            with self.subTest(family=family):
                self.assertIn(family, published)

    def test_the_labels_named_in_the_table_are_the_labels_carried(self):
        published = self._published()
        for family, names in sorted(self._documented().items()):
            with self.subTest(family=family):
                self.assertEqual(names, published.get(family, set()))

    #: A labels cell ending in an ellipsis is open by design: a provider type is whatever
    #: providers exist, and a log level is whatever the column holds. Every other cell
    #: promises a closed list, and a closed list that is wrong is how `up` and `down`
    #: outlived a body that has only ever said `ok` and `error`.
    OPEN_VOCABULARY = "\u2026"

    def _values_published(self):
        """(family, label name) -> the set of values that label takes in the body."""
        out = {}
        for line in self.body.splitlines():
            if not line or line.startswith("#") or "{" not in line:
                continue
            family, _, labels = line.rpartition(" ")[0].partition("{")
            for pair in labels[:-1].split(","):
                name, _, value = pair.partition("=")
                out.setdefault((family, name), set()).add(value.strip('"'))
        return out

    def test_a_closed_vocabulary_lists_exactly_the_values_published(self):
        published = self._values_published()
        checked = 0
        for metric_cell, labels_cell, _ in self.rows:
            if self.OPEN_VOCABULARY in labels_cell or "(" not in labels_cell:
                continue
            if "*(none)*" in labels_cell:
                continue
            family = self._ticked(metric_cell)[0]
            name = self._ticked(labels_cell.split("(")[0])[0]
            documented = set(self._ticked(labels_cell.split("(", 1)[1]))
            with self.subTest(family=family, label=name):
                self.assertEqual(documented, published.get((family, name), set()))
            checked += 1
        # Every row skipped is a row this test did not check, and a filter that quietly
        # matched everything would pass in silence. Four rows carry a closed list today;
        # `vauxtra_webhook_delivery_total` left that side of the line when its three
        # statuses became a floor the endpoint zero-fills rather than the whole vocabulary.
        self.assertGreaterEqual(checked, 4)

    #: A family whose cell is open still has a floor: the values the endpoint zero-fills,
    #: named in the cell ahead of the ellipsis. Nothing compared those to the constants the
    #: code fills from, and the open-ended reading of the cell would excuse the difference
    #: -- so the cell could name a value the endpoint never publishes and read as correct.
    ZERO_FILLED = {
        "vauxtra_logs_24h": "LOG_LEVELS",
        "vauxtra_webhook_delivery_total": "WEBHOOK_DELIVERY_STATUSES",
    }

    def test_an_open_cell_still_names_the_floor_the_endpoint_zero_fills(self):
        from app import models

        seen = 0
        for metric_cell, labels_cell, _ in self.rows:
            constant = self.ZERO_FILLED.get(self._ticked(metric_cell)[0])
            if constant is None:
                continue
            with self.subTest(constant=constant):
                self.assertIn(self.OPEN_VOCABULARY, labels_cell)
                named = self._ticked(labels_cell.split("(", 1)[1])
                self.assertEqual(named, list(getattr(models, constant)))
            seen += 1
        self.assertEqual(seen, len(self.ZERO_FILLED))

def _scrape_against(seed):
    """`/metrics` read off a database of this test's own, with `seed` run on it first.

    The class above builds one body and every test reads it, which is the right shape for
    "is the catalogue the one published" and the wrong one for "what does this family do
    when the table is empty" -- that needs a body per condition.
    """
    import shutil

    import app.db as _app_db
    from app import models

    tmpdir = tempfile.mkdtemp()
    orig = (models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH)
    models.DATA_DIR = _app_db.DATA_DIR = tmpdir
    models.DB_PATH = _app_db.DB_PATH = os.path.join(tmpdir, "test.db")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-metrics")
    try:
        models.init_db()
        conn = models.get_db()
        try:
            seed(conn)
            conn.commit()
        finally:
            conn.close()

        from app.main import app

        return TestClient(app, raise_server_exceptions=True).get("/metrics").text
    finally:
        models.DATA_DIR, models.DB_PATH, _app_db.DATA_DIR, _app_db.DB_PATH = orig
        shutil.rmtree(tmpdir, ignore_errors=True)


def _value_in(body, family, labels):
    """One sample line's value, parsed the way `_samples` above parses them."""
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        head, _, value = line.rpartition(" ")
        published, brace, carried = head.partition("{")
        if published == family and (carried[:-1] if brace else "") == labels:
            return float(value)
    return None


def _no_deliveries(conn):
    """Seed nothing: the state of an instance that has never sent a webhook."""


def _one_delivery(conn, status):
    """One delivery row, with the webhook its foreign key needs standing behind it."""
    webhook_id = conn.execute(
        "INSERT INTO webhooks (name, url, enabled) VALUES ('w', 'u', 1)"
    ).lastrowid
    conn.execute(
        "INSERT INTO webhook_delivery_log (webhook_id, url, title, body, status, attempt,"
        " error_msg) VALUES (?, 'u', 't', 'b', ?, 1, '')",
        (webhook_id, status),
    )

class TheDeliveryFamilyIsPublishedWhateverTheTableHolds(unittest.TestCase):
    """`vauxtra_webhook_delivery_total` was emitted only `if dlq_rows`, and only for the
    statuses the table happened to hold.

    So the family went absent entirely on an instance that had never sent a webhook, and an
    alarm on failed deliveries read no-data on the instance that had never failed -- the one
    answer it must never give. `docs/HOWTO.md` declared the closed vocabulary `pending,
    delivered, failed` beside it the whole time, a promise kept only where all three
    happened to be present at once; and the fixture two classes up seeds exactly that
    condition, one row per status, which is why the catalogue check was green.

    Each body here is built against a table of this test's own, so nothing below rests on
    that fixture.
    """

    def test_all_three_read_zero_on_an_instance_that_has_never_sent_one(self):
        from app.models import WEBHOOK_DELIVERY_STATUSES

        body = _scrape_against(_no_deliveries)
        for status in WEBHOOK_DELIVERY_STATUSES:
            with self.subTest(status=status):
                self.assertEqual(
                    _value_in(body, "vauxtra_webhook_delivery_total", f'status="{status}"'),
                    0.0,
                )

    def test_the_family_declares_help_and_type_with_nothing_to_count(self):
        body = _scrape_against(_no_deliveries)
        for keyword in ("HELP", "TYPE"):
            with self.subTest(keyword=keyword):
                self.assertIn(f"# {keyword} vauxtra_webhook_delivery_total", body)

    def test_a_status_nothing_in_the_product_writes_is_reported_not_dropped(self):
        body = _scrape_against(lambda conn: _one_delivery(conn, "abandoned"))
        self.assertEqual(
            _value_in(body, "vauxtra_webhook_delivery_total", 'status="abandoned"'), 1.0
        )
        self.assertEqual(
            _value_in(body, "vauxtra_webhook_delivery_total", 'status="failed"'), 0.0
        )

    def test_a_row_with_no_status_gets_a_name_instead_of_an_empty_label(self):
        body = _scrape_against(lambda conn: _one_delivery(conn, ""))
        self.assertEqual(
            _value_in(body, "vauxtra_webhook_delivery_total", 'status="unspecified"'), 1.0
        )
        self.assertIsNone(_value_in(body, "vauxtra_webhook_delivery_total", 'status=""'))

class AFailedQueryIsVisibleRatherThanAFamilyThatQuietlyVanishes(unittest.TestCase):
    """Two sections of `app/api/metrics.py` sat inside `except Exception: pass`.

    They were written in the commit that created `webhook_delivery_log` and
    `service_templates`, when an instance could still predate both tables. `init_db()` has
    created them with `CREATE TABLE IF NOT EXISTS` on every boot since, and runs in the
    lifespan before a scrape can arrive, so what the guard could still catch was a real
    failure -- and what it did with one was drop the series without a word. Zero-filling a
    family that a swallowed error can still make vanish is half a remedy.

    The six sibling sections in that function have never had a guard: a broken query
    surfaces as a 500, which a scrape reads as `up 0` and an operator can see. These two
    now answer the same way.
    """

    def test_a_broken_delivery_query_is_raised_rather_than_swallowed(self):
        with self.assertRaises(sqlite3.OperationalError):
            _scrape_against(lambda conn: conn.execute("DROP TABLE webhook_delivery_log"))

    def test_a_broken_template_query_is_raised_rather_than_swallowed(self):
        with self.assertRaises(sqlite3.OperationalError):
            _scrape_against(lambda conn: conn.execute("DROP TABLE service_templates"))


if __name__ == "__main__":
    unittest.main()
