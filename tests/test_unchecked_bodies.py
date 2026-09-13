"""A body value of the wrong type answered 500, and a missing one deleted the alert rules.

Seven write routes read their body as a plain `dict` and reached straight for what they
wanted. `.strip()` on a number, `int()` on a word and `.get()` on a string all raise, nothing
catches any of them, and the caller read `500 Internal Server Error` for a mistake in their
own payload. Sixteen distinct ones were measured before this file existed.

The panel produces none of them: it types its own state and clamps its own numbers before it
posts. What does reach these routes unshaped is `vauxtra_mcp/tools/admin.py`, where
`create_webhook`, `update_webhook`, `test_webhook_url`, `set_service_alerts`, `add_domain`,
`create_environment` and `update_environment` pass their arguments on as JSON, and the thing
composing that JSON is a language model.

`POST /api/services/{sid}/alerts` is the one that cost something. It replaces every rule of
the service, and it read its list with `body.get("alerts", [])`: a body with that one key
misspelled, or an empty body, deleted every alert rule on the service and answered
`{"ok": True}`. An entry inside the list that had lost its `webhook_id` was skipped by the
same reasoning, and skipping it after the DELETE is not "not added", it is "removed". Both
are silent, and silent in the direction nobody hears about: the service stops alerting and
goes on looking configured.

Every test here drives the real app over HTTP. Handing the route function a model instance
would prove nothing -- pydantic is what is being measured, and it only runs when FastAPI
parses a request.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as db
import app.main as app_main
import app.scheduler as scheduler
from app import models


class _OverHttp(unittest.TestCase):
    """The app on its own database, answered through the stack that parses the body."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)

        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "bodies.test.db")
        models.init_db()

        self._patches = [
            patch.object(auth, "APP_PASSWORD", ""),
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
        ]
        for p in self._patches:
            p.start()

        # `raise_server_exceptions=False` so an unhandled exception arrives here as the 500 a
        # real caller would read, instead of unwinding into the test and being reported as an
        # error. What is measured is the status a client sees.
        self._client_cm = TestClient(app_main.app, raise_server_exceptions=False)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patches):
            p.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # ---------------------------------------------------------------------------- fixtures

    def _service(self, subdomain: str = "app") -> int:
        conn = models.get_db()
        try:
            cur = conn.execute(
                """INSERT INTO services (subdomain, domain, target_ip, target_port, enabled)
                   VALUES (?, 'example.com', '10.0.0.5', 8080, 1)""",
                (subdomain,),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _webhook(self, name: str = "on-call") -> int:
        resp = self.client.post(
            "/api/webhooks", json={"name": name, "url": "json://hook.test/x"}
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["id"]

    def _rules(self, sid: int) -> list[dict]:
        resp = self.client.get(f"/api/services/{sid}/alerts")
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()


class WebhookCreationTests(_OverHttp):
    """`POST /api/webhooks` read six fields off a dict and trusted every one of them."""

    def test_a_value_of_the_wrong_type_is_refused_and_not_a_server_error(self) -> None:
        wrong = [
            ("name", None),
            ("name", 5),
            ("url", 5),
            ("min_down_minutes", "soon"),
            ("min_down_minutes", [1]),
            ("repeat_interval_minutes", "hourly"),
        ]
        for field, value in wrong:
            with self.subTest(field=field, value=value):
                body = {"name": "on-call", "url": "json://hook.test/x", field: value}
                resp = self.client.post("/api/webhooks", json=body)
                self.assertEqual(resp.status_code, 422, resp.text)
                self.assertIn(field, resp.text)

    def test_the_sentences_written_by_hand_are_the_ones_still_answered(self) -> None:
        """The model carries types. Every refusal the route already spelled out stays its."""
        empty = self.client.post("/api/webhooks", json={"name": "  ", "url": "json://h.test/x"})
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["detail"], "Name and URL are required")

        no_url = self.client.post("/api/webhooks", json={"name": "on-call", "url": ""})
        self.assertEqual(no_url.status_code, 400)
        self.assertEqual(no_url.json()["detail"], "URL is required")

        unknown = self.client.post(
            "/api/webhooks",
            json={
                "name": "on-call",
                "url": "json://hook.test/x",
                "scope_type": "service",
                "scope_ref_id": 4040,
            },
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertIn("unknown service 4040", unknown.json()["detail"])

        word = self.client.post(
            "/api/webhooks",
            json={"name": "on-call", "url": "json://hook.test/x", "scope_type": "everything"},
        )
        self.assertEqual(word.status_code, 400)
        self.assertEqual(word.json()["detail"], "Invalid scope type")

    def test_a_scope_target_that_is_not_a_number_is_still_the_400_it_was(self) -> None:
        """`scope_ref_id` stays untyped so `_normalize_scope` keeps answering for it."""
        for value in ("abc", [7], {"id": 7}):
            with self.subTest(value=value):
                resp = self.client.post(
                    "/api/webhooks",
                    json={
                        "name": "on-call",
                        "url": "json://hook.test/x",
                        "scope_type": "service",
                        "scope_ref_id": value,
                    },
                )
                self.assertEqual(resp.status_code, 400, resp.text)
                self.assertEqual(resp.json()["detail"], "Invalid scope target")

    def test_a_well_formed_body_is_still_stored(self) -> None:
        """The witness: the model refuses what is wrong and nothing that is right."""
        sid = self._service()
        resp = self.client.post(
            "/api/webhooks",
            json={
                "name": "on-call",
                "url": "json://hook.test/x",
                "scope_type": "service",
                "scope_ref_id": sid,
                "min_down_minutes": 5,
                "repeat_interval_minutes": 30,
                "alert_on_any_down": True,
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        stored = resp.json()
        self.assertEqual(stored["scope_ref_id"], sid)
        self.assertEqual(stored["min_down_minutes"], 5)
        self.assertEqual(stored["repeat_interval_minutes"], 30)
        self.assertEqual(stored["alert_on_any_down"], 1)


class WebhookUpdateTests(_OverHttp):
    """`PUT /api/webhooks/{wid}` fills what the caller left out from the stored row."""

    def test_a_value_of_the_wrong_type_is_refused_and_not_a_server_error(self) -> None:
        wid = self._webhook()
        for field, value in (("name", 5), ("url", 5), ("min_down_minutes", "soon")):
            with self.subTest(field=field, value=value):
                resp = self.client.put(f"/api/webhooks/{wid}", json={field: value})
                self.assertEqual(resp.status_code, 422, resp.text)
                self.assertIn(field, resp.text)

    def test_a_partial_update_still_keeps_every_field_it_did_not_send(self) -> None:
        """`exclude_unset` asks the same question the dict's `.get(name, existing)` asked."""
        created = self.client.post(
            "/api/webhooks",
            json={
                "name": "on-call",
                "url": "json://hook.test/x",
                "min_down_minutes": 7,
                "alert_on_any_down": True,
            },
        ).json()

        toggled = self.client.put(f"/api/webhooks/{created['id']}", json={"enabled": False})
        self.assertEqual(toggled.status_code, 200, toggled.text)

        rows = self.client.get("/api/webhooks").json()
        row = next(r for r in rows if r["id"] == created["id"])
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["name"], "on-call")
        self.assertEqual(row["min_down_minutes"], 7)
        self.assertEqual(row["alert_on_any_down"], 1)
        self.assertEqual(row["url_masked"], "json://***")

    def test_a_row_that_is_gone_is_still_404_before_anything_else(self) -> None:
        resp = self.client.put("/api/webhooks/999999", json={"enabled": False})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "Webhook not found")


class WebhookUrlTestTests(_OverHttp):
    """`POST /api/webhooks/test-url` fires a request without storing anything."""

    def test_a_url_that_is_not_a_string_is_refused_and_not_a_server_error(self) -> None:
        resp = self.client.post("/api/webhooks/test-url", json={"url": 5})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("url", resp.text)

    def test_an_empty_url_is_still_the_sentence_it_was(self) -> None:
        resp = self.client.post("/api/webhooks/test-url", json={"url": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "URL is required")

    def test_a_real_url_still_reaches_apprise(self) -> None:
        """The witness: nothing above this line stops a request that was well formed."""
        with patch("apprise.Apprise.notify", return_value=True):
            resp = self.client.post(
                "/api/webhooks/test-url", json={"url": "json://hook.test/x"}
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["ok"])


class ServiceAlertReplacementTests(_OverHttp):
    """The route replaces every rule of the service, so anything it skips, it removes."""

    def _armed(self, sid: int) -> int:
        """One service with one rule on it, which every test below then tries to lose."""
        wid = self._webhook()
        resp = self.client.post(
            f"/api/services/{sid}/alerts",
            json={"alerts": [{"webhook_id": wid, "min_down_minutes": 3}]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(self._rules(sid)), 1)
        return wid

    def test_a_body_without_the_list_does_not_clear_the_rules(self) -> None:
        """`{}` used to delete every rule and answer ok. So did a misspelled key."""
        sid = self._service()
        self._armed(sid)

        for body in ({}, {"alert": []}, {"rules": []}):
            with self.subTest(body=body):
                resp = self.client.post(f"/api/services/{sid}/alerts", json=body)
                self.assertEqual(resp.status_code, 422, resp.text)
                self.assertIn("alerts", resp.text)
                self.assertEqual(len(self._rules(sid)), 1)

    def test_a_list_that_is_not_a_list_does_not_clear_the_rules(self) -> None:
        sid = self._service()
        self._armed(sid)

        for value in ("all", 5, {"webhook_id": 1}):
            with self.subTest(value=value):
                resp = self.client.post(f"/api/services/{sid}/alerts", json={"alerts": value})
                self.assertEqual(resp.status_code, 422, resp.text)
                self.assertEqual(len(self._rules(sid)), 1)

    def test_an_entry_without_a_webhook_id_is_refused_instead_of_skipped(self) -> None:
        """Skipping it after the DELETE is not "not added", it is "removed"."""
        sid = self._service()
        wid = self._armed(sid)
        other = self._webhook("pager")

        resp = self.client.post(
            f"/api/services/{sid}/alerts",
            json={"alerts": [{"webhook_id": other}, {"on_up": True}]},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("webhook_id", resp.text)

        rules = self._rules(sid)
        self.assertEqual([r["webhook_id"] for r in rules], [wid])

    def test_an_id_naming_no_row_is_answered_before_the_deletion(self) -> None:
        """Both columns are foreign keys, and both used to be left to the index to report."""
        sid = self._service()
        wid = self._armed(sid)

        unknown = self.client.post(
            f"/api/services/{sid}/alerts",
            json={"alerts": [{"webhook_id": wid}, {"webhook_id": 4040}]},
        )
        self.assertEqual(unknown.status_code, 400, unknown.text)
        self.assertEqual(
            unknown.json()["detail"], "Nothing to alert through -- unknown webhook 4040"
        )
        self.assertEqual([r["webhook_id"] for r in self._rules(sid)], [wid])

        gone = self.client.post(
            "/api/services/999999/alerts", json={"alerts": [{"webhook_id": wid}]}
        )
        self.assertEqual(gone.status_code, 404, gone.text)
        self.assertEqual(gone.json()["detail"], "Service not found")

    def test_every_unknown_webhook_is_named_at_once(self) -> None:
        """Two wrong ids are two lines to fix, not two requests to make."""
        sid = self._service()
        self._armed(sid)

        resp = self.client.post(
            f"/api/services/{sid}/alerts",
            json={"alerts": [{"webhook_id": 4041}, {"webhook_id": 4040}]},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(
            resp.json()["detail"], "Nothing to alert through -- unknown webhook 4040, 4041"
        )

    def test_clearing_the_rules_is_still_one_request(self) -> None:
        """The witness: an empty list means empty, and says so. `{}` no longer can."""
        sid = self._service()
        self._armed(sid)

        resp = self.client.post(f"/api/services/{sid}/alerts", json={"alerts": []})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(self._rules(sid), [])

    def test_a_well_formed_list_is_still_stored_and_still_clamped(self) -> None:
        """The witness: the rules the panel sends go in, and a negative wait is still zero."""
        sid = self._service()
        wid = self._webhook()
        other = self._webhook("pager")

        resp = self.client.post(
            f"/api/services/{sid}/alerts",
            json={
                "alerts": [
                    {"webhook_id": wid, "on_up": False, "min_down_minutes": 15},
                    {"webhook_id": other, "min_down_minutes": -5},
                ]
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        rules = {r["webhook_id"]: r for r in self._rules(sid)}
        self.assertEqual(set(rules), {wid, other})
        self.assertEqual(rules[wid]["on_up"], 0)
        self.assertEqual(rules[wid]["on_down"], 1)
        self.assertEqual(rules[wid]["min_down_minutes"], 15)
        self.assertEqual(rules[other]["min_down_minutes"], 0)


class EnvironmentBodyTests(_OverHttp):
    """Both environment writes read the name through `.strip()` without checking it first."""

    def test_a_name_that_is_not_a_string_is_refused_and_not_a_server_error(self) -> None:
        created = self.client.post("/api/environments", json={"name": "staging"}).json()
        for value in (5, ["prod"], None):
            with self.subTest(value=value):
                post = self.client.post("/api/environments", json={"name": value})
                self.assertEqual(post.status_code, 422, post.text)
                put = self.client.put(
                    f"/api/environments/{created['id']}", json={"name": value}
                )
                self.assertEqual(put.status_code, 422, put.text)

        self.assertEqual(
            self.client.get("/api/environments").json()[0]["name"], "staging"
        )

    def test_the_sentences_the_route_already_wrote_are_unchanged(self) -> None:
        empty = self.client.post("/api/environments", json={"name": "   "})
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["detail"], "Name is required")

        long_name = self.client.post("/api/environments", json={"name": "x" * 33})
        self.assertEqual(long_name.status_code, 422)
        self.assertIn("Name too long", long_name.text)

    def test_a_colour_that_is_not_one_still_falls_back_to_blue(self) -> None:
        """The witness: the one field that was never an error is still not one."""
        resp = self.client.post(
            "/api/environments", json={"name": "staging", "color": "not-a-colour"}
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["color"], "blue")


class DomainBodyTests(_OverHttp):
    """`POST /api/domains` normalises the name, which needed a name to normalise."""

    def test_a_name_that_is_not_a_string_is_refused_and_not_a_server_error(self) -> None:
        for value in (5, ["example.com"]):
            with self.subTest(value=value):
                resp = self.client.post("/api/domains", json={"name": value})
                self.assertEqual(resp.status_code, 422, resp.text)

    def test_a_malformed_domain_is_still_the_sentence_it_was(self) -> None:
        resp = self.client.post("/api/domains", json={"name": "invalid"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid domain name:", resp.json()["detail"])

    def test_a_real_domain_is_still_stored_normalised(self) -> None:
        """The witness: the type check refuses a number and nothing a caller would mean."""
        resp = self.client.post("/api/domains", json={"name": "Example.COM"})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["name"], "example.com")
        self.assertIn("example.com", self.client.get("/api/domains").json())


class ProviderUpdateTests(_OverHttp):
    """`PUT /api/providers/{pid}` took two values the create route refuses in so many words."""

    def _provider(self, name: str = "NPM maison") -> int:
        resp = self.client.post(
            "/api/providers",
            json={
                "name": name,
                "type": "npm",
                "url": "http://10.0.0.5:81",
                "username": "ops@example.com",
                "password": "secret",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["id"]

    def _row(self, pid: int):
        conn = models.get_db()
        try:
            return conn.execute(
                "SELECT name, url, username, password, enabled FROM providers WHERE id=?", (pid,)
            ).fetchone()
        finally:
            conn.close()

    def test_the_update_refuses_a_blank_name_in_the_words_the_create_uses(self) -> None:
        """Two spellings of the same empty answer: one was kept, the other erased the name."""
        pid = self._provider()

        created = self.client.post(
            "/api/providers", json={"name": "   ", "type": "npm", "url": "http://10.0.0.5:81"}
        )
        self.assertEqual(created.status_code, 422)
        self.assertIn("Name is required", created.text)

        for value in ("   ", "", "\t"):
            with self.subTest(value=value):
                resp = self.client.put(f"/api/providers/{pid}", json={"name": value})
                self.assertEqual(resp.status_code, 422, resp.text)
                self.assertIn("Name is required", resp.text)
                self.assertEqual(self._row(pid)["name"], "NPM maison")

    def test_a_flag_that_is_not_one_is_refused_instead_of_stored(self) -> None:
        """Ten queries read `WHERE enabled=1`, so anything else is a provider used by nothing."""
        pid = self._provider()

        resp = self.client.put(f"/api/providers/{pid}", json={"enabled": 7})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(self._row(pid)["enabled"], 1)

        conn = models.get_db()
        try:
            matched = conn.execute(
                "SELECT COUNT(*) FROM providers WHERE enabled=1"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(matched, 1)

    def test_a_row_left_out_of_range_is_put_back_by_the_next_save(self) -> None:
        """A provider stored as `7` before this version, saved again without the flag."""
        pid = self._provider()
        conn = models.get_db()
        try:
            conn.execute("UPDATE providers SET enabled=7 WHERE id=?", (pid,))
            conn.commit()
        finally:
            conn.close()

        resp = self.client.put(f"/api/providers/{pid}", json={"username": "sre@example.com"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._row(pid)["enabled"], 1)

    def test_the_toggle_the_panel_sends_still_works_both_ways(self) -> None:
        """The witness: `Providers.tsx` sends `enabled ? 1 : 0`, which is what a bool means."""
        pid = self._provider()

        off = self.client.put(f"/api/providers/{pid}", json={"enabled": 0})
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(self._row(pid)["enabled"], 0)

        on = self.client.put(f"/api/providers/{pid}", json={"enabled": 1})
        self.assertEqual(on.status_code, 200, on.text)
        self.assertEqual(self._row(pid)["enabled"], 1)

        for value in (True, False):
            with self.subTest(value=value):
                resp = self.client.put(f"/api/providers/{pid}", json={"enabled": value})
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertEqual(self._row(pid)["enabled"], int(value))

    def test_a_partial_update_still_keeps_the_name_and_the_stored_password(self) -> None:
        """The witness: the toggle sends only `enabled`, and must not cost the credentials."""
        pid = self._provider()
        before = self._row(pid)

        resp = self.client.put(f"/api/providers/{pid}", json={"enabled": 0})
        self.assertEqual(resp.status_code, 200, resp.text)

        after = self._row(pid)
        self.assertEqual(after["name"], before["name"])
        self.assertEqual(after["url"], before["url"])
        self.assertEqual(after["username"], before["username"])
        self.assertEqual(after["password"], before["password"])

    def test_a_real_rename_is_still_a_rename(self) -> None:
        """The witness: refusing the blank one refuses nothing an operator would mean."""
        pid = self._provider()
        resp = self.client.put(f"/api/providers/{pid}", json={"name": "  NPM Bruz  "})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._row(pid)["name"], "NPM Bruz")


if __name__ == "__main__":
    unittest.main()
