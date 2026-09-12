"""A template is a pre-filled service payload, so it has to refuse what a service refuses.

`TemplateIn` validated four of its fourteen fields -- the name, the scheme, the expose mode
and the port -- and let the rest through. The three it skipped are three `ServiceIn`
validates, which made the template the one place in Vauxtra where `public_target_mode` could
be the word "garbage" and `dns_ip` could be a sentence. Nothing refused them at save time;
`GET /api/templates/{id}/apply` handed them back, and the refusal arrived from
`POST /api/services`, about a value typed into a different form on a different day.

The ids were worse, because the two kinds failed in two different ways and neither was
useful:

  - `proxy_provider_id`, `dns_provider_id` and `tunnel_provider_id` are real foreign keys --
    `service_templates` declares `ON DELETE SET NULL` on all three -- so an unknown one
    reached SQLite and came back as `IntegrityError: FOREIGN KEY constraint failed`, which
    FastAPI turns into a 500. The operator filled in a form and was told the server had
    crashed.
  - `tag_ids` lives in `tag_ids_json`, a TEXT column no constraint reaches, so an unknown
    one was stored without complaint and returned for as long as the template existed.

`app/api/services.py` already solved this, for the same reason, with `_unknown_references`;
the templates route simply never called anything like it. It does now, and the tests below
hold both halves: the refusal names the id, and a payload whose ids all exist is still
accepted -- a check that refuses good input is worse than no check.

The third class is about a column rather than a payload. A deleted provider empties its
column, because the foreign key says so. A deleted tag left the id sitting in the JSON, so
the template went on naming a tag nobody could see, and the panel sent it to
`POST /api/services` to be refused for it. Every read now drops those, which makes the two
kinds of reference rot the same way -- quietly -- which is the behaviour the schema already
chose for the providers.

Dropping it on `apply` alone would have been worse than not dropping it: the editor renders
a chip per *existing* tag, so a dead id has no chip to click off, and the refusal added
above would then reject the save for an id the form gave no way to remove. The tests below
hold both ends of that -- the id is gone from the list, from the single template and from
the applied payload, and the column itself is untouched, so restoring the tag restores the
template.
"""

import hashlib
import json
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


class _TemplateBench(unittest.TestCase):
    """One temp database per test, one provider, one tag, and a write-scoped key."""

    HEADERS = {"Authorization": "Bearer key-rw"}

    PROVIDER_ID = 1
    TAG_ID = 1

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)

        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "templates.test.db")
        models.init_db()

        self._patchers = [
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
            patch.object(auth, "APP_PASSWORD", "test-password"),
        ]
        for p in self._patchers:
            p.start()

        self._client_cm = TestClient(app_main.app)
        self.client = self._client_cm.__enter__()

        conn = models.get_db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS api_keys (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
                   prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'read',
                   created_at TEXT NOT NULL DEFAULT (datetime('now')), last_used_at TEXT)"""
        )
        conn.execute(
            "INSERT INTO api_keys (name, key_hash, prefix, scopes) VALUES (?,?,?,?)",
            ("rw", hashlib.sha256(b"key-rw").hexdigest(), "rw", "write"),
        )
        conn.execute(
            "INSERT INTO providers (id, name, type, url, enabled) VALUES (?,?,?,?,1)",
            (self.PROVIDER_ID, "NPM", "npm", "http://10.0.0.2:81"),
        )
        conn.execute("INSERT INTO tags (id, name) VALUES (?,?)", (self.TAG_ID, "prod"))
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # ── instruments ──────────────────────────────────────────────────────────────────────

    def _create(self, **fields):
        fields.setdefault("name", "T")
        return self.client.post("/api/templates", json=fields, headers=self.HEADERS)

    def _update(self, tid: int, **fields):
        fields.setdefault("name", "T")
        return self.client.put(f"/api/templates/{tid}", json=fields, headers=self.HEADERS)

    def _apply(self, tid: int) -> dict:
        r = self.client.get(f"/api/templates/{tid}/apply", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _get(self, tid: int) -> dict:
        r = self.client.get(f"/api/templates/{tid}", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _rows(self) -> int:
        conn = models.get_db()
        try:
            return conn.execute("SELECT COUNT(*) AS n FROM service_templates").fetchone()["n"]
        finally:
            conn.close()

    def _set_tag_column(self, tid: int, raw: str) -> None:
        """Write `tag_ids_json` behind the route's back, which is the only way it gets bad."""
        conn = models.get_db()
        try:
            conn.execute("UPDATE service_templates SET tag_ids_json=? WHERE id=?", (raw, tid))
            conn.commit()
        finally:
            conn.close()


class ATemplateRefusesTheValuesAServiceRefusesTests(_TemplateBench):

    def test_a_public_target_mode_the_service_route_refuses_is_refused_here_too(self):
        r = self._create(public_target_mode="garbage")
        self.assertEqual(r.status_code, 422, r.text)

    def test_a_dns_target_that_is_not_a_hostname_is_refused(self):
        r = self._create(dns_ip="not an address")
        self.assertEqual(r.status_code, 422, r.text)

    def test_a_domain_that_is_not_a_domain_is_refused(self):
        r = self._create(domain="pas de point")
        self.assertEqual(r.status_code, 422, r.text)

    def test_an_empty_domain_and_an_empty_dns_target_stay_allowed(self):
        """The calibration. A template is partial on purpose, or it is a service."""
        r = self._create(domain="", dns_ip="")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["domain"], "")
        self.assertEqual(r.json()["dns_ip"], "")

    def test_the_values_are_normalised_the_way_the_service_route_normalises_them(self):
        r = self._create(domain="MAISON.LAN", dns_ip="NAS.MAISON.LAN", public_target_mode="AUTO")
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["domain"], "maison.lan")
        self.assertEqual(body["dns_ip"], "nas.maison.lan")
        self.assertEqual(body["public_target_mode"], "auto")

    def test_the_update_route_applies_the_same_rules(self):
        tid = self._create().json()["id"]
        self.assertEqual(self._update(tid, public_target_mode="garbage").status_code, 422)
        self.assertEqual(self._update(tid, dns_ip="not an address").status_code, 422)
        self.assertEqual(self._update(tid, domain="pas de point").status_code, 422)


class AnIdThatPointsAtNothingIsNamedTests(_TemplateBench):

    def test_an_unknown_provider_answers_400_and_names_it(self):
        """This was a 500: the foreign key fired inside SQLite, past every readable message."""
        r = self._create(proxy_provider_id=999)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("provider 999", r.json()["detail"])

    def test_an_unknown_tag_answers_400_and_names_it(self):
        """And this was a 201: no constraint reaches a JSON column."""
        r = self._create(tag_ids=[999])
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("tag 999", r.json()["detail"])

    def test_the_refusal_names_every_unknown_id_not_just_the_first(self):
        r = self._create(proxy_provider_id=999, dns_provider_id=998, tag_ids=[997])
        self.assertEqual(r.status_code, 400, r.text)
        detail = r.json()["detail"]
        for named in ("tag 997", "provider 998", "provider 999"):
            self.assertIn(named, detail)

    def test_the_ids_that_do_exist_are_not_named(self):
        r = self._create(proxy_provider_id=self.PROVIDER_ID, dns_provider_id=999)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertNotIn(f"provider {self.PROVIDER_ID}", r.json()["detail"])

    def test_nothing_is_written_when_the_call_is_refused(self):
        self.assertEqual(self._create(tag_ids=[999]).status_code, 400)
        self.assertEqual(self._rows(), 0)

    def test_an_update_naming_an_unknown_id_changes_nothing(self):
        tid = self._create(name="Before").json()["id"]
        r = self._update(tid, name="After", proxy_provider_id=999)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("Nothing was changed", r.json()["detail"])
        self.assertEqual(self._get(tid)["name"], "Before")

    def test_ids_that_all_exist_are_still_accepted(self):
        """A check that refuses sound input is worse than no check at all."""
        r = self._create(
            proxy_provider_id=self.PROVIDER_ID,
            dns_provider_id=self.PROVIDER_ID,
            tunnel_provider_id=self.PROVIDER_ID,
            tag_ids=[self.TAG_ID],
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["tag_ids"], [self.TAG_ID])

    def test_a_template_naming_no_id_at_all_is_still_accepted(self):
        r = self._create(proxy_provider_id=None, dns_provider_id=None, tag_ids=[])
        self.assertEqual(r.status_code, 201, r.text)


class AReferenceThatRotsAfterTheSaveTests(_TemplateBench):

    def _delete_tag(self) -> None:
        conn = models.get_db()
        try:
            conn.execute("DELETE FROM tags WHERE id=?", (self.TAG_ID,))
            conn.commit()
        finally:
            conn.close()

    def test_a_deleted_provider_leaves_the_column_empty(self):
        """Not new behaviour -- `ON DELETE SET NULL` -- but it is the reference the tag now
        follows, so it is held here rather than assumed."""
        tid = self._create(proxy_provider_id=self.PROVIDER_ID).json()["id"]
        conn = models.get_db()
        conn.execute("DELETE FROM providers WHERE id=?", (self.PROVIDER_ID,))
        conn.commit()
        conn.close()
        self.assertIsNone(self._apply(tid)["proxy_provider_id"])

    def test_a_deleted_tag_is_dropped_from_the_applied_payload(self):
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._delete_tag()
        self.assertEqual(self._apply(tid)["tag_ids"], [])

    def test_a_tag_that_still_exists_survives_the_same_call(self):
        """The calibration: the filter must drop the dead id, not the list."""
        conn = models.get_db()
        conn.execute("INSERT INTO tags (id, name) VALUES (2, 'staging')")
        conn.commit()
        conn.close()
        tid = self._create(tag_ids=[self.TAG_ID, 2]).json()["id"]
        self._delete_tag()
        self.assertEqual(self._apply(tid)["tag_ids"], [2])

    def test_a_deleted_tag_is_gone_from_the_editor_too(self):
        """`GET /api/templates/{id}` is what fills the edit form, and the form can only
        deselect a tag that still has a chip. Leaving the id here would leave the operator
        holding a template that opens and can no longer be saved."""
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._delete_tag()
        self.assertEqual(self._get(tid)["tag_ids"], [])

    def test_a_deleted_tag_is_gone_from_the_list_as_well(self):
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._delete_tag()
        listed = self.client.get("/api/templates", headers=self.HEADERS).json()
        self.assertEqual([t["tag_ids"] for t in listed if t["id"] == tid], [[]])

    def test_reopening_the_template_after_the_save_is_accepted(self):
        """The whole point of the two above: the form is loaded from the route and posted
        back unchanged, which must not be refused for something the operator never chose."""
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._delete_tag()
        loaded = self._get(tid)
        r = self._update(tid, name=loaded["name"], tag_ids=loaded["tag_ids"])
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_column_is_not_rewritten_behind_the_operators_back(self):
        """Reads are filtered; the stored row is left alone. A tag deleted by mistake and
        put back by hand therefore comes back to the templates that named it."""
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._delete_tag()
        conn = models.get_db()
        conn.execute("INSERT INTO tags (id, name) VALUES (?, 'prod')", (self.TAG_ID,))
        conn.commit()
        conn.close()
        self.assertEqual(self._get(tid)["tag_ids"], [self.TAG_ID])


class TagIdsAlwaysComeBackAsAListOfIntsTests(_TemplateBench):

    def test_a_column_that_is_not_json_reads_as_no_tags(self):
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._set_tag_column(tid, "{oops")
        self.assertEqual(self._get(tid)["tag_ids"], [])

    def test_a_column_that_is_json_but_not_a_list_reads_as_no_tags(self):
        """`{"a": 1}` parses, so the old `except` never fired and the object reached the
        panel under a name every caller reads as a list of ids."""
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._set_tag_column(tid, json.dumps({"a": 1}))
        self.assertEqual(self._get(tid)["tag_ids"], [])
        self.assertEqual(self._apply(tid)["tag_ids"], [])

    def test_a_list_carrying_something_that_is_not_an_int_keeps_only_the_ints(self):
        """Tag 3 is created first so that what is measured here is the shape filter and not
        the existence filter of the class above -- a missing tag would be dropped either
        way, and the test would pass without proving anything about `"two"` or `True`."""
        conn = models.get_db()
        conn.execute("INSERT INTO tags (id, name) VALUES (3, 'edge')")
        conn.commit()
        conn.close()
        tid = self._create(tag_ids=[self.TAG_ID]).json()["id"]
        self._set_tag_column(tid, json.dumps([1, "two", None, True, 3]))
        self.assertEqual(self._get(tid)["tag_ids"], [1, 3])

    def test_one_unreadable_row_does_not_take_the_whole_list_down(self):
        good = self._create(name="Good", tag_ids=[self.TAG_ID]).json()["id"]
        bad = self._create(name="Bad", tag_ids=[self.TAG_ID]).json()["id"]
        self._set_tag_column(bad, "{oops")
        r = self.client.get("/api/templates", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        by_id = {t["id"]: t["tag_ids"] for t in r.json()}
        self.assertEqual(by_id[good], [self.TAG_ID])
        self.assertEqual(by_id[bad], [])


if __name__ == "__main__":
    unittest.main()
