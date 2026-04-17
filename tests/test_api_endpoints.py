import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models
from app.api import docker as docker_api
from app.api import environments as environments_api
from app.api import providers as providers_api
from app.api import services as services_api
from app.api import settings as settings_api
from app.api import sync as sync_api
from app.api import webhooks as webhooks_api


def _request(method: str = "GET", path: str = "/") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
    }
    return Request(scope)


class _FakeDiagnosticsProvider:
    def test_connection(self):
        return True

    def validate_permissions(self, hostname_hint: str = "", write_probe: bool = False):
        return {
            "ok": True,
            "checks": [
                {
                    "name": "token_verify",
                    "ok": True,
                    "detail": f"hint={hostname_hint or 'none'}",
                    "blocking": True,
                }
            ],
            "warnings": [],
        }

    def health_status(self):
        return {
            "ok": True,
            "status": "healthy",
            "connections": 1,
            "clients": 1,
        }


class IsolatedDBTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = models.DB_PATH
        self._orig_data_dir = models.DATA_DIR

        import app.db as _app_db
        self._orig_db_db_path = _app_db.DB_PATH
        self._orig_db_data_dir = _app_db.DATA_DIR

        test_db_path = os.path.join(self._tmpdir.name, "dns_manager.api.test.db")
        models.DATA_DIR = self._tmpdir.name
        models.DB_PATH = test_db_path
        _app_db.DATA_DIR = self._tmpdir.name
        _app_db.DB_PATH = test_db_path
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db
        models.DB_PATH = self._orig_db_path
        models.DATA_DIR = self._orig_data_dir
        _app_db.DB_PATH = self._orig_db_db_path
        _app_db.DATA_DIR = self._orig_db_data_dir
        self._tmpdir.cleanup()


class DomainsApiTests(IsolatedDBTestCase):
    def test_add_list_delete_domain(self) -> None:
        with patch.object(settings_api, "require_auth", lambda _req, scope=None: None):
            with self.assertRaises(HTTPException) as exc:
                settings_api.add_domain(_request("POST", "/api/domains"), {"name": "invalid"})
            self.assertEqual(exc.exception.status_code, 400)

            created = settings_api.add_domain(
                _request("POST", "/api/domains"),
                {"name": "Example.COM"},
            )
            self.assertEqual(created["name"], "example.com")

            listed = settings_api.list_domains(_request("GET", "/api/domains"))
            self.assertIn("example.com", listed)

            deleted = settings_api.delete_domain("example.com", _request("DELETE", "/api/domains/example.com"))
            self.assertTrue(deleted["ok"])

            listed_after = settings_api.list_domains(_request("GET", "/api/domains"))
            self.assertNotIn("example.com", listed_after)


class ProvidersApiTests(IsolatedDBTestCase):
    def test_provider_types_and_unavailable_guard(self) -> None:
        with patch.object(providers_api, "require_auth", lambda _req, scope=None: None):
            types_map = providers_api.list_types(_request("GET", "/api/providers/types"))
            # Traefik and Cloudflare are now available
            self.assertIn("traefik", types_map)
            self.assertTrue(types_map["traefik"]["available"])
            self.assertTrue(types_map["traefik"]["read_only"])
            self.assertIn("cloudflare", types_map)
            self.assertTrue(types_map["cloudflare"]["available"])
            self.assertIn("capabilities", types_map["cloudflare"])
            self.assertTrue(types_map["cloudflare"]["capabilities"]["dns"])
            self.assertTrue(types_map["cloudflare"]["capabilities"]["supports_auto_public_target"])

            # Adding a Traefik provider should succeed (available=True)
            result = providers_api.add_provider(
                _request("POST", "/api/providers"),
                providers_api.ProviderIn(
                    name="Traefik",
                    type="traefik",
                    url="http://traefik.local:8080",
                    username="",
                    password="",
                ),
            )
            self.assertEqual(result["type"], "traefik")

            # Unknown provider type must still be rejected by the Pydantic validator
            import pydantic
            with self.assertRaises(pydantic.ValidationError):
                providers_api.ProviderIn(
                    name="Unknown",
                    type="unknown_type",
                    url="http://host:1234",
                )

    def test_add_provider_encrypts_password(self) -> None:
        with patch.object(providers_api, "require_auth", lambda _req, scope=None: None):
            created = providers_api.add_provider(
                _request("POST", "/api/providers"),
                providers_api.ProviderIn(
                    name="NPM",
                    type="npm",
                    url="http://npm.local:81",
                    username="admin@example.com",
                    password="super-secret",
                ),
            )

            conn = models.get_db()
            row = conn.execute("SELECT * FROM providers WHERE id=?", (created["id"],)).fetchone()
            conn.close()

            self.assertIsNotNone(row)
            self.assertNotEqual(row["password"], "super-secret")
            self.assertTrue(len(row["password"]) > 16)

    def test_provider_validation_endpoints_and_tunnel_health(self) -> None:
        with patch.object(providers_api, "require_auth", lambda _req, scope=None: None), patch.object(
            providers_api,
            "create_provider",
            lambda _row: _FakeDiagnosticsProvider(),
        ):
            draft = providers_api.validate_provider_draft(
                _request("POST", "/api/providers/validate-draft"),
                providers_api.ProviderDraftValidationIn(
                    name="CF",
                    type="cloudflare",
                    url="",
                    username="",
                    password="token",
                    hostname_hint="app.example.com",
                    write_probe=False,
                ),
            )
            self.assertTrue(draft["ok"])
            self.assertEqual(draft["health"]["status"], "healthy")

            created = providers_api.add_provider(
                _request("POST", "/api/providers"),
                providers_api.ProviderIn(
                    name="Tunnel",
                    type="cloudflare_tunnel",
                    url="",
                    username="account-id",
                    password="token",
                    extra={"tunnel_id": "tunnel-123"},
                ),
            )

            validated = providers_api.validate_provider(
                created["id"],
                _request("POST", f"/api/providers/{created['id']}/validate"),
                providers_api.ProviderValidationOptions(hostname_hint="svc.example.com"),
            )
            self.assertTrue(validated["ok"])

            tunnel_health = providers_api.list_tunnel_health(_request("GET", "/api/providers/tunnels/health"))
            self.assertEqual(tunnel_health["total"], 1)
            self.assertEqual(tunnel_health["healthy"], 1)


class ServicesApiTests(IsolatedDBTestCase):
    def test_public_target_suggestion_endpoint(self) -> None:
        with patch.object(services_api, "require_auth", lambda _req, scope=None: None), patch.object(
            services_api,
            "suggest_public_targets",
            lambda _conn, proxy_provider_id=None: {
                "candidates": [{"value": "198.51.100.7", "source": "server_public_ip"}],
                "recommended": "198.51.100.7",
            },
        ):
            data = services_api.suggest_public_target(
                _request("GET", "/api/services/public-target/suggest"),
                proxy_provider_id=1,
            )

            self.assertEqual(data["recommended"], "198.51.100.7")
            self.assertEqual(len(data["candidates"]), 1)

    def test_service_preflight_and_sync_endpoints(self) -> None:
        from app.models import get_db

        conn = get_db()
        conn.execute(
            """INSERT OR IGNORE INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (99, 'TestProxy', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)"""
        )
        conn.commit()
        conn.close()

        with patch.object(services_api, "require_auth", lambda _req, scope=None: None), patch.object(
            services_api,
            "_service_target_reachable",
            lambda _host, _port, timeout=2.0: (True, "reachable"),
        ), patch.object(
            services_api,
            "create_provider",
            lambda _row: type("FakeProvider", (), {"test_connection": lambda self: True})(),
        ), patch.object(sync_api, "require_auth", lambda _req, scope=None: None):
            service_payload = services_api.ServiceIn(
                subdomain="app",
                domain="example.com",
                target_ip="127.0.0.1",
                target_port=8080,
                forward_scheme="http",
                expose_mode="proxy_dns",
                proxy_provider_id=99,
                dns_provider_id=None,
                dns_ip="",
                websocket=False,
                enabled=True,
                public_target_mode="manual",
                auto_update_dns=False,
                tunnel_provider_id=None,
                tunnel_hostname="",
                extra_proxy_provider_ids=[],
                extra_dns_provider_ids=[],
            )

            preflight = services_api.preflight_service(
                _request("POST", "/api/services/preflight"),
                services_api.ServicePreflightIn(**service_payload.model_dump(), service_id=None),
            )
            self.assertTrue(preflight["ok"])

            created = services_api.add_service(_request("POST", "/api/services"), service_payload)
            created_payload = json.loads(created.body.decode("utf-8"))
            service_id = int(created_payload["id"])

            dry_run = sync_api.dry_run_push_service(service_id, _request("POST", f"/api/services/{service_id}/push/dry-run"))
            self.assertIn("ok", dry_run)

            drift = sync_api.service_drift(service_id, _request("GET", f"/api/services/{service_id}/drift"))
            self.assertIn("ok", drift)

            reconcile = sync_api.reconcile_service(service_id, _request("POST", f"/api/services/{service_id}/reconcile"))
            self.assertIn("before", reconcile)
            self.assertIn("after", reconcile)


class WebhooksApiTests(IsolatedDBTestCase):
    def test_service_alert_roundtrip(self) -> None:
        conn = models.get_db()
        conn.execute(
            """INSERT INTO services
               (id, subdomain, domain, target_ip, target_port, enabled, status)
               VALUES (1, 'app', 'example.com', '127.0.0.1', 80, 1, 'ok')"""
        )
        conn.commit()
        conn.close()

        with patch.object(webhooks_api, "require_auth", lambda _req, scope=None: None):
            webhook = webhooks_api.add_webhook(
                _request("POST", "/api/webhooks"),
                {"name": "Ops", "url": "mailto://ops@example.com"},
            )

            result = webhooks_api.set_service_alerts(
                1,
                _request("POST", "/api/services/1/alerts"),
                {
                    "alerts": [
                        {
                            "webhook_id": webhook["id"],
                            "on_up": True,
                            "on_down": True,
                            "min_down_minutes": 3,
                        }
                    ]
                },
            )
            self.assertTrue(result["ok"])

            alerts = webhooks_api.get_service_alerts(1, _request("GET", "/api/services/1/alerts"))
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["min_down_minutes"], 3)
            self.assertEqual(alerts[0]["webhook_name"], "Ops")


class EnvironmentsApiTests(IsolatedDBTestCase):
    def test_environment_validation_and_duplicates(self) -> None:
        with patch.object(environments_api, "require_auth", lambda _req, scope=None: None):
            first = environments_api.add_environment(
                _request("POST", "/api/environments"),
                {"name": "production", "color": "not-a-color"},
            )
            self.assertEqual(first["color"], "blue")

            with self.assertRaises(HTTPException) as exc:
                environments_api.add_environment(
                    _request("POST", "/api/environments"),
                    {"name": "production", "color": "red"},
                )
            self.assertEqual(exc.exception.status_code, 409)


class _FakeImage:
    def __init__(self) -> None:
        self.tags = ["nginx:latest"]
        self.short_id = "sha256:fake"


class _FakeContainer:
    def __init__(self) -> None:
        self.id = "container-1"
        self.name = "web-app"
        self.image = _FakeImage()
        self.status = "running"
        self.attrs = {
            "NetworkSettings": {
                "Ports": {
                    "8080/tcp": [{"HostPort": "8080"}],
                },
                "Networks": {
                    "bridge": {"IPAddress": "172.19.0.12"},
                },
            },
            "Config": {
                "Labels": {
                    "vauxtra.subdomain": "web",
                    "vauxtra.scheme": "http",
                    "vauxtra.websocket": "true",
                }
            },
        }


class _FakeContainersAPI:
    def list(self):
        return [_FakeContainer()]


class _FakeDockerClient:
    def __init__(self) -> None:
        self.containers = _FakeContainersAPI()


class DockerApiTests(IsolatedDBTestCase):
    def test_docker_endpoint_crud_and_default(self) -> None:
        with patch.object(docker_api, "require_auth", lambda _req, scope=None: None):
            before = docker_api.list_docker_endpoints(_request("GET", "/api/docker/endpoints"))
            self.assertGreaterEqual(len(before), 1)
            self.assertTrue(any(bool(ep["is_default"]) for ep in before))

            created = docker_api.add_docker_endpoint(
                _request("POST", "/api/docker/endpoints"),
                docker_api.DockerEndpointIn(
                    name="Lab Docker",
                    docker_host="tcp://10.0.0.20:2375",
                    enabled=True,
                ),
            )
            self.assertEqual(created["name"], "Lab Docker")
            self.assertEqual(created["docker_host"], "tcp://10.0.0.20:2375")

            docker_api.set_default_docker_endpoint(
                created["id"],
                _request("POST", f"/api/docker/endpoints/{created['id']}/default"),
            )
            after_default = docker_api.list_docker_endpoints(_request("GET", "/api/docker/endpoints"))
            new_default = next(ep for ep in after_default if bool(ep["is_default"]))
            self.assertEqual(new_default["id"], created["id"])

            deleted = docker_api.delete_docker_endpoint(
                created["id"],
                _request("DELETE", f"/api/docker/endpoints/{created['id']}"),
            )
            self.assertTrue(deleted["ok"])

            final = docker_api.list_docker_endpoints(_request("GET", "/api/docker/endpoints"))
            self.assertGreaterEqual(len(final), 1)
            self.assertTrue(any(bool(ep["is_default"]) for ep in final))

            # Guard: the last remaining endpoint cannot be deleted.
            with self.assertRaises(HTTPException) as exc:
                docker_api.delete_docker_endpoint(
                    final[0]["id"],
                    _request("DELETE", f"/api/docker/endpoints/{final[0]['id']}"),
                )
            self.assertEqual(exc.exception.status_code, 400)

    def test_docker_test_and_discovery_use_selected_endpoint(self) -> None:
        requested_hosts: list[str | None] = []

        def fake_client(host: str | None = None):
            requested_hosts.append(host)
            return _FakeDockerClient()

        with patch.object(docker_api, "require_auth", lambda _req, scope=None: None), patch.object(docker_api, "_docker_client", fake_client):
            created = docker_api.add_docker_endpoint(
                _request("POST", "/api/docker/endpoints"),
                docker_api.DockerEndpointIn(
                    name="Remote Docker",
                    docker_host="tcp://10.0.0.21:2375",
                    enabled=True,
                ),
            )

            tested = docker_api.test_docker_endpoint(
                created["id"],
                _request("POST", f"/api/docker/endpoints/{created['id']}/test"),
            )
            self.assertTrue(tested["ok"])
            self.assertEqual(tested["containers"], 1)

            containers = docker_api.list_docker_containers(
                _request("GET", "/api/docker/containers"),
                endpoint_id=created["id"],
            )
            self.assertEqual(len(containers), 1)
            first = containers[0]
            self.assertEqual(first["endpoint_id"], created["id"])
            self.assertEqual(first["endpoint_name"], "Remote Docker")
            self.assertEqual(first["target_port"], 8080)
            self.assertEqual(first["target_ip"], "172.19.0.12")
            self.assertTrue(first["websocket"])

            # The selected endpoint host must be used by both test and discovery.
            self.assertIn("tcp://10.0.0.21:2375", requested_hosts)

    def test_import_logs_endpoint_name(self) -> None:
        with patch.object(docker_api, "require_auth", lambda _req, scope=None: None):
            created = docker_api.add_docker_endpoint(
                _request("POST", "/api/docker/endpoints"),
                docker_api.DockerEndpointIn(
                    name="Lab2",
                    docker_host="tcp://10.0.0.22:2375",
                    enabled=True,
                ),
            )

            result = docker_api.import_docker_containers(
                _request("POST", "/api/docker/import"),
                {
                    "endpoint_id": created["id"],
                    "domain": "example.com",
                    "containers": [
                        {
                            "id": "container-2",
                            "name": "api",
                            "subdomain": "api",
                            "target_ip": "10.0.1.50",
                            "target_port": 8081,
                            "forward_scheme": "http",
                            "websocket": False,
                        }
                    ],
                },
            )

            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["skipped"], 0)

            conn = models.get_db()
            service = conn.execute(
                "SELECT id FROM services WHERE subdomain=? AND domain=?",
                ("api", "example.com"),
            ).fetchone()
            self.assertIsNotNone(service)

            log = conn.execute(
                "SELECT message FROM logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()

            self.assertIsNotNone(log)
            self.assertIn("Docker [Lab2] imported", log["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)