"""A refusal from the far end is not a crash on this end.

Two routes ask a notification target to accept a message, six ask a DNS or a proxy provider
to read or write a record, and two ask a Docker daemon for its containers. When that far end
refused -- an expired token, a host that never answers, a socket that was never mounted, a
generic apprise scheme whose destination drops the packet -- Vauxtra answered 500. A 500
says "I broke", so the interface said the server had a problem and the operator went looking
through Vauxtra's own journal for a fault that was never theirs.

502 is the code for "I asked someone else, and what came back was not usable". 503 will not
do: it says *this* server is unavailable, which is the same false accusation written in
another number, and 504 would claim a timeout that neither apprise's bare `False` nor a
provider exception lets us tell apart from a flat refusal.

`app/api/docker.py` is the file that shows what happens when the rule is only written down
in two places. It gave one situation two more numbers of its own -- 503 when `ping` failed,
500 when the listing broke -- so the same "the daemon did not answer" reached three
different screens as three different accusations. Both are 502 now, and this file measures
them next to the eight that already were.

What must stay 500 is guarded just as hard here: apprise or docker not installed is our own
missing dependency, and `create_provider` failing is our own registry or our own unreadable
secret. The last class reads the AST of every module under `app/` -- not a hand-written list
of three -- so the next route written anywhere cannot quietly go back to blaming us for
somebody else's refusal.

That detector was measured before it was trusted, and it recognised one recidive shape out
of six. It knew a carrier only by the factory that built it, from a list of six names, so an
`httpx.Client` or a `requests.Session` held in a variable, a factory written after the list,
and a call relayed through a second local name all walked past it; and a bare `add`
exemption, meant for apprise's URL parsing, waved through `provider.add(record)` -- a write
to a DNS zone. Every shape now has a snippet the detector must flag, asserted below, and a
second test counts the try blocks the sweep is actually looking at, because a rule that
matches nothing passes an empty sweep for free.
"""

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import models
from app.api import docker as docker_api
from app.api import providers as providers_api
from app.api import webhooks as webhooks_api

_ROOT = Path(__file__).resolve().parents[1]


def _request(method: str = "POST", path: str = "/") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


class _IsolatedDB(unittest.TestCase):
    """Each test gets its own database file. `tests/` is not a package, so this is per-file."""

    def setUp(self) -> None:
        import app.db as _app_db

        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR)
        models.DATA_DIR = _app_db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = _app_db.DB_PATH = os.path.join(self._tmpdir.name, "destination.test.db")
        models.init_db()

    def tearDown(self) -> None:
        import app.db as _app_db

        models.DB_PATH, models.DATA_DIR, _app_db.DB_PATH, _app_db.DATA_DIR = self._orig
        self._tmpdir.cleanup()


# ── The notification target ────────────────────────────────────────────────────────────


class NotificationTargetRefusalTests(_IsolatedDB):
    """`POST /api/webhooks/test-url` and `POST /api/webhooks/{wid}/test`.

    apprise reports a refused send by returning `False` from `notify()` and a broken
    connection by raising. Both used to arrive as 500, so the two screens that call these
    routes accused Vauxtra of a fault that belonged to the URL the operator had just typed.
    """

    URL = "json://collector.lan/hook"

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(webhooks_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

        conn = models.get_db()
        conn.execute(
            "INSERT INTO webhooks (id, name, url, enabled) VALUES (1, 'collector', ?, 1)",
            (self.URL,),
        )
        conn.commit()
        conn.close()

    def _test_url(self) -> None:
        webhooks_api.test_webhook_url(_request(), {"url": self.URL})

    def _test_stored(self) -> None:
        webhooks_api.test_webhook(1, _request())

    def _status_of(self, call) -> int:
        with self.assertRaises(HTTPException) as caught:
            call()
        return caught.exception.status_code

    # -- the destination refused ---------------------------------------------------------

    def test_a_target_that_refuses_the_message_is_not_our_fault(self) -> None:
        with patch("apprise.Apprise.notify", return_value=False):
            self.assertEqual(self._status_of(self._test_url), 502)

    def test_a_stored_target_that_refuses_the_message_is_not_our_fault(self) -> None:
        with patch("apprise.Apprise.notify", return_value=False):
            self.assertEqual(self._status_of(self._test_stored), 502)

    def test_a_target_that_breaks_the_connection_is_not_our_fault(self) -> None:
        with patch("apprise.Apprise.notify", side_effect=OSError("connection refused")):
            self.assertEqual(self._status_of(self._test_url), 502)

    def test_a_stored_target_that_breaks_the_connection_is_not_our_fault(self) -> None:
        with patch("apprise.Apprise.notify", side_effect=OSError("connection refused")):
            self.assertEqual(self._status_of(self._test_stored), 502)

    def test_the_refusal_tells_the_operator_where_to_look(self) -> None:
        """The number reattributes the fault; the sentence has to point somewhere too."""
        with patch("apprise.Apprise.notify", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                self._test_url()
        self.assertIn("target", str(caught.exception.detail).lower())

    # -- and what is still ours -----------------------------------------------------------

    def test_a_missing_package_is_still_our_own_fault(self) -> None:
        """The control that stops the fix from repainting every failure as someone else's.

        `None` in `sys.modules` is what makes `import apprise` raise ImportError without
        uninstalling anything.
        """
        with patch.dict(sys.modules, {"apprise": None}):
            self.assertEqual(self._status_of(self._test_url), 500)
            self.assertEqual(self._status_of(self._test_stored), 500)

    def test_an_unusable_url_is_still_a_400(self) -> None:
        conn = models.get_db()
        conn.execute("UPDATE webhooks SET url='not-a-scheme' WHERE id=1")
        conn.commit()
        conn.close()
        self.assertEqual(self._status_of(self._test_stored), 400)

    # -- the healthy case ------------------------------------------------------------------

    def test_a_target_that_accepts_the_message_still_answers_ok(self) -> None:
        """Green before the fix and green after it: 502 is a reattribution, not a new floor."""
        with patch("apprise.Apprise.notify", return_value=True):
            self.assertEqual(webhooks_api.test_webhook_url(_request(), {"url": self.URL}),
                             {"ok": True})
            self.assertEqual(webhooks_api.test_webhook(1, _request()), {"ok": True})


# ── The DNS and proxy providers ────────────────────────────────────────────────────────


class _RefusingProvider:
    """A provider client whose far end refuses, the way an expired token does.

    The real clients let the exception out of `requests`; all that matters at the route
    boundary is that something crossed it after a request had left the process.
    """

    def __init__(self, error: Exception | None = None):
        self._error = error or RuntimeError("401 Unauthorized")

    def _refuse(self, *_args, **_kwargs):
        raise self._error

    list_rewrites = _refuse
    add_rewrite = _refuse
    delete_rewrite = _refuse
    list_hosts = _refuse
    create_host = _refuse
    delete_host = _refuse


class _WorkingProvider:
    """The same six calls, answered the way a reachable provider answers them."""

    def list_rewrites(self):
        return [{"domain": "app.home.lab", "answer": "10.0.0.5"}]

    def add_rewrite(self, _domain, _answer):
        return True

    def delete_rewrite(self, _domain, _answer):
        return True

    def list_hosts(self):
        return [{"id": 7, "domain_names": ["app.home.lab"]}]

    def create_host(self, domain, ip, port, scheme="http", websocket=False, cert_id=None):
        return {"id": 7, "domain": domain, "ip": ip, "port": port, "scheme": scheme}

    def delete_host(self, _host_id):
        return True


class ProviderRefusalTests(_IsolatedDB):
    """The six record routes of the integration inspector.

    An AdGuard answering 401, an NPM that is not listening, a Cloudflare token whose scope
    was revoked: every one of them arrived in the inspector as the same sentence about
    Vauxtra having a problem, next to a Retry button that could not help.
    """

    DNS_PID = 1
    PROXY_PID = 2

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(providers_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

        conn = models.get_db()
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?, 'AdGuard', 'adguard', 'http://ag', 'admin', 'pass', '{}', 1)""",
            (self.DNS_PID,),
        )
        conn.execute(
            """INSERT INTO providers (id, name, type, url, username, password, extra, enabled)
               VALUES (?, 'NPM', 'npm', 'http://npm:81', 'admin', 'pass', '{}', 1)""",
            (self.PROXY_PID,),
        )
        conn.commit()
        conn.close()

    # -- the six calls, each run against whichever client the test hands over --------------

    def _routes(self) -> dict:
        record = providers_api.DNSRecordIn(domain="app.home.lab", answer="10.0.0.5")
        host = providers_api.ProxyHostIn(
            domain_names=["app.home.lab"], forward_host="10.0.0.5", forward_port=8080
        )
        return {
            "list_dns_records": lambda: providers_api.list_dns_records(
                self.DNS_PID, _request("GET")
            ),
            "create_dns_record": lambda: providers_api.create_dns_record(
                self.DNS_PID, _request(), record
            ),
            "delete_dns_record": lambda: providers_api.delete_dns_record(
                self.DNS_PID, "app.home.lab", _request("DELETE"), answer="10.0.0.5"
            ),
            "list_proxy_hosts": lambda: providers_api.list_proxy_hosts(
                self.PROXY_PID, _request("GET")
            ),
            "create_proxy_host": lambda: providers_api.create_proxy_host(
                self.PROXY_PID, _request(), host
            ),
            "delete_proxy_host": lambda: providers_api.delete_proxy_host(
                self.PROXY_PID, "7", _request("DELETE")
            ),
        }

    def _statuses(self, factory) -> dict:
        out = {}
        for name, call in self._routes().items():
            with patch.object(providers_api, "create_provider", factory):
                try:
                    call()
                except HTTPException as exc:
                    out[name] = exc.status_code
                else:
                    out[name] = 200
        return out

    # -- the destination refused ------------------------------------------------------------

    def test_a_provider_that_refuses_is_not_our_fault(self) -> None:
        statuses = self._statuses(lambda _row: _RefusingProvider())
        self.assertEqual(sorted(set(statuses.values())), [502], statuses)

    def test_a_provider_that_never_answers_is_not_our_fault(self) -> None:
        statuses = self._statuses(lambda _row: _RefusingProvider(TimeoutError("read timed out")))
        self.assertEqual(sorted(set(statuses.values())), [502], statuses)

    def test_the_words_of_the_provider_survive_the_reattribution(self) -> None:
        with patch.object(providers_api, "create_provider", lambda _row: _RefusingProvider()):
            with self.assertRaises(HTTPException) as caught:
                providers_api.list_dns_records(self.DNS_PID, _request("GET"))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("401 Unauthorized", str(caught.exception.detail))

    # -- and what is still ours ---------------------------------------------------------------

    def test_a_provider_type_we_cannot_build_is_still_our_own_fault(self) -> None:
        """The twin control, and the one that keeps `create_provider` out of the try.

        `create_provider` opens no connection: it reads the registry and decrypts a stored
        secret. An unknown type or a secret this instance can no longer read is a fault on
        this side of the wire, so it must not be repainted 502 along with the call itself.
        """

        def _unbuildable(_row):
            raise ValueError("Provider 'mystery' not yet supported")

        statuses = self._statuses(_unbuildable)
        self.assertEqual(sorted(set(statuses.values())), [500], statuses)

    def test_an_unknown_provider_id_is_still_a_404(self) -> None:
        with patch.object(providers_api, "create_provider", lambda _row: _RefusingProvider()):
            with self.assertRaises(HTTPException) as caught:
                providers_api.list_dns_records(999, _request("GET"))
        self.assertEqual(caught.exception.status_code, 404)

    def test_a_provider_without_the_capability_is_still_a_400(self) -> None:
        with patch.object(providers_api, "create_provider", lambda _row: _RefusingProvider()):
            with self.assertRaises(HTTPException) as caught:
                providers_api.list_dns_records(self.PROXY_PID, _request("GET"))
        self.assertEqual(caught.exception.status_code, 400)

    # -- the healthy case -----------------------------------------------------------------------

    def test_a_reachable_provider_still_answers_its_records(self) -> None:
        """Green before the fix and green after it."""
        statuses = self._statuses(lambda _row: _WorkingProvider())
        self.assertEqual(sorted(set(statuses.values())), [200], statuses)

    def test_a_record_the_provider_rejects_is_still_a_400(self) -> None:
        """A `False` from the provider is a rejected record, not a broken conversation."""

        class _Rejecting(_WorkingProvider):
            def add_rewrite(self, _domain, _answer):
                return False

        record = providers_api.DNSRecordIn(domain="app.home.lab", answer="10.0.0.5")
        with patch.object(providers_api, "create_provider", lambda _row: _Rejecting()):
            with self.assertRaises(HTTPException) as caught:
                providers_api.create_dns_record(self.DNS_PID, _request(), record)
        self.assertEqual(caught.exception.status_code, 400)


# ── The container daemon ───────────────────────────────────────────────────────────────


class _RefusingDaemon:
    """A client whose `ping` never answers, the way an unmounted socket behaves."""

    def ping(self):
        raise OSError("[Errno 2] No such file or directory: '/var/run/docker.sock'")


class _Containers:
    def __init__(self, outcome):
        self._outcome = outcome

    def list(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _Daemon:
    """A client that answers `ping` and then either lists or breaks, as `outcome` says."""

    def __init__(self, outcome):
        self.containers = _Containers(outcome)

    def ping(self):
        return True


class _Image:
    tags = ["nas:latest"]
    short_id = "sha256:c0ffee"


class _Container:
    """The attributes `list_docker_containers` reads off one running container."""

    id = "c0ffee1234"
    name = "nas"
    status = "running"
    image = _Image()
    attrs = {
        "Config": {"Labels": {}, "ExposedPorts": {"8080/tcp": {}}},
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "192.168.1.20"}}},
    }


class DockerDaemonRefusalTests(_IsolatedDB):
    """`_docker_client` and `GET /api/docker/containers`.

    The daemon is a far end exactly like a provider or a notification target: it lives
    behind a socket or a `tcp://` host that this instance may or may not reach. Vauxtra
    answered 503 when `ping` failed and 500 when the listing broke, so a single unreachable
    daemon reached the operator as two different accusations, neither of them 502.
    """

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(docker_api, "require_auth", lambda _req, scope=None: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _status_of(self, call) -> int:
        with self.assertRaises(HTTPException) as caught:
            call()
        return caught.exception.status_code

    def _containers(self):
        return docker_api.list_docker_containers(_request("GET"), endpoint_id=None)

    # -- the daemon refused --------------------------------------------------------------

    def test_a_daemon_that_never_answers_is_not_our_fault(self) -> None:
        with patch("docker.DockerClient", lambda base_url=None: _RefusingDaemon()):
            self.assertEqual(
                self._status_of(lambda: docker_api._docker_client("tcp://192.168.1.10:2375")),
                502,
            )

    def test_a_daemon_that_breaks_mid_listing_is_not_our_fault(self) -> None:
        broken = _Daemon(OSError("connection reset by peer"))
        with patch.object(docker_api, "_docker_client", lambda _host=None: broken):
            self.assertEqual(self._status_of(self._containers), 502)

    def test_the_refusal_tells_the_operator_where_to_look(self) -> None:
        """The number reattributes the fault; the sentence has to point somewhere too."""
        with patch("docker.DockerClient", lambda base_url=None: _RefusingDaemon()):
            with self.assertRaises(HTTPException) as caught:
                docker_api._docker_client("tcp://192.168.1.10:2375")
        self.assertIn("Docker host", str(caught.exception.detail))

    # -- and what is still ours ---------------------------------------------------------------

    def test_a_docker_package_that_is_not_installed_is_still_our_own_fault(self) -> None:
        """The twin of apprise above: a dependency that is not there is a broken install."""
        with patch.dict(sys.modules, {"docker": None}):
            self.assertEqual(self._status_of(lambda: docker_api._docker_client()), 500)

    def test_an_unknown_endpoint_id_is_still_a_404(self) -> None:
        self.assertEqual(
            self._status_of(lambda: docker_api.test_docker_endpoint(999, _request())), 404
        )

    def test_a_disabled_endpoint_is_still_a_400(self) -> None:
        conn = models.get_db()
        conn.execute(
            "INSERT INTO docker_endpoints (id, name, docker_host, enabled, is_default) "
            "VALUES (7, 'parked', 'tcp://192.168.1.11:2375', 0, 0)"
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            self._status_of(lambda: docker_api.test_docker_endpoint(7, _request())), 400
        )

    # -- the healthy case -----------------------------------------------------------------------

    def test_a_reachable_daemon_still_lists_its_containers(self) -> None:
        """Green before the fix and green after it."""
        with patch.object(docker_api, "_docker_client", lambda _host=None: _Daemon([_Container()])):
            rows = self._containers()
        self.assertEqual([row["name"] for row in rows], ["nas"])
        self.assertEqual(rows[0]["target_ip"], "192.168.1.20")


# ── The guard ──────────────────────────────────────────────────────────────────────────


def _guarded() -> list[tuple[str, str]]:
    """`(path, source)` for every module under `app/`, discovered rather than listed.

    This used to be a three-entry tuple naming the files the fix touched. The rule it
    enforces is not a property of those three: any route anywhere that wraps a call to
    somebody else and answers 500 or 503 is the same false accusation. A hand-written list
    beside a detector that already passes everywhere is a list nobody has a reason to
    update, so the day the fourth caller is written in a fifth file it sits outside the
    guard and nothing says so. Measured over the whole directory: 0 offenders.
    """
    return [
        (path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted((_ROOT / "app").rglob("*.py"))
    ]


# ── The positive controls ──────────────────────────────────────────────────────────────
#
# One snippet per shape the detector has to flag, each asserted below. A sweep that finds
# nothing is passed for free by a rule that matches nothing, so the sweep over `app/` is
# worth exactly what these controls are worth. Five of the six recidive shapes were
# measured to walk straight past the first version of the rule.

_MUST_BE_FLAGGED = {
    # The one it already caught: the carrier is literally spelled `provider`.
    "a carrier spelled provider": """
def route():
    try:
        records = provider.list_rewrites()
        return records
    except Exception as e:
        raise HTTPException(500, f"Failed to list DNS records: {e}")
""",
    # A client held in a variable instead of called inline. The old rule knew `Apprise` and
    # `DockerClient` by name and nothing else, so the two commonest HTTP clients in this
    # code base built a carrier it could not see.
    "an httpx client held in a variable": """
def route():
    client = httpx.Client(timeout=10)
    try:
        return client.get(url).json()
    except Exception as e:
        raise HTTPException(500, f"Failed to read the zone: {e}")
""",
    "a requests session": """
def route():
    session = requests.Session()
    try:
        return session.post(url, json=body)
    except Exception as e:
        raise HTTPException(500, f"Failed to push the record: {e}")
""",
    # A factory nobody added to the list. This is the shape a hand-written set of names can
    # never cover, which is why the rule reads the last word of the name instead.
    "a client factory the guard was never told about": """
def route():
    zone = _zone_client(host)
    try:
        return zone.list_records()
    except Exception as e:
        raise HTTPException(500, f"Failed to list records: {e}")
""",
    # The call reaches the far end through a second local name. Nothing is built here, so a
    # rule that only watches assignments from a factory sees an ordinary variable.
    "a carrier relayed through a second local name": """
def route():
    target = provider
    try:
        return target.list_rewrites()
    except Exception as e:
        raise HTTPException(500, f"Failed to list rewrites: {e}")
""",
    # A write to a DNS provider, named `add`. The bare `{"add"}` exemption below used to
    # wave this through without a word.
    "a method called add on a real provider": """
def route():
    try:
        provider.add(record)
    except Exception as e:
        raise HTTPException(500, f"Failed to add the record: {e}")
""",
    # Nothing here is spelled `provider` and nothing is named `notify`, so a detector that
    # reads the carrier's *name* walks past it. This is the 500 `list_docker_containers` had.
    "a carrier called anything else": """
def route():
    endpoint = _docker_client(host)
    try:
        return endpoint.containers.list()
    except Exception as e:
        raise HTTPException(500, f"Failed to list Docker containers: {e}")
""",
    # 503 over the same call is the same false accusation in another number, so the rule
    # counts it as one. This is the shape `_docker_client` had.
    "the same accusation written 503": """
def build():
    try:
        client = docker.DockerClient(base_url=host)
        client.ping()
        return client
    except Exception as e:
        raise HTTPException(503, f"Docker daemon unavailable: {e}")
""",
}

# What it must leave alone: our own missing dependency, next to the very same remote call.
_MISSING_PACKAGE = """
def route():
    try:
        import apprise
        a = apprise.Apprise()
        if not a.notify(title="t", body="b"):
            raise HTTPException(502, "The target refused the message.")
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed")
"""

# And our own client construction, which reaches nobody and so earns its 500.
_LOCAL_FAULT = """
def build():
    try:
        return create_provider(row)
    except Exception as e:
        raise HTTPException(500, f"Could not build a client: {e}")
"""


# ── What counts as a carrier ───────────────────────────────────────────────────────────

# Modules whose whole purpose is to talk to another process. A call on one of them leaves
# this one, whether or not anything was assigned first: `requests.request(...)` in
# `app/providers/cloudflare.py` is a carrier with no variable in front of it.
_REMOTE_MODULES = frozenset(
    {"aiohttp", "apprise", "docker", "httpx", "paramiko", "requests", "urllib3", "websockets"}
)

# Constructors and factories reached without their module (`from apprise import Apprise`).
_FACTORY_NAMES = frozenset({"Apprise", "DockerClient", "from_env", "create_provider"})

# The rule that replaces the list, and the reason a new factory needs no entry above: a
# function whose name ends in one of these words hands back a far end. `_docker_client`,
# `_provider_client`, `create_provider`, `TimeoutSession`, `httpx.AsyncClient` all match.
_FACTORY_WORDS = ("client", "session", "provider")

# Deliberately absent from that tuple: `connection`. `get_connection()` in `app/models.py`
# hands back a SQLite handle on a local file, and `conn.execute(...)` reaches nobody.
# Measured with it included: six more try blocks in `app/models.py`, and every `conn.execute`
# in `app/api/` came under a rule about somebody else's refusal. A detector that flags the
# database is a detector nobody will read.

# `Apprise()` is the one factory whose product is not only a carrier: the bag parses URLs
# and instantiates plugins in this process before anything is sent.
_APPRISE_FACTORIES = frozenset({"Apprise"})

# The exemption, and why it is keyed on the carrier and not on the method name alone. It was
# a bare `frozenset({"add"})`, which exempted `add` on *anything*: `provider.add(record)` is
# a write to a DNS zone and was waved through without a word. What genuinely takes no wire is
# `Apprise.add(url)` -- it parses the URL and instantiates the plugin locally, and nothing
# leaves until `notify`, which is why `_validate_apprise_url` may answer for it itself. So
# the exemption is `add` on an apprise bag, and nothing else.
_PARSE_ONLY = {"apprise": frozenset({"add"})}

# The name the first version of this guard knew, kept because `app/api/providers.py` calls
# straight through it without an assignment the walker could read.
_KNOWN_CARRIERS = {"provider": "remote"}


def _root_name(node: ast.AST) -> str | None:
    """The variable an attribute chain hangs off: `client.containers.list` -> `client`."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _callee_name(call: ast.Call) -> str:
    """`f(...)` -> `f`, `mod.f(...)` -> `f`, and `""` for anything with no name at all."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _builds_a_carrier(value: ast.AST) -> str | None:
    """`"apprise"`, `"remote"` or `None`: what this expression builds, if it builds a client.

    Building a client reaches nobody, which is why this is also what tells `_is_remote_call`
    to leave a constructor alone. The 500 over `create_provider(row)` is ours: the registry
    or the secret is what failed, and no packet was ever sent.
    """
    if not isinstance(value, ast.Call):
        return None
    name = _callee_name(value)
    if not name:
        return None
    if name in _APPRISE_FACTORIES:
        return "apprise"
    lowered = name.lower()
    if name in _FACTORY_NAMES or lowered.endswith(_FACTORY_WORDS):
        return "remote"
    return None


def _bound(scope: ast.AST) -> list[tuple[ast.AST, ast.AST]]:
    """Every `(target, value)` pair in `scope`, whatever syntax did the binding.

    `with httpx.Client() as c` and `if (c := _zone_client(h))` bind a carrier exactly as
    `c = ...` does, and a rule that only reads `ast.Assign` is blind to both.
    """
    pairs: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            pairs += [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.NamedExpr) or (
            isinstance(node, ast.AnnAssign) and node.value is not None
        ):
            pairs.append((node.target, node.value))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            pairs += [
                (item.optional_vars, item.context_expr)
                for item in node.items
                if item.optional_vars is not None
            ]
    return pairs


def _bound_names(target: ast.AST) -> list[str]:
    """The plain names a binding target writes to, unpacking `a, b = ...` on the way."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [element.id for element in target.elts if isinstance(element, ast.Name)]
    return []


def _carriers(scope: ast.AST) -> dict[str, str]:
    """Every name in `scope` that holds a client, mapped to what kind of client it is.

    This is what widened the guard. It used to ask whether the variable was *called*
    `provider`, so `endpoint = _docker_client(host)` -- the exact shape of the 500 in
    `list_docker_containers` -- was invisible to it. It then read a fixed list of six
    factory names, which left an `httpx.Client` or a `requests.Session` in a variable, a
    factory written after the list, and `target = provider` all outside the guard. The loop
    runs to a fixed point because a relayed name can be bound above the name it copies.
    """
    found = dict(_KNOWN_CARRIERS)
    pairs = _bound(scope)
    for _ in range(len(pairs) + 1):
        grew = False
        for target, value in pairs:
            kind = _builds_a_carrier(value)
            if kind is None and isinstance(value, ast.Name):
                kind = found.get(value.id)  # the relay: `target = provider`
            if kind is None:
                continue
            for name in _bound_names(target):
                if found.get(name) != kind:
                    found[name] = kind
                    grew = True
        if not grew:
            break
    return found


def _is_remote_call(node: ast.AST, carriers: dict[str, str]) -> bool:
    """A call that leaves this process and waits for somebody else to answer.

    Three ways in: the call sits on a name holding a client, however that name is spelled;
    it is a verb on a module whose job is the wire; or it is `*.notify`, apprise's one
    sending verb, which routes reach without a local variable. Building the client is none
    of those -- that call reaches nobody, and its failure really is ours.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if _builds_a_carrier(node) is not None:
        return False
    root = _root_name(node.func.value)
    kind = carriers.get(root)
    if kind is not None:
        return node.func.attr not in _PARSE_ONLY.get(kind, frozenset())
    if root in _REMOTE_MODULES:
        return True
    return node.func.attr == "notify"


def _blames_us(node: ast.AST) -> bool:
    """A status that accuses this server: 500 says we broke, 503 says we are unavailable."""
    if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
        return False
    func = node.exc.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if name != "HTTPException":
        return False
    first = node.exc.args[0] if node.exc.args else None
    if isinstance(first, ast.Constant) and first.value in (500, 503):
        return True
    return any(
        kw.arg == "status_code"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value in (500, 503)
        for kw in node.exc.keywords
    )


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    """A package that is not installed is ours, and `except ImportError` is how both say so."""
    caught = handler.type
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(isinstance(n, ast.Name) and n.id == "ImportError" for n in names if n is not None)


def _scopes(tree: ast.AST) -> list[ast.AST]:
    """The module, plus each function in it: a carrier is only a carrier where it is bound."""
    found = [tree]
    found += [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return found


def _watched(source: str, label: str = "sample.py") -> list[int]:
    """The line of every try block whose body reaches somebody else.

    These are the blocks `_offenders` then judges. Counting them is what says the sweep has
    a subject at all: a carrier rule that went blind would empty this list and leave the
    sweep below green over an empty question.
    """
    found = []
    tree = ast.parse(source, filename=label)
    for scope in _scopes(tree):
        carriers = _carriers(scope)
        for node in ast.walk(scope):
            if isinstance(node, ast.Try) and any(
                _is_remote_call(sub, carriers) for stmt in node.body for sub in ast.walk(stmt)
            ):
                found.append(node.lineno)
    return sorted(set(found))


def _offenders(source: str, label: str) -> list[str]:
    found = []
    tree = ast.parse(source, filename=label)
    for scope in _scopes(tree):
        carriers = _carriers(scope)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Try):
                continue
            if not any(
                _is_remote_call(sub, carriers) for stmt in node.body for sub in ast.walk(stmt)
            ):
                continue
            judged = list(node.body)
            judged += [
                stmt
                for handler in node.handlers
                if not _catches_import_error(handler)
                for stmt in handler.body
            ]
            for stmt in judged:
                for sub in ast.walk(stmt):
                    if _blames_us(sub):
                        found.append(f"{label}:{sub.lineno}")
    return sorted(set(found))


class NoRouteBlamesUsForSomebodyElsesRefusalTests(unittest.TestCase):
    """Read from the AST rather than line by line.

    A route added next year will copy the block above it; the copy is what this refuses.
    """

    def test_no_module_under_app_blames_us_for_a_call_that_left_the_process(self) -> None:
        offenders = []
        for label, source in _guarded():
            offenders += _offenders(source, label)
        self.assertEqual(offenders, [])

    def test_the_sweep_has_something_to_look_at(self) -> None:
        """The witness for the test above, which a rule matching nothing also passes.

        Measured over `app/`: 48 of its 258 try blocks wrap a call to somebody else, spread
        over 9 files. The floors below sit well under that -- this is here to fail when a
        change makes the carrier rule blind, not to pin a count that every new route moves.
        The three files the fix started from are named because those are the ones whose loss
        would be silent.
        """
        watched = [
            (label, line) for label, source in _guarded() for line in _watched(source, label)
        ]
        files = {label for label, _ in watched}

        self.assertGreaterEqual(len(watched), 25, "the detector stopped seeing remote calls")
        self.assertGreaterEqual(len(files), 5, "the sweep narrowed to a handful of files")
        for rel in ("app/api/webhooks.py", "app/api/providers.py", "app/api/docker.py"):
            self.assertIn(rel, files, f"{rel} fell out of the sweep")

    def test_every_recidive_shape_is_flagged(self) -> None:
        """The positive controls, one per shape the detector claims to catch.

        Measured against the first version of the rule: one of these eight was flagged and
        seven were not. A detector with no positive control proves nothing, because the
        sweep above is green for a rule that matches nothing at all.
        """
        for shape, source in _MUST_BE_FLAGGED.items():
            with self.subTest(shape=shape):
                self.assertEqual(len(_offenders(source, "sample.py")), 1)

    def test_it_leaves_a_missing_package_alone(self) -> None:
        self.assertEqual(_offenders(_MISSING_PACKAGE, "sample.py"), [])

    def test_it_leaves_our_own_client_construction_alone(self) -> None:
        self.assertEqual(_offenders(_LOCAL_FAULT, "sample.py"), [])

    def test_it_leaves_url_parsing_alone(self) -> None:
        """`a.add(url)` is called on an apprise bag and still reaches nobody.

        Without this, widening the rule to follow the carrier would have made
        `_validate_apprise_url` an offender for answering 500 over a URL it only parsed --
        a guard that cries everywhere being no better than one that sees nothing. The
        exemption is `add` *on an apprise bag*: `provider.add(record)` above is a write to a
        DNS zone and is flagged.
        """
        parse_only = '''
def validate(url):
    try:
        import apprise
        a = apprise.Apprise()
        if not a.add(url):
            raise HTTPException(400, "Invalid or unrecognized Apprise URL")
    except ImportError:
        raise HTTPException(500, "Package 'apprise' not installed")
    except Exception as e:
        raise HTTPException(500, str(e))
'''
        self.assertEqual(_offenders(parse_only, "sample.py"), [])

    def test_a_local_database_handle_is_not_a_far_end(self) -> None:
        """The other half of calibration: `conn.execute` is a file, not a wire.

        `get_connection()` in `app/models.py` hands back a SQLite handle, and a rule that
        read `connection` as a client word put every `conn.execute` in `app/api/` under a
        guard about somebody else's refusal.
        """
        local_db = """
def route():
    conn = get_connection()
    try:
        return conn.execute("SELECT 1").fetchall()
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")
"""
        self.assertEqual(_offenders(local_db, "sample.py"), [])


if __name__ == "__main__":
    unittest.main()
