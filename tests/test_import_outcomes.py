"""What the import route refuses it has to name, and what it passes over it has to count.

`POST /api/services/import` walked its payload with four bare `continue` statements -- a
proxy host with no domain, a name with no dot, a name already tracked, a DNS record with no
address -- and the map its DNS loop reads was built by a comprehension that dropped a
nameless entry, and a second entry for a name already in the map, before the loop even
started. None of the six touched `errors`. The answer was `{"imported": 0, "errors": []}`,
which is also the answer for "there was nothing to do".

The wizard believes that answer. `Setup.tsx` counts `imported`, counts `errors`, and with
both at zero shows no banner of either colour before stepping to "all set". An operator who
ticked `localhost` -- Technitium serves that zone out of the box -- clicked "Import 1 and
finish", saw nothing at all, and landed on an empty dashboard with an empty journal.

Two outcomes were not enough to say what happened, and the first repair made that visible:
routing *everything* through `errors` painted a successful re-import red, because "Quick
import" deliberately sends rows Vauxtra already tracks. So the route answers with four:

  * `imported` -- a new service exists;
  * `linked`   -- an existing service gained the DNS half it was missing, a real write that
                  used to report as `{"imported": 0, "errors": []}`;
  * `skipped`  -- passed over on purpose, nothing is wrong, nothing to fix;
  * `errors`   -- the row is wrong and the operator has somewhere to go.

`imported` and `errors` keep the meaning and the shape they always had, so the wizard and the
settings panel keep working unchanged; this file pins all four, and pins which record wins
when two answer for one name -- the choice the route makes silently otherwise.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import sync as sync_api


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _dns(domain: str, answer: str = "10.0.0.9", provider_id: int = 1, provider_name: str = "Technitium") -> dict:
    """One row of `dns_rewrites`, shaped the way `sync_services` hands it to the panel."""
    return {
        "domain": domain,
        "answer": answer,
        "_provider_id": provider_id,
        "_provider_name": provider_name,
    }


def _host(domain: str, host_id: int = 77) -> dict:
    """One row of `proxy_hosts`, same shape."""
    return {
        "id": host_id,
        "domains": [domain],
        "forward_host": "10.0.0.9",
        "forward_port": 8080,
        "forward_scheme": "http",
        "_provider_id": 2,
        "_provider_name": "NPM",
        "_provider_type": "npm",
    }


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        db_path = os.path.join(self._tmpdir.name, "import.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

        # The route is called in process, so the dependency the router would have run first
        # has to be stood down here.
        patcher = patch.object(sync_api, "require_auth_or_setup", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Three providers, so the foreign keys on `services` hold and so a name can be
        # answered for by two *different* DNS integrations, which is the interesting case.
        conn = models.get_db()
        for pid, name, kind, url in (
            (1, "Technitium", "technitium", "http://dns"),
            (2, "NPM", "npm", "http://npm:81"),
            (3, "AdGuard", "adguard", "http://adguard"),
        ):
            conn.execute(
                """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
                   VALUES (?, ?, ?, ?, 'admin', 'pass', '{}', 1)""",
                (pid, name, kind, url),
            )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _services(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT subdomain, domain FROM services ORDER BY id").fetchall()
        conn.close()
        return [f"{r['subdomain']}.{r['domain']}" for r in rows]

    def _rows(self) -> list[dict]:
        conn = models.get_db()
        rows = conn.execute(
            "SELECT subdomain, domain, target_ip, dns_provider_id, dns_ip FROM services ORDER BY id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _logs(self) -> list[str]:
        conn = models.get_db()
        rows = conn.execute("SELECT message FROM logs ORDER BY id").fetchall()
        conn.close()
        return [r["message"] for r in rows]


class ImportNamesWhatItRefusesTests(_IsolatedDB):
    def test_an_importable_name_still_imports_without_a_word_of_complaint(self) -> None:
        """The witness. A route that refused everything would pass every test below it."""
        result = sync_api.import_services(_request(), {"dns_rewrites": [_dns("essai.vxlab.test")]})

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["linked"], 0)
        self.assertEqual(self._services(), ["essai.vxlab.test"])

    def test_a_single_label_name_is_refused_out_loud(self) -> None:
        result = sync_api.import_services(
            _request(), {"dns_rewrites": [_dns("localhost", "127.0.0.1")]}
        )

        self.assertEqual(result["imported"], 0)
        self.assertEqual(self._services(), [])
        self.assertTrue(any("localhost" in e for e in result["errors"]), result)
        self.assertTrue(any("dot" in e for e in result["errors"]), result)
        self.assertTrue(any("localhost" in m for m in self._logs()), self._logs())

    def test_a_proxy_host_with_a_single_label_name_is_refused_out_loud(self) -> None:
        result = sync_api.import_services(_request(), {"proxy_hosts": [_host("localhost")]})

        self.assertEqual(result["imported"], 0)
        self.assertEqual(self._services(), [])
        self.assertTrue(any("localhost" in e for e in result["errors"]), result)

    def test_a_proxy_host_with_no_domain_at_all_is_named_by_its_id(self) -> None:
        bare = _host("app.vxlab.test", host_id=91)
        bare["domains"] = []

        result = sync_api.import_services(_request(), {"proxy_hosts": [bare]})

        self.assertEqual(result["imported"], 0)
        self.assertTrue(any("91" in e for e in result["errors"]), result)

    def test_a_dns_record_with_no_address_is_named(self) -> None:
        result = sync_api.import_services(
            _request(), {"dns_rewrites": [_dns("vide.vxlab.test", "")]}
        )

        self.assertEqual(result["imported"], 0)
        self.assertTrue(any("vide.vxlab.test" in e for e in result["errors"]), result)

    def test_a_rewrite_with_no_name_is_named_too(self) -> None:
        """This one never reached the loop: the comprehension that built its map dropped it."""
        result = sync_api.import_services(_request(), {"dns_rewrites": [_dns("")]})

        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["errors"]), 1, result)
        self.assertIn("Technitium", result["errors"][0])

    def test_a_row_that_raises_is_named_and_the_run_carries_on(self) -> None:
        """`except Exception` used to append `str(e)` alone, which names neither row nor place.

        The payload below is what the panel sends when a provider reports a port as text; the
        row is unusable, the next one is fine, and the operator needs to be told which is
        which.
        """
        broken = _host("casse.vxlab.test", host_id=42)
        broken["forward_port"] = "not a number"

        result = sync_api.import_services(
            _request(), {"proxy_hosts": [broken, _host("bon.vxlab.test", host_id=43)]}
        )

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._services(), ["bon.vxlab.test"])
        self.assertEqual(len(result["errors"]), 1, result)
        self.assertIn("42", result["errors"][0])
        self.assertTrue(any("42" in m for m in self._logs()), self._logs())


class WhichRecordWinsIsWrittenDownTests(_IsolatedDB):
    """Two integrations can answer for one name. The route keeps one; that choice is pinned.

    Nothing in the repository used to fix it: the map was built by a comprehension, so the
    *last* record won, and the service silently carried that provider and that address into
    every later `push_service`. `sync_services` selects providers with no `ORDER BY`, so
    which record arrives first is not the repository's to promise -- but which of the two
    *arrived* records is retained is, and it is the first.
    """

    def test_the_first_record_is_the_one_kept_and_both_providers_are_named(self) -> None:
        result = sync_api.import_services(
            _request(),
            {
                "dns_rewrites": [
                    _dns("essai.vxlab.test", "10.0.0.9", 1, "Technitium"),
                    _dns("essai.vxlab.test", "10.0.0.10", 3, "AdGuard"),
                ]
            },
        )

        self.assertEqual(result["imported"], 1)
        self.assertEqual(self._services(), ["essai.vxlab.test"])
        # The retained answer, not merely the retained name.
        row = self._rows()[0]
        self.assertEqual(row["dns_provider_id"], 1, row)
        self.assertEqual(row["dns_ip"], "10.0.0.9", row)
        self.assertEqual(row["target_ip"], "10.0.0.9", row)
        # And the operator is told, by name, which two disagree.
        self.assertEqual(len(result["errors"]), 1, result)
        self.assertIn("Technitium", result["errors"][0])
        self.assertIn("AdGuard", result["errors"][0])

    def test_one_provider_holding_the_record_twice_is_not_reported_as_two(self) -> None:
        """"AdGuard and AdGuard both answer" reads as a bug in the message, not in the data."""
        result = sync_api.import_services(
            _request(),
            {
                "dns_rewrites": [
                    _dns("double.vxlab.test", "10.0.0.9", 3, "AdGuard"),
                    _dns("double.vxlab.test", "10.0.0.10", 3, "AdGuard"),
                ]
            },
        )

        self.assertEqual(len(result["errors"]), 1, result)
        self.assertNotIn("AdGuard and AdGuard", result["errors"][0])
        self.assertIn("AdGuard", result["errors"][0])

    def test_a_padded_name_is_the_same_name(self) -> None:
        """Unstripped, " nas.vxlab.test " was a second key, and imported a second service.

        That row matched no scan and pushed nowhere: its subdomain was " nas" and its domain
        "vxlab.test ", so every later lookup by name missed it.
        """
        result = sync_api.import_services(
            _request(),
            {
                "dns_rewrites": [
                    _dns("nas.vxlab.test"),
                    _dns(" nas.vxlab.test "),
                ]
            },
        )

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._services(), ["nas.vxlab.test"])
        self.assertEqual(len(result["errors"]), 1, result)

    def test_no_imported_row_carries_a_blank_at_either_end(self) -> None:
        """Both sides strip, so a padded name on one still pairs with a clean one on the other."""
        result = sync_api.import_services(
            _request(),
            {
                "proxy_hosts": [_host("web.vxlab.test")],
                "dns_rewrites": [_dns(" web.vxlab.test ")],
            },
        )

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(result["errors"], [], result)
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        for field in ("subdomain", "domain"):
            self.assertEqual(rows[0][field], rows[0][field].strip(), rows[0])
        # Paired, not imported twice: the proxy row carries the DNS half.
        self.assertEqual(rows[0]["dns_provider_id"], 1, rows[0])


class ImportCountsThePassesItMakesOnPurposeTests(_IsolatedDB):
    """"Quick import" sends the whole scan back, tracked rows included, and says so first.

    `SyncSection.tsx` counts them out loud in its confirm dialog before it sends. They are the
    nominal case of that button, so a re-import is a success: routing them through `errors`
    painted it red, and logging each one wrote a journal line per tracked service on every
    click. `check_all` in `app/api/services.py` had already settled that second question --
    "One line for the run, not one per service" -- and this follows it.
    """

    def test_a_name_already_tracked_is_set_aside_and_not_called_an_error(self) -> None:
        first = sync_api.import_services(_request(), {"proxy_hosts": [_host("app.vxlab.test")]})
        second = sync_api.import_services(_request(), {"proxy_hosts": [_host("app.vxlab.test")]})

        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["errors"], [], second)
        self.assertEqual(self._services(), ["app.vxlab.test"])
        self.assertTrue(any("app.vxlab.test" in s for s in second["skipped"]), second)

    def test_re_importing_twenty_tracked_hosts_adds_one_journal_line_not_twenty(self) -> None:
        payload = {"proxy_hosts": [_host(f"h{i}.vxlab.test", host_id=i) for i in range(20)]}

        first = sync_api.import_services(_request(), payload)
        after_first = len(self._logs())
        again = sync_api.import_services(_request(), payload)
        added = self._logs()[after_first:]

        self.assertEqual(first["imported"], 20, first)
        self.assertEqual(again["imported"], 0, again)
        self.assertEqual(again["errors"], [], again)
        self.assertEqual(len(again["skipped"]), 20, again)
        self.assertEqual(
            len(added),
            1,
            "a re-import writes one line for the run; twenty tracked services must not each "
            f"write their own: {added}",
        )
        self.assertIn("20", added[0])

    def test_the_spare_names_of_a_multi_domain_host_are_listed(self) -> None:
        """A proxy host may serve several names; a service carries one. The rest get named."""
        many = _host("one.vxlab.test")
        many["domains"] = ["one.vxlab.test", "two.vxlab.test", "three.vxlab.test"]

        result = sync_api.import_services(_request(), {"proxy_hosts": [many]})

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(result["errors"], [], result)
        self.assertEqual(self._services(), ["one.vxlab.test"])
        for spare in ("two.vxlab.test", "three.vxlab.test"):
            self.assertTrue(any(spare in s for s in result["skipped"]), result["skipped"])


class ImportCountsTheLinkItWritesTests(_IsolatedDB):
    """A DNS record landing on a service that already exists is a write, not a no-op.

    It sets `dns_provider_id` and `dns_ip` -- the two columns `push_service` reads next -- and
    wrote its own journal line while answering `{"imported": 0, "errors": []}`, the answer for
    "there was nothing to do". The operator saw no banner and the DNS half had just moved.
    """

    def test_a_dns_record_attaching_to_an_existing_service_is_counted(self) -> None:
        sync_api.import_services(_request(), {"proxy_hosts": [_host("app.vxlab.test")]})

        result = sync_api.import_services(
            _request(), {"dns_rewrites": [_dns("app.vxlab.test", "10.0.0.11", 3, "AdGuard")]}
        )

        self.assertEqual(result["imported"], 0, result)
        self.assertEqual(result["linked"], 1, result)
        self.assertEqual(result["errors"], [], result)
        row = self._rows()[0]
        self.assertEqual(row["dns_provider_id"], 3, row)
        self.assertEqual(row["dns_ip"], "10.0.0.11", row)


class EverySubmittedRowIsAccountedForTests(_IsolatedDB):
    def test_nothing_the_operator_ticked_leaves_without_a_word(self) -> None:
        """The invariant the wizard depends on: every row sent lands in one of the four.

        `Setup.tsx` only calls the route when at least one row is ticked, so an answer that
        counts nothing anywhere is a row the operator watched disappear. The payload below
        holds one of each outcome and nothing that folds, so the sum is an equality.
        """
        sync_api.import_services(_request(), {"proxy_hosts": [_host("app.vxlab.test")]})

        payload = {
            "proxy_hosts": [
                _host("app.vxlab.test"),                  # already tracked -> set aside
                _host("localhost", host_id=78),           # no dot          -> refused
            ],
            "dns_rewrites": [
                _dns("essai.vxlab.test"),                              # new       -> imported
                _dns("localhost", "127.0.0.1"),                        # no dot    -> refused
                _dns("vide.vxlab.test", ""),                           # no answer -> refused
                _dns("app.vxlab.test", "10.0.0.12", 3, "AdGuard"),     # existing  -> linked
            ],
        }
        submitted = len(payload["proxy_hosts"]) + len(payload["dns_rewrites"])

        result = sync_api.import_services(_request(), payload)

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(result["linked"], 1, result)
        self.assertEqual(len(result["skipped"]), 1, result)
        self.assertEqual(len(result["errors"]), 3, result)
        self.assertEqual(
            result["imported"] + result["linked"] + len(result["skipped"]) + len(result["errors"]),
            submitted,
            result,
        )
        for refused in ("localhost", "vide.vxlab.test"):
            self.assertTrue(any(refused in e for e in result["errors"]), result["errors"])

    def test_a_paired_dns_row_is_counted_once_and_not_lost(self) -> None:
        """The one fold, and the reason the sum above is written against a payload without it.

        A DNS record whose name matches a proxy host in the same payload is not a second
        service: it is the other half of the first one, and it lands in `dns_provider_id` and
        `dns_ip` on the row the proxy host created. Two rows in, one service out, counted
        once -- so the totals are short by the number of pairs, deliberately, and a reader
        who sums them is owed that sentence rather than left to discover it.
        """
        payload = {
            "proxy_hosts": [_host("web.vxlab.test")],
            "dns_rewrites": [_dns("web.vxlab.test", "10.0.0.13")],
        }

        result = sync_api.import_services(_request(), payload)

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(result["linked"], 0, result)
        self.assertEqual(result["skipped"], [], result)
        self.assertEqual(result["errors"], [], result)
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["dns_ip"], "10.0.0.13", rows[0])
        # The half that came from the proxy host is still there; the fold added, it did not
        # replace.
        self.assertEqual(rows[0]["target_ip"], "10.0.0.9", rows[0])

    def test_the_two_fields_the_panel_already_reads_keep_their_shape(self) -> None:
        """`linked` and `skipped` are additions; `imported` and `errors` are not redefined.

        `types/api.ts` types the response, `Setup.tsx` and `SyncSection.tsx` branch on those
        two fields, and `vauxtra_mcp` hands the object back as it stands. A rename here is a
        silent break there.
        """
        result = sync_api.import_services(_request(), {"dns_rewrites": [_dns("essai.vxlab.test")]})

        self.assertEqual(sorted(result), ["errors", "imported", "linked", "skipped"])
        self.assertIsInstance(result["imported"], int)
        self.assertIsInstance(result["linked"], int)
        self.assertIsInstance(result["errors"], list)
        self.assertIsInstance(result["skipped"], list)


class _Scanned:
    """A provider that answers a scan with exactly what the test handed it, nothing more."""

    def __init__(self, hosts=(), rewrites=()):
        self._hosts = [dict(h) for h in hosts]
        self._rewrites = [dict(r) for r in rewrites]

    def list_hosts(self):
        return [dict(h) for h in self._hosts]

    def list_rewrites(self):
        return [dict(r) for r in self._rewrites]


class ANameIsTheSameNameInAnyCaseTests(_IsolatedDB):
    """A provider spells a hostname however it likes; a service is stored in one spelling.

    `ServiceIn` lowercases the subdomain and `normalize_domain` lowercases the domain, so
    everything the editor writes is stored in lower case. This route wrote what the provider
    spelled, and compared what the provider spelled -- and nothing downstream ever noticed,
    because `_service_fqdn` lowercases the public hostname it derives. A service stored as
    `NAS.vxlab.test` pushed, and drifted, under `nas.vxlab.test`: the same name as the
    service already tracking it.

    Three things followed, none of them visible. The scan offered the row as new every time.
    Ticking it inserted a second service, because the "already tracks" lookup compared the
    stored spelling and so does the unique index on `(subdomain, domain)` -- so the index
    could not stop it either, and two services pushed over each other. And a proxy host and
    a DNS record for one name, spelled differently by their two providers, stopped pairing:
    two services, each holding half of what one should have held.
    """

    def _scan(self, *, hosts=(), rewrites=()):
        """`POST /api/services/sync`, with the three fixture providers answering."""
        answers = {1: _Scanned(rewrites=rewrites), 2: _Scanned(hosts=hosts), 3: _Scanned()}
        with patch.object(sync_api, "create_provider", lambda row: answers[row["id"]]):
            return sync_api.sync_services(_request())

    def _track(self, subdomain: str, domain: str) -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT INTO services (subdomain, domain, target_ip, target_port) VALUES (?,?,?,80)",
            (subdomain, domain, "10.0.0.9"),
        )
        conn.commit()
        conn.close()

    def test_a_scan_marks_a_tracked_name_spelled_with_a_capital_as_already_imported(self) -> None:
        """The first screen the operator sees. An unticked row is one they never import."""
        self._track("nas", "vxlab.test")

        result = self._scan(rewrites=[{"domain": "NAS.vxlab.test", "answer": "10.0.0.9"}])

        self.assertEqual(len(result["dns_rewrites"]), 1, result)
        self.assertTrue(result["dns_rewrites"][0]["_already_imported"], result["dns_rewrites"][0])

    def test_a_scan_still_calls_an_untracked_name_new(self) -> None:
        """The witness. A marker stuck on "already imported" would pass the test above it."""
        self._track("nas", "vxlab.test")

        result = self._scan(rewrites=[{"domain": "autre.vxlab.test", "answer": "10.0.0.9"}])

        self.assertFalse(result["dns_rewrites"][0]["_already_imported"], result["dns_rewrites"][0])

    def test_a_tracked_name_spelled_with_a_capital_is_not_imported_twice(self) -> None:
        self._track("nas", "vxlab.test")

        result = sync_api.import_services(_request(), {"dns_rewrites": [_dns("NAS.vxlab.test")]})

        self.assertEqual(result["imported"], 0, result)
        self.assertEqual(self._services(), ["nas.vxlab.test"])

    def test_a_proxy_host_and_a_dns_record_pair_across_spellings(self) -> None:
        """One name, two providers, two spellings: one service holding both halves."""
        result = sync_api.import_services(
            _request(),
            {
                "proxy_hosts": [_host("WWW.vxlab.test")],
                "dns_rewrites": [_dns("www.vxlab.test", "10.0.0.13")],
            },
        )

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._services(), ["www.vxlab.test"])
        row = self._rows()[0]
        self.assertEqual(row["dns_ip"], "10.0.0.13", row)
        self.assertEqual(row["target_ip"], "10.0.0.9", row)

    def test_an_imported_name_is_stored_the_way_the_editor_would_store_it(self) -> None:
        """Not cosmetic: the unique index on `(subdomain, domain)` compares these literally."""
        result = sync_api.import_services(_request(), {"dns_rewrites": [_dns("Cave.VXLab.Test")]})

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._services(), ["cave.vxlab.test"])
        conn = models.get_db()
        domains = [r["name"] for r in conn.execute("SELECT name FROM domains").fetchall()]
        conn.close()
        self.assertIn("vxlab.test", domains)

    def test_two_providers_answering_in_two_spellings_are_still_two_answers_for_one_name(self) -> None:
        """The disagreement this route exists to report, hidden by a capital letter."""
        result = sync_api.import_services(
            _request(),
            {
                "dns_rewrites": [
                    _dns("NAS.vxlab.test", "10.0.0.9", 1, "Technitium"),
                    _dns("nas.vxlab.test", "10.0.0.10", 3, "AdGuard"),
                ]
            },
        )

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(self._services(), ["nas.vxlab.test"])
        self.assertEqual(len(result["errors"]), 1, result)
        self.assertIn("Technitium", result["errors"][0])
        self.assertIn("AdGuard", result["errors"][0])

    def test_a_name_already_in_the_right_case_still_imports(self) -> None:
        """The witness for the import half."""
        result = sync_api.import_services(_request(), {"dns_rewrites": [_dns("essai.vxlab.test")]})

        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self._services(), ["essai.vxlab.test"])


if __name__ == "__main__":
    unittest.main()
