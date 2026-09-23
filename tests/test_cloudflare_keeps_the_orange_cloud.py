"""The orange cloud of a Cloudflare record is the operator's, and a tunnel's CNAME needs it.

Every write set the flag from the integration's default, which is off, and every CNAME was
written grey whatever it pointed at. Two defects came out of that one line.

A CNAME to `<tunnel id>.cfargotunnel.com` only answers when it is proxied. Measured in
production on 2026-09-23: a test name Vauxtra had just created toward a tunnel answered a
CNAME and no address through two public resolvers, while the proxied record beside it, same
target, answered two. Creating, renaming, re-enabling or correcting the drift of a tunnel's
name cut it off.

A proxied A record whose address changed came back grey. Nothing said so, and the origin's
address was in public DNS from then on. The push path corrects a drift by removing the
record and adding it back, the scheduler's address update rewrites it in place, a rename
creates the new name: each wrote the default over the operator's choice.
"""

# `_Zone.list` is the SDK's name, and it shadows the builtin in the class's annotations.
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from starlette.requests import Request

from app import models
from app.api import sync as sync_api

TUNNEL = "0f0f0f0f-1111-2222-3333-444444444444.cfargotunnel.com"


def _request(method: str = "GET", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _Zone:
    """One zone held in memory behind the SDK's `dns.records`, answering as Cloudflare does."""

    def __init__(self, *records: tuple[str, str, str, bool]):
        self.records = [
            SimpleNamespace(id=f"r{i}", name=name, type=rtype, content=content, proxied=proxied)
            for i, (name, rtype, content, proxied) in enumerate(records, 1)
        ]
        self.writes: list[str] = []

    def list(self, zone_id, type=None, name=None, **_):
        return [
            r
            for r in self.records
            if (type is None or r.type == type) and (name is None or r.name == name["exact"])
        ]

    def create(self, zone_id, name, type, content, ttl, proxied):
        record = SimpleNamespace(
            id=f"r{len(self.records) + 10}", name=name, type=type, content=content, proxied=proxied
        )
        self.records.append(record)
        self.writes.append("create")
        return record

    def update(self, dns_record_id, zone_id, name, type, content, ttl, proxied):
        record = next(r for r in self.records if r.id == dns_record_id)
        record.name, record.type, record.content, record.proxied = name, type, content, proxied
        self.writes.append("update")
        return record

    def delete(self, dns_record_id, zone_id):
        self.records = [r for r in self.records if r.id != dns_record_id]
        self.writes.append("delete")

    def held(self, name: str) -> list[tuple[str, str, bool]]:
        return [(r.type, r.content, r.proxied) for r in self.records if r.name == name]


def _cloudflare(zone: _Zone, *, proxied: bool = False):
    """A CloudflareProvider on a configured zone, its SDK client a mock around `zone`."""
    mock_module = MagicMock()
    import app.providers.cloudflare as cf_module

    orig = (cf_module._HAS_CF, cf_module._cf)
    cf_module._HAS_CF, cf_module._cf = True, mock_module
    try:
        provider = cf_module.CloudflareProvider(
            "https://api.cloudflare.com/client/v4", "zone123", "test_token", {"proxied": proxied}
        )
    finally:
        cf_module._HAS_CF, cf_module._cf = orig
    provider._client = MagicMock()
    provider._client.dns.records = zone
    return provider


class AnAddressKeepsItsFlagTests(unittest.TestCase):
    def test_a_new_address_keeps_the_orange_cloud(self) -> None:
        zone = _Zone(("app.example.com", "A", "203.0.113.7", True))

        self.assertTrue(_cloudflare(zone).add_rewrite("app.example.com", "203.0.113.8"))

        self.assertEqual(zone.held("app.example.com"), [("A", "203.0.113.8", True)])

    def test_a_grey_record_stays_grey_under_an_orange_default(self) -> None:
        """The integration's default is for a new record, not for one the operator set."""
        zone = _Zone(("app.example.com", "A", "203.0.113.7", False))

        _cloudflare(zone, proxied=True).add_rewrite("app.example.com", "203.0.113.8")

        self.assertEqual(zone.held("app.example.com"), [("A", "203.0.113.8", False)])

    def test_a_new_record_still_takes_the_default(self) -> None:
        for default in (False, True):
            with self.subTest(default=default):
                zone = _Zone()
                _cloudflare(zone, proxied=default).add_rewrite("app.example.com", "203.0.113.7")
                self.assertEqual(zone.held("app.example.com"), [("A", "203.0.113.7", default)])

    def test_a_record_removed_then_put_back_keeps_its_flag(self) -> None:
        """The push path's correction of a drift: remove the stale record, then add."""
        zone = _Zone(("app.example.com", "A", "203.0.113.9", True))
        provider = _cloudflare(zone)

        self.assertTrue(provider.delete_rewrite("app.example.com", "203.0.113.9"))
        self.assertTrue(provider.add_rewrite("app.example.com", "203.0.113.7"))

        self.assertEqual(zone.held("app.example.com"), [("A", "203.0.113.7", True)])

    def test_an_address_flag_never_reaches_a_cname(self) -> None:
        """A proxied CNAME toward another account answers error 1014."""
        zone = _Zone(("app.example.com", "A", "203.0.113.9", True))
        provider = _cloudflare(zone)

        provider.delete_rewrite("app.example.com", "203.0.113.9")
        provider.add_rewrite("app.example.com", "edge.example.net")

        self.assertEqual(zone.held("app.example.com"), [("CNAME", "edge.example.net", False)])


class ARenameCarriesTheFlagTests(unittest.TestCase):
    def test_a_rename_carries_the_orange_cloud(self) -> None:
        zone = _Zone(("old.example.com", "A", "203.0.113.7", True))

        self.assertTrue(
            _cloudflare(zone).update_rewrite(
                "old.example.com", "203.0.113.7", "new.example.com", "203.0.113.7"
            )
        )

        self.assertEqual(zone.held("new.example.com"), [("A", "203.0.113.7", True)])
        self.assertEqual(zone.held("old.example.com"), [])

    def test_a_rename_carries_a_grey_one_under_an_orange_default(self) -> None:
        zone = _Zone(("old.example.com", "A", "203.0.113.7", False))

        _cloudflare(zone, proxied=True).update_rewrite(
            "old.example.com", "203.0.113.7", "new.example.com", "203.0.113.7"
        )

        self.assertEqual(zone.held("new.example.com"), [("A", "203.0.113.7", False)])

    def test_a_rename_from_an_address_to_a_cname_does_not_carry_it(self) -> None:
        zone = _Zone(("old.example.com", "A", "203.0.113.7", True))

        _cloudflare(zone).update_rewrite(
            "old.example.com", "203.0.113.7", "new.example.com", "edge.example.net"
        )

        self.assertEqual(zone.held("new.example.com"), [("CNAME", "edge.example.net", False)])

    def test_a_new_address_under_the_same_name_is_an_update_in_place(self) -> None:
        """The scheduler's address update: one write, and the flag is still there."""
        zone = _Zone(("app.example.com", "A", "203.0.113.7", True))

        _cloudflare(zone).update_rewrite(
            "app.example.com", "203.0.113.7", "app.example.com", "203.0.113.8"
        )

        self.assertEqual(zone.held("app.example.com"), [("A", "203.0.113.8", True)])
        self.assertEqual(zone.writes, ["update"])


class ATunnelCnameIsProxiedTests(unittest.TestCase):
    def test_a_cname_to_a_tunnel_is_created_orange(self) -> None:
        for target in (TUNNEL, TUNNEL.upper() + "."):
            with self.subTest(target=target):
                zone = _Zone()
                self.assertTrue(_cloudflare(zone).add_rewrite("app.example.com", target))
                self.assertEqual(zone.held("app.example.com"), [("CNAME", target, True)])

    def test_a_grey_cname_to_a_tunnel_is_turned_orange(self) -> None:
        zone = _Zone(("app.example.com", "CNAME", TUNNEL, False))

        self.assertTrue(_cloudflare(zone).add_rewrite("app.example.com", TUNNEL))

        self.assertEqual(zone.held("app.example.com"), [("CNAME", TUNNEL, True)])
        self.assertEqual(zone.writes, ["update"])

    def test_an_orange_cname_to_a_tunnel_is_left_alone(self) -> None:
        zone = _Zone(("app.example.com", "CNAME", TUNNEL, True))

        self.assertTrue(_cloudflare(zone).add_rewrite("app.example.com", TUNNEL))

        self.assertEqual(zone.writes, [])

    def test_a_cname_moved_onto_a_tunnel_is_orange(self) -> None:
        zone = _Zone(("app.example.com", "CNAME", "edge.example.net", False))

        _cloudflare(zone).add_rewrite("app.example.com", TUNNEL)

        self.assertEqual(zone.held("app.example.com"), [("CNAME", TUNNEL, True)])

    def test_any_other_cname_is_still_created_grey(self) -> None:
        zone = _Zone()

        _cloudflare(zone, proxied=True).add_rewrite("app.example.com", "edge.example.net")

        self.assertEqual(zone.held("app.example.com"), [("CNAME", "edge.example.net", False)])

    def test_a_name_that_only_contains_the_tunnel_domain_is_not_a_tunnel(self) -> None:
        zone = _Zone()

        _cloudflare(zone).add_rewrite("app.example.com", "cfargotunnel.com.example.net")

        self.assertEqual(
            zone.held("app.example.com"), [("CNAME", "cfargotunnel.com.example.net", False)]
        )


class APushKeepsTheFlagTests(unittest.TestCase):
    """The push route itself, correcting a drift on a proxied record."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "orange.test.db")
        models.init_db()
        self.addCleanup(self._restore)

        self.zone = _Zone(("app.example.com", "A", "198.51.100.9", True))
        provider = _cloudflare(self.zone)
        for p in (
            patch.object(sync_api, "require_auth", lambda _r, scope=None: None),
            patch.object(sync_api, "create_provider", lambda _row: provider),
        ):
            p.start()
            self.addCleanup(p.stop)

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (4, 'Cloudflare', 'cloudflare', 'https://api.cloudflare.com',
                       'zone123', 'token', '{}', 1)"""
        )
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, dns_provider_id, dns_ip, public_target_mode)
               VALUES ('app', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'dns',
                       4, '198.51.100.7', 'manual')""",
        )
        self.sid = cur.lastrowid
        conn.commit()
        conn.close()

    def _restore(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def test_a_push_that_corrects_a_drift_keeps_the_orange_cloud(self) -> None:
        result = sync_api.push_service(self.sid, _request("POST"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.zone.held("app.example.com"), [("A", "198.51.100.7", True)])


if __name__ == "__main__":
    unittest.main()
