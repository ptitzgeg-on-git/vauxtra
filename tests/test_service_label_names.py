"""A label whose name held a comma or a colon came back as a label the base never held.

Both taxonomies are serialized onto a service row the same way, and were read back the same
way. The three service queries in `app/api/services.py` asked SQLite for
`GROUP_CONCAT(DISTINCT t.name || ':' || t.color || ':' || t.id)` and `parse_tags`
(`app/models.py`) took the answer apart on "," and then on ":", keeping every chunk that
came out in exactly three pieces. The name is the first of those pieces, so the name is the
one field that can move the boundaries it is being read between.

Nothing refuses either character. `TagIn` (`app/api/tags.py`) and `EnvironmentIn`
(`app/api/environments.py`) strip the name, refuse it empty and stop it at 32 characters,
and that is the whole rule -- the panel offers a free text field and the API takes it.

Measured on this build before the change, with six tags on one service named `prod`, `a,b`,
`web:prod`, `a,`, `x:` and `zeta`, `GET /api/services` answered with four: `prod`, `b`,
`` (the empty string) and `zeta`. Two names vanished and two came back changed, under the
right id and a name nobody typed -- `b` and the empty one are labels `GET /api/tags` has
never listed. That is the part worth naming: a row that goes missing is visible, and a row
that answers under a borrowed name is not.

The damage stops at the label that caused it. Each label contributes one comma-separated
run, so a name carrying a comma splits its own run and never reaches its neighbour's --
`prod` and `zeta` above came back untouched. `test_the_old_encoding_could_not_carry_these`
pins that reading of the fixture, so the rest of this file is known to be adversarial
rather than merely green.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app import models
from app.api import environments as environments_api
from app.api import services as services_api
from app.api import tags as tags_api
from app.api.services import ServiceIn

#: The two halves of the panel. One component and one input write both.
_KINDS = ("tags", "environments")

#: Names a label is allowed to have and that the removed encoding could not carry back.
#: The plain ones are the witnesses: they were green before and must stay green.
AWKWARD = ("alpha", "a,b", "web:prod", "a,", "x:", ":", ",", "a:b,c:d", "zeta")


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _no_auth(_req, scope=None):
    """Stand in for `require_auth`, which is not what any of this measures."""
    return None


class _NullProvider:
    """Every provider call succeeds and touches nothing, as in `tests/test_mcp_bridge.py`."""

    def create_host(self, *_a, **_k):
        return {"id": 1}

    def update_host(self, *_a, **_k):
        return True

    def delete_host(self, *_a, **_k):
        return True

    def toggle_host(self, *_a, **_k):
        return True

    def add_rewrite(self, *_a, **_k):
        return True

    def delete_rewrite(self, *_a, **_k):
        return True

    def update_rewrite(self, *_a, **_k):
        return True

    def find_best_certificate(self, _domain):
        return None


def _make_label(kind: str, name: str) -> int:
    """Create one label through the route the panel calls, and return its id."""
    if kind == "tags":
        with patch.object(tags_api, "require_auth", _no_auth):
            return tags_api.create_tag(_request("POST"), tags_api.TagIn(name=name))["id"]
    with patch.object(environments_api, "require_auth", _no_auth):
        body = environments_api.EnvironmentIn(name=name)
        return environments_api.add_environment(_request("POST"), body)["id"]


def _stored(kind: str) -> list[dict]:
    """Every label the base holds, in the order its own list route answers in."""
    table = "tags" if kind == "tags" else "environments"
    conn = models.get_db()
    try:
        return [
            {"id": r["id"], "name": r["name"], "color": r["color"]}
            for r in conn.execute(f"SELECT id, name, color FROM {table} ORDER BY name, id")
        ]
    finally:
        conn.close()


def _service(host: str, kind: str = "", label_ids: tuple[int, ...] = ()) -> int:
    """One service, optionally holding every label id given."""
    sub, _, dom = host.partition(".")
    conn = models.get_db()
    try:
        sid = conn.execute(
            "INSERT INTO services (subdomain, domain, target_ip, target_port, enabled) "
            "VALUES (?, ?, '10.0.0.4', 80, 1)",
            (sub, dom),
        ).lastrowid
        link = "service_tags" if kind == "tags" else "service_environments"
        column = "tag_id" if kind == "tags" else "environment_id"
        for lid in label_ids:
            conn.execute(
                f"INSERT INTO {link} (service_id, {column}) VALUES (?, ?)", (sid, lid)
            )
        conn.commit()
        return sid
    finally:
        conn.close()


def _listed(sid: int, kind: str) -> list[dict]:
    """The labels `GET /api/services` puts on one service."""
    with patch.object(services_api, "require_auth", _no_auth):
        rows = services_api.list_services(_request())
    row = next(r for r in rows if r["id"] == sid)
    return row[kind]


def _read(sid: int, kind: str) -> list[dict]:
    """The labels `GET /api/services/{sid}` puts on that service."""
    with patch.object(services_api, "require_auth", _no_auth):
        return services_api.get_service(sid, _request())[kind]


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)

        db_path = os.path.join(self._tmpdir.name, "service.labels.test.db")
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = db_path
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()


class OldEncodingTests(unittest.TestCase):
    """The fixture is adversarial. This measures `AWKWARD`, not the application.

    Nothing here calls Vauxtra. It replays the encoding this change removed over the names
    the rest of the file uses, so a green suite below means those names really are the ones
    that used to break, and not nine spellings of `prod`.
    """

    @staticmethod
    def _round_trip(labels: list[dict]) -> list[dict]:
        """`GROUP_CONCAT(name || ':' || color || ':' || id)`, then `parse_tags`, as they were."""
        raw = ",".join(f"{x['name']}:{x['color']}:{x['id']}" for x in labels)
        out = []
        for chunk in raw.split(","):
            parts = chunk.split(":")
            if len(parts) == 3:
                out.append({"name": parts[0], "color": parts[1], "id": int(parts[2])})
        return out

    def test_the_old_encoding_could_not_carry_these(self) -> None:
        labels = [{"id": i + 1, "name": n, "color": "blue"} for i, n in enumerate(AWKWARD)]
        back = self._round_trip(labels)

        # Nine labels in, five out: four names disappeared without a trace.
        self.assertEqual(len(back), 5)
        # And two of the five answer under a name nobody typed, which is the worse half.
        self.assertEqual({x["name"] for x in back} - set(AWKWARD), {"", "b"})

    def test_the_damage_stopped_at_the_name_that_caused_it(self) -> None:
        """A plain neighbour either side of the awkward ones came back untouched.

        Each label contributed one comma-separated run, so a name carrying a comma split
        its own run and never reached the next label's. Worth stating because the opposite
        would have been the louder bug, and it is not the one that was there.
        """
        labels = [{"id": i + 1, "name": n, "color": "blue"} for i, n in enumerate(AWKWARD)]
        names = {x["name"] for x in self._round_trip(labels)}
        self.assertIn("alpha", names)
        self.assertIn("zeta", names)


class ServiceLabelTests(_IsolatedDB):
    """What a service row says its labels are, against what the label table holds."""

    def _held(self, kind: str, host: str = "") -> tuple[int, tuple[int, ...]]:
        """One service carrying every name in `AWKWARD`, and the ids it was given.

        The hostname is keyed on `kind` because `setUp` runs once per test method and not
        once per subTest: both halves of a loop share one base, and `idx_services_hostname`
        is UNIQUE on (subdomain, domain).
        """
        ids = tuple(_make_label(kind, name) for name in AWKWARD)
        return _service(host or f"api.{kind}.test", kind, ids), ids

    def test_every_name_survives_both_reads(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                sid, _ = self._held(kind)
                self.assertEqual(_listed(sid, kind), _stored(kind))
                self.assertEqual(_read(sid, kind), _stored(kind))

    def test_no_name_appears_that_was_never_typed(self) -> None:
        for kind in _KINDS:
            with self.subTest(kind=kind):
                sid, _ = self._held(kind)
                self.assertEqual({x["name"] for x in _read(sid, kind)}, set(AWKWARD))

    def test_the_labels_come_back_in_name_order(self) -> None:
        """The order `GET /api/tags` and `GET /api/environments` already answer in.

        `GROUP_CONCAT DISTINCT` promised no order at all, so the chips could be reordered
        by an unrelated write. Sorting is ASCII here, which is what SQLite compares.
        """
        for kind in _KINDS:
            with self.subTest(kind=kind):
                sid, _ = self._held(kind)
                self.assertEqual([x["name"] for x in _read(sid, kind)], sorted(AWKWARD))

    def test_a_service_with_no_labels_answers_with_an_empty_list(self) -> None:
        """The `LEFT JOIN` is gone, so an absent label list has to be built, not folded."""
        sid = _service("bare.probe.test")
        for kind in _KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(_listed(sid, kind), [])
                self.assertEqual(_read(sid, kind), [])

    def test_one_service_does_not_borrow_another_s_labels(self) -> None:
        """The list route reads every label in one query, so the keying is worth a witness."""
        for kind in _KINDS:
            with self.subTest(kind=kind):
                mine = _make_label(kind, f"only-{kind}-mine")
                yours = _make_label(kind, f"only-{kind}-yours")
                first = _service(f"first.{kind}.test", kind, (mine,))
                second = _service(f"second.{kind}.test", kind, (yours,))

                self.assertEqual([x["id"] for x in _listed(first, kind)], [mine])
                self.assertEqual([x["id"] for x in _listed(second, kind)], [yours])
                self.assertEqual([x["id"] for x in _read(first, kind)], [mine])

    def test_the_write_route_answers_with_the_same_labels(self) -> None:
        """`PUT /api/services/{sid}` builds its answer from a third copy of that query.

        Three routes read a service back and the encoding lived in all three. Mending only
        the two GETs would leave the panel right until somebody pressed Save, which is the
        moment an operator is watching it.
        """
        for kind in _KINDS:
            with self.subTest(kind=kind):
                conn = models.get_db()
                pid = conn.execute(
                    "INSERT INTO providers (name, type, url, username, password, extra, "
                    "enabled) VALUES (?, 'npm', 'http://npm:81', 'admin', 'pw', '{}', 1)",
                    (f"NPM {kind}",),
                ).lastrowid
                conn.commit()
                conn.close()

                sid, ids = self._held(kind)
                body = ServiceIn(
                    subdomain="api",
                    domain=f"{kind}.test",
                    target_ip="10.0.0.4",
                    target_port=80,
                    proxy_provider_id=pid,
                    tag_ids=list(ids) if kind == "tags" else [],
                    environment_ids=list(ids) if kind == "environments" else [],
                )
                with (
                    patch.object(services_api, "require_auth", _no_auth),
                    patch.object(services_api, "create_provider", lambda _row: _NullProvider()),
                ):
                    answer = services_api.update_service(sid, _request("PUT"), body)

                self.assertEqual(answer[kind], _stored(kind))


if __name__ == "__main__":
    unittest.main()
