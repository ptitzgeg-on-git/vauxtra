"""A template stores a tag list and an environment list, and used to store only the first.

The expose wizard offers one label control with two halves. "Save as template" read the tag
half and not the environment half: the body it sent listed `tag_ids` and no
`environment_ids`, `TemplateIn` had no such field to receive one, and `service_templates`
had no column to store it. The route answered 201, and the template came back naming no
environment -- which is what a template where none was chosen looks like, so there was
nothing to see.

Every test below is written over *both* halves rather than over the environment half alone.
A bug whose whole shape was "the second half was maintained by hand beside the first" is not
answered by a second hand-maintained list of tests; `_LABEL_COLUMNS` (`app/api/templates.py`)
is one table and these are one set of assertions run twice, so a third half would be covered
the day it is added.

The round-trip class is the one that would have caught it: a save naming both halves is read
back, applied, and compared against what was sent. Its negative witness saves a template
naming tags only and asserts the environment list comes back empty -- without it, a route
that answered `environment_ids: [1]` to every read would pass the positive test.
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

#: One half of the label control: the payload key, the route that owns the rows, the noun the
#: refusal uses, and the column the rot is left in. The same four names `_LABEL_COLUMNS`
#: carries in `app/api/templates.py`, which is what these tests are checking stays paired.
HALVES = (
    ("tag_ids", "tags", "tag", "tag_ids_json"),
    ("environment_ids", "environments", "environment", "environment_ids_json"),
)

TAG_ID = 1
ENVIRONMENT_ID = 1
PROVIDER_ID = 1

#: What a save naming both halves sends, and what every read of it has to answer.
BOTH = {"tag_ids": [TAG_ID], "environment_ids": [ENVIRONMENT_ID]}


class _Bench(unittest.TestCase):
    """One temp database per test, with one provider, one tag, one environment."""

    HEADERS = {"Authorization": "Bearer key-rw"}

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)

        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "halves.test.db")
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
        self._seed()

    def _seed(self) -> None:
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
            (PROVIDER_ID, "NPM", "npm", "http://10.0.0.2:81"),
        )
        conn.execute("INSERT INTO tags (id, name) VALUES (?,?)", (TAG_ID, "prod"))
        conn.execute(
            "INSERT INTO environments (id, name) VALUES (?,?)", (ENVIRONMENT_ID, "staging")
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    # -- instruments ----------------------------------------------------------------------

    def _create(self, **fields):
        fields.setdefault("name", "T")
        return self.client.post("/api/templates", json=fields, headers=self.HEADERS)

    def _created(self, **fields) -> dict:
        r = self._create(**fields)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _get(self, tid: int) -> dict:
        r = self.client.get(f"/api/templates/{tid}", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _listed(self, tid: int) -> dict:
        r = self.client.get("/api/templates", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return next(t for t in r.json() if t["id"] == tid)

    def _apply(self, tid: int) -> dict:
        r = self.client.get(f"/api/templates/{tid}/apply", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _delete_label(self, route: str, lid: int) -> None:
        r = self.client.delete(f"/api/{route}/{lid}", headers=self.HEADERS)
        self.assertEqual(r.status_code, 200, r.text)

    def _column(self, tid: int, column: str) -> str:
        conn = models.get_db()
        try:
            row = conn.execute(
                f"SELECT {column} AS c FROM service_templates WHERE id=?", (tid,)
            ).fetchone()
            return row["c"]
        finally:
            conn.close()

    def _set_column(self, tid: int, column: str, raw: str) -> None:
        """Write a label column behind the route's back, the only way it gets bad."""
        conn = models.get_db()
        try:
            conn.execute(f"UPDATE service_templates SET {column}=? WHERE id=?", (raw, tid))
            conn.commit()
        finally:
            conn.close()

    def _rows(self) -> int:
        conn = models.get_db()
        try:
            return conn.execute("SELECT COUNT(*) AS n FROM service_templates").fetchone()["n"]
        finally:
            conn.close()


class TheSaveAsTemplateRoundTripKeepsBothHalvesTests(_Bench):
    """The shape the expose panel sends, read back through every reader there is.

    `buildTemplatePayload` (`frontend/src/components/features/expose/ExposeModal.tsx`) names
    every field it sends, and the body below is that list. Sending the whole thing rather
    than the two label keys alone is the point: the key that went missing went missing from
    a body that was otherwise complete, and accepted.
    """

    PANEL_BODY = {
        "name": "From an exposure",
        "description": "",
        "forward_scheme": "https",
        "target_port": 8443,
        "websocket": False,
        "expose_mode": "proxy_dns",
        "proxy_provider_id": PROVIDER_ID,
        "dns_provider_id": None,
        "tunnel_provider_id": None,
        "public_target_mode": "manual",
        "domain": "maison.lan",
        "dns_ip": "10.0.0.9",
        "tag_ids": [TAG_ID],
        "environment_ids": [ENVIRONMENT_ID],
        "icon_url": "",
    }

    def test_the_save_answers_with_both_halves(self) -> None:
        body = self._created(**self.PANEL_BODY)
        for key, _route, _noun, _column in HALVES:
            with self.subTest(half=key):
                self.assertEqual(body[key], self.PANEL_BODY[key])

    def test_every_reader_answers_with_both_halves(self) -> None:
        """One template, three readers. The panel uses all three and they read one row."""
        tid = self._created(**self.PANEL_BODY)["id"]
        for reader, read in (("get", self._get), ("list", self._listed), ("apply", self._apply)):
            answer = read(tid)
            for key, _route, _noun, _column in HALVES:
                with self.subTest(reader=reader, half=key):
                    self.assertEqual(answer[key], self.PANEL_BODY[key])

    def test_a_template_naming_tags_only_names_no_environment(self) -> None:
        """The negative witness.

        A route answering `[1]` to every label key would pass the two tests above without
        storing anything. This one saves a template that names a tag and no environment and
        asserts the second half comes back empty, so the tests above are measuring the row
        rather than a constant.
        """
        tid = self._created(name="Tags only", tag_ids=[TAG_ID])["id"]
        for reader, read in (("get", self._get), ("list", self._listed), ("apply", self._apply)):
            answer = read(tid)
            with self.subTest(reader=reader):
                self.assertEqual(answer["tag_ids"], [TAG_ID])
                self.assertEqual(answer["environment_ids"], [])

    def test_an_update_carries_both_halves_as_well(self) -> None:
        """The other write route. A template is edited more often than it is created."""
        tid = self._created(**self.PANEL_BODY)["id"]
        r = self.client.put(
            f"/api/templates/{tid}",
            json={**self.PANEL_BODY, "tag_ids": [], "environment_ids": [ENVIRONMENT_ID]},
            headers=self.HEADERS,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tag_ids"], [])
        self.assertEqual(r.json()["environment_ids"], [ENVIRONMENT_ID])
        self.assertEqual(self._get(tid)["environment_ids"], [ENVIRONMENT_ID])

    def test_a_template_written_before_the_column_existed_reads_as_naming_none(self) -> None:
        """The migration's promise, over a row inserted without the column.

        `environment_ids_json` is added by `ALTER TABLE ... DEFAULT '[]'`, so a template
        saved before this change names no environment -- which is what it named. A row
        written the way the old route wrote it stands in for one.
        """
        conn = models.get_db()
        conn.execute(
            "INSERT INTO service_templates (name, tag_ids_json) VALUES (?,?)",
            ("Older than the column", json.dumps([TAG_ID])),
        )
        conn.commit()
        tid = conn.execute("SELECT id FROM service_templates").fetchone()["id"]
        conn.close()
        self.assertEqual(self._get(tid)["environment_ids"], [])
        self.assertEqual(self._get(tid)["tag_ids"], [TAG_ID])


class EachHalfIsCheckedTheWayTheOtherIsTests(_Bench):
    """The refusal, the rot and the shape guard, run over both halves from one list."""

    def test_an_unknown_id_is_refused_and_named_with_its_own_noun(self) -> None:
        for key, _route, noun, _column in HALVES:
            with self.subTest(half=key):
                r = self._create(name=f"unknown-{key}", **{key: [99]})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn(f"{noun} 99", r.json()["detail"])
                self.assertEqual(self._rows(), 0)

    def test_an_unknown_id_in_either_half_is_named_in_the_same_refusal(self) -> None:
        """Both halves and a provider, so the operator fixes the form once."""
        r = self._create(tag_ids=[99], environment_ids=[98], proxy_provider_id=97)
        self.assertEqual(r.status_code, 400, r.text)
        detail = r.json()["detail"]
        for named in ("tag 99", "environment 98", "provider 97"):
            self.assertIn(named, detail)

    def test_an_id_that_exists_is_never_named(self) -> None:
        """The calibration: a check that refuses good input is worse than no check."""
        r = self._create(**BOTH)
        self.assertEqual(r.status_code, 201, r.text)

    def test_a_deleted_label_is_dropped_from_every_reader(self) -> None:
        """One template naming both halves, each half deleted in turn.

        The second pass opens by asserting the half it has not deleted yet still reads back,
        which is the witness that the first delete took one half and not the template.
        """
        tid = self._created(name="rot", **BOTH)["id"]
        for key, route, _noun, _column in HALVES:
            with self.subTest(half=key):
                self.assertEqual(self._get(tid)[key], [1])
                self._delete_label(route, 1)
                for reader, read in (
                    ("get", self._get),
                    ("list", self._listed),
                    ("apply", self._apply),
                ):
                    with self.subTest(reader=reader):
                        self.assertEqual(read(tid)[key], [])

    def test_the_column_keeps_the_dead_id_so_putting_the_label_back_restores_it(self) -> None:
        """The drop is a read, not a rewrite, in both halves alike."""
        tid = self._created(name="restored", **BOTH)["id"]
        for key, route, _noun, column in HALVES:
            with self.subTest(half=key):
                self._delete_label(route, 1)
                self.assertEqual(self._get(tid)[key], [])
                self.assertEqual(json.loads(self._column(tid, column)), [1])
        conn = models.get_db()
        conn.execute("INSERT INTO tags (id, name) VALUES (?,?)", (TAG_ID, "prod"))
        conn.execute(
            "INSERT INTO environments (id, name) VALUES (?,?)", (ENVIRONMENT_ID, "staging")
        )
        conn.commit()
        conn.close()
        for key, _route, _noun, _column in HALVES:
            with self.subTest(half=key, restored=True):
                self.assertEqual(self._get(tid)[key], [1])

    def test_a_column_that_is_not_json_reads_as_naming_nothing(self) -> None:
        """One bad row must not take `GET /api/templates` down, in either column."""
        tid = self._created(name="bad", **BOTH)["id"]
        for key, _route, _noun, column in HALVES:
            with self.subTest(half=key):
                self._set_column(tid, column, "not json at all")
                self.assertEqual(self._get(tid)[key], [])
                self.assertEqual(self._listed(tid)[key], [])
                self.assertEqual(self._apply(tid)[key], [])

    def test_a_column_that_is_json_but_not_a_list_reads_as_naming_nothing(self) -> None:
        tid = self._created(name="shaped", **BOTH)["id"]
        for key, _route, _noun, column in HALVES:
            with self.subTest(half=key):
                self._set_column(tid, column, '{"a": 1}')
                self.assertEqual(self._get(tid)[key], [])

    def test_a_list_holding_something_that_is_not_an_int_keeps_only_the_ints(self) -> None:
        """Booleans included: `True` is an `int` in Python and is not a label id."""
        tid = self._created(name="mixed", **BOTH)["id"]
        for key, _route, _noun, column in HALVES:
            with self.subTest(half=key):
                self._set_column(tid, column, json.dumps([1, "1", None, True, 2]))
                self.assertEqual(self._get(tid)[key], [1])


class AKeyTheModelDoesNotKnowIsRefusedTests(_Bench):
    """`extra="forbid"`, the rule that makes the next dropped field a failed save.

    A client that reads a service back and writes it again sends the serialized relations
    (`tags`, `environments`), not the id lists this model expects. `ServiceIn` has refused
    that shape for exactly this reason; `TemplateIn` used to ignore it, which is how a key
    the panel really did send could have gone nowhere in silence.
    """

    def test_the_serialized_relation_shape_is_refused_rather_than_ignored(self) -> None:
        for sent in ("tags", "environments"):
            with self.subTest(key=sent):
                r = self._create(name=f"relation-{sent}", **{sent: [{"id": 1, "name": "x"}]})
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(self._rows(), 0)

    def test_a_misspelt_label_key_is_refused_rather_than_defaulted(self) -> None:
        r = self._create(name="misspelt", environment_id=[ENVIRONMENT_ID])
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self._rows(), 0)

    def test_the_keys_the_model_does_know_are_still_accepted(self) -> None:
        """The calibration for the rule above."""
        r = self._create(name="known", **BOTH)
        self.assertEqual(r.status_code, 201, r.text)


if __name__ == "__main__":
    unittest.main()
