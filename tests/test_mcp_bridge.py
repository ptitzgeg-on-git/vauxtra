"""The MCP bridge must be able to read a service back and write it again.

`update_service` and `toggle_service` both did a read-modify-write: GET the service, change
one field, PUT the whole thing back. But the GET serializes the relations as objects under
`tags`/`environments`, while the PUT expects `tag_ids`/`environment_ids` -- which pydantic
filled with their empty defaults, and `set_tags` starts with a DELETE. Every toggle from an
assistant silently stripped a service of its tags and environments.

Two ends are covered here: the bridge now rebuilds the id lists, and `ServiceIn` refuses
unknown keys so the next client that gets this wrong gets a 422 instead of a wiped service.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from starlette.requests import Request

from app import models
from app.api import services as services_api
from app.api.services import ServiceIn
from vauxtra_mcp import server as mcp_server
from vauxtra_mcp.tools.services import _SERVICE_WRITABLE_KEYS, _service_to_payload


def _request(method: str = "PUT", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _NullProvider:
    """Every provider call succeeds and touches nothing."""

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


class McpPayloadTests(unittest.TestCase):
    """`_service_to_payload` alone, against a realistic GET body."""

    GET_BODY = {
        # -- writable ------------------------------------------------------------
        "subdomain": "vault",
        "domain": "example.com",
        "target_ip": "10.0.0.9",
        "target_port": 8080,
        "forward_scheme": "http",
        "websocket": 0,
        "enabled": 1,
        "dns_provider_id": 3,
        "proxy_provider_id": 2,
        "tunnel_provider_id": None,
        "expose_mode": "proxy_dns",
        "public_target_mode": "manual",
        "auto_update_dns": 0,
        "tunnel_hostname": "",
        "dns_ip": "198.51.100.7",
        "icon_url": "",
        "extra_proxy_provider_ids": [5],
        "extra_dns_provider_ids": [],
        # -- read-only ------------------------------------------------------------
        "id": 12,
        "created_at": "2026-01-01 00:00:00",
        "status": "up",
        "last_checked": "2026-01-02 03:04:05",
        "npm_host_id": 77,
        "tags": [{"id": 4, "name": "prod", "color": "red"}, {"id": 7, "name": "web", "color": "blue"}],
        "environments": [{"id": 2, "name": "prod", "color": "green"}],
        "push_targets": [{"role": "proxy", "provider_id": 5}],
        "dns_provider_name": "AdGuard",
        "proxy_type": "npm",
    }

    def test_the_relations_come_back_as_id_lists(self):
        payload = _service_to_payload(self.GET_BODY)
        self.assertEqual(payload["tag_ids"], [4, 7])
        self.assertEqual(payload["environment_ids"], [2])

    def test_the_read_only_keys_are_dropped(self):
        payload = _service_to_payload(self.GET_BODY)
        for key in ("id", "created_at", "status", "last_checked", "npm_host_id",
                    "tags", "environments", "push_targets", "dns_provider_name", "proxy_type"):
            self.assertNotIn(key, payload)

    def test_the_writable_fields_survive_untouched(self):
        payload = _service_to_payload(self.GET_BODY)
        for key in _SERVICE_WRITABLE_KEYS:
            self.assertEqual(payload[key], self.GET_BODY[key], key)

    def test_the_payload_is_accepted_by_the_model_it_is_built_for(self):
        """The point of the allowlist: what comes out has to validate as a ServiceIn."""
        model = ServiceIn(**_service_to_payload(self.GET_BODY))
        self.assertEqual(model.tag_ids, [4, 7])
        self.assertEqual(model.environment_ids, [2])

    def test_a_service_with_no_relations_yields_empty_lists(self):
        body = {**self.GET_BODY, "tags": [], "environments": None}
        payload = _service_to_payload(body)
        self.assertEqual(payload["tag_ids"], [])
        self.assertEqual(payload["environment_ids"], [])

    def test_a_column_the_model_does_not_know_never_reaches_the_put(self):
        """An allowlist, so a column added to the table later cannot start leaking."""
        payload = _service_to_payload({**self.GET_BODY, "some_future_column": "x"})
        self.assertNotIn("some_future_column", payload)


class ServiceInStrictnessTests(unittest.TestCase):
    def test_a_raw_get_body_is_refused_instead_of_silently_truncated(self):
        with self.assertRaises(ValidationError) as ctx:
            ServiceIn(**McpPayloadTests.GET_BODY)
        # The refusal must name the offending keys, otherwise the caller cannot act on it.
        message = str(ctx.exception)
        self.assertIn("tags", message)

    def test_a_typo_in_a_field_name_is_refused(self):
        with self.assertRaises(ValidationError):
            ServiceIn(
                subdomain="app", domain="example.com", target_ip="10.0.0.1",
                target_port=80, enabeld=False,  # noqa: F401  (deliberate typo)
            )


class ServiceUpdateKeepsRelationsTests(unittest.TestCase):
    """End to end: an MCP-shaped payload through the real PUT handler."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.db as _app_db

        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "mcp.test.db")
        models.init_db()

        self._patchers = [
            patch.object(services_api, "require_auth", lambda _req, scope=None: None),
            patch.object(services_api, "create_provider", lambda _row: _NullProvider()),
        ]
        for p in self._patchers:
            p.start()

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (2, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (3, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)"""
        )
        conn.execute("INSERT INTO tags (id, name, color) VALUES (4, 'prod', 'red')")
        conn.execute("INSERT INTO tags (id, name, color) VALUES (7, 'web', 'blue')")
        conn.execute("INSERT INTO environments (id, name, color) VALUES (2, 'production', 'green')")
        cur = conn.execute(
            """INSERT INTO services
                 (subdomain, domain, target_ip, target_port, forward_scheme, websocket,
                  enabled, expose_mode, proxy_provider_id, npm_host_id,
                  dns_provider_id, dns_ip, public_target_mode)
               VALUES ('vault', 'example.com', '10.0.0.9', 8080, 'http', 0, 1, 'proxy_dns',
                       2, 77, 3, '198.51.100.7', 'manual')"""
        )
        self.sid = cur.lastrowid
        conn.execute("INSERT INTO service_tags (service_id, tag_id) VALUES (?,4)", (self.sid,))
        conn.execute("INSERT INTO service_tags (service_id, tag_id) VALUES (?,7)", (self.sid,))
        conn.execute(
            "INSERT INTO service_environments (service_id, environment_id) VALUES (?,2)", (self.sid,)
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        import app.db as _app_db

        for p in reversed(self._patchers):
            p.stop()
        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def _relations(self) -> tuple[list[int], list[int]]:
        conn = models.get_db()
        tags = [r[0] for r in conn.execute(
            "SELECT tag_id FROM service_tags WHERE service_id=? ORDER BY tag_id", (self.sid,))]
        envs = [r[0] for r in conn.execute(
            "SELECT environment_id FROM service_environments WHERE service_id=? ORDER BY environment_id",
            (self.sid,))]
        conn.close()
        return tags, envs

    def test_a_toggle_built_from_a_get_keeps_the_tags(self):
        current = services_api.get_service(self.sid, _request("GET"))
        self.assertEqual([t["id"] for t in current["tags"]], [4, 7])

        payload = {**_service_to_payload(current), "enabled": False}
        services_api.update_service(self.sid, _request(), ServiceIn(**payload))

        self.assertEqual(self._relations(), ([4, 7], [2]))

    def test_the_toggle_still_took_effect(self):
        current = services_api.get_service(self.sid, _request("GET"))
        payload = {**_service_to_payload(current), "enabled": False}
        services_api.update_service(self.sid, _request(), ServiceIn(**payload))

        conn = models.get_db()
        enabled = conn.execute("SELECT enabled FROM services WHERE id=?", (self.sid,)).fetchone()[0]
        conn.close()
        self.assertEqual(enabled, 0)

    def test_dropping_a_single_tag_still_works(self):
        """The fix must not turn the id lists into "always keep everything"."""
        current = services_api.get_service(self.sid, _request("GET"))
        payload = _service_to_payload(current)
        payload["tag_ids"] = [4]
        services_api.update_service(self.sid, _request(), ServiceIn(**payload))

        self.assertEqual(self._relations(), ([4], [2]))


class HttpBindTests(unittest.TestCase):
    """`--http` used to publish an unauthenticated admin bridge on every interface."""

    def _bind(self, env: dict) -> tuple[str, int]:
        with patch.dict(os.environ, env, clear=False):
            for key in ("VAUXTRA_MCP_HOST", "VAUXTRA_MCP_PORT"):
                if key not in env:
                    os.environ.pop(key, None)
            return mcp_server._http_bind()

    def test_the_default_is_loopback(self):
        self.assertEqual(self._bind({}), ("127.0.0.1", 9000))

    def test_publishing_takes_a_deliberate_variable(self):
        self.assertEqual(self._bind({"VAUXTRA_MCP_HOST": "0.0.0.0"})[0], "0.0.0.0")

    def test_an_unusable_port_falls_back_instead_of_crashing(self):
        self.assertEqual(self._bind({"VAUXTRA_MCP_PORT": "not-a-port"})[1], 9000)

    def test_an_empty_host_is_treated_as_unset(self):
        self.assertEqual(self._bind({"VAUXTRA_MCP_HOST": "  "})[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
