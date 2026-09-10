#!/usr/bin/env python
"""Drive a real Vauxtra against the five lab providers, over its own HTTP API.

This is not a unit test with a mocked provider. It boots the actual FastAPI application on
an isolated temporary database, registers the containers from `compose.yaml` as providers,
and then exercises the paths an operator uses: test the connection, publish a service, let
the provider drift out from under Vauxtra, reconcile, delete.

Drift is created the way it happens in the field -- by changing the provider directly,
behind Vauxtra's back -- rather than by editing rows in Vauxtra's database. That is the
only version of the test that can fail for the right reason.

Run:  python lab/harness.py       (nothing to install; uses the repo's own dependencies)
"""

import os
import sys
import tempfile
import traceback
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

from fastapi.testclient import TestClient  # noqa: E402

import app.auth as auth  # noqa: E402
import app.db as db  # noqa: E402
import app.main as app_main  # noqa: E402
import app.scheduler as scheduler  # noqa: E402
from app import models  # noqa: E402
from app.providers.factory import _PROVIDER_REGISTRY, create_provider  # noqa: E402

PW = "vauxtra-lab-pw"

# `vxlab-upstream` rather than 127.0.0.1: the proxies dial the origin from inside their own
# container, where loopback is the proxy itself. The compose network resolves the name.
UPSTREAM_HOST = "vxlab-upstream"
UPSTREAM_PORT = 80
DNS_IP = "10.0.0.99"

PROVIDERS = [
    {"name": "lab-adguard", "type": "adguard", "url": "http://127.0.0.1:3080",
     "username": "admin", "password": PW, "kind": "dns"},
    {"name": "lab-pihole", "type": "pihole", "url": "http://127.0.0.1:3082",
     "username": "", "password": PW, "kind": "dns"},
    {"name": "lab-technitium", "type": "technitium", "url": "http://127.0.0.1:5380",
     "username": "admin", "password": PW, "kind": "dns"},
    {"name": "lab-npm", "type": "npm", "url": "http://127.0.0.1:3081",
     "username": "admin@example.com", "password": PW, "kind": "proxy"},
    {"name": "lab-zoraxy", "type": "zoraxy", "url": "http://127.0.0.1:3083",
     "username": "admin", "password": PW, "kind": "proxy"},
]

RESULTS: list[tuple[str, str, str, str]] = []


def record(phase: str, name: str, ok: bool | None, detail: str = "") -> None:
    status = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    RESULTS.append((phase, name, status, detail))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[status]
    print(f"[{mark}] {phase:22} {name:44} {detail}"[:190], flush=True)


def check(phase: str, name: str, fn) -> object:
    """Run one probe. An exception is a failure, not a crash of the harness."""
    try:
        ok, detail, value = fn()
    except Exception as exc:  # noqa: BLE001 -- a provider raising IS the result
        record(phase, name, False, f"{exc.__class__.__name__}: {exc}")
        return None
    record(phase, name, ok, detail)
    return value


# --------------------------------------------------------------------------- app boot
tmpdir = tempfile.TemporaryDirectory()
models.DATA_DIR = tmpdir.name
models.DB_PATH = os.path.join(tmpdir.name, "lab-harness.db")
db.DATA_DIR = tmpdir.name
db.DB_PATH = models.DB_PATH
models.init_db()

patches = [
    patch.object(auth, "APP_PASSWORD", ""),
    patch.object(scheduler, "start", lambda interval_minutes=0: None),
    patch.object(scheduler, "configure", lambda interval_minutes=0: None),
]
for p in patches:
    p.start()

client_cm = TestClient(app_main.app)
client = client_cm.__enter__()


def provider_row(pid: int):
    conn = models.get_db()
    try:
        return conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    finally:
        conn.close()


def live(pid: int):
    """The provider object Vauxtra itself would build, to poke the container directly."""
    return create_provider(provider_row(pid))


def bare(spec: dict):
    """A provider built straight from the spec, before anything is registered in the DB."""
    cls, _needs_extra = _PROVIDER_REGISTRY[spec["type"]]
    return cls(spec["url"], spec["username"], spec["password"])


LAB_SUFFIX = ".vxlab.test"


def wipe(spec: dict) -> tuple[bool, str, None]:
    """Remove everything a previous run left on this provider.

    A leftover is not harmless: NPM refuses a second host for a domain it already serves,
    so one aborted run makes the next one fail on `create` and blame Vauxtra. The harness
    has to start from an empty provider to be able to fail for the right reason.
    """
    p = bare(spec)
    removed = 0
    if spec["kind"] == "dns":
        for row in p.list_rewrites() or []:
            domain = str(row.get("domain", ""))
            if domain.lower().endswith(LAB_SUFFIX):
                answer = row.get("answer") or row.get("ip") or ""
                removed += bool(p.delete_rewrite(domain, answer))
    else:
        for h in p.list_hosts() or []:
            domains = h.get("domains") or h.get("domain_names") or []
            if any(str(d).lower().endswith(LAB_SUFFIX) for d in domains):
                removed += bool(p.delete_host(h.get("id")))
    return True, f"{removed} leftover(s) removed", None


def main() -> int:
    ids: dict[str, int] = {}

    # ------------------------------------------------------------------ 0. clean slate
    for spec in PROVIDERS:
        check("0-nettoyage", spec["name"], lambda spec=spec: wipe(spec))

    # ------------------------------------------------------------------ 1. registration
    for spec in PROVIDERS:
        payload = {k: spec[k] for k in ("name", "type", "url", "username", "password")}

        def register(payload=payload):
            r = client.post("/api/providers", json=payload)
            if r.status_code != 201:
                return False, f"HTTP {r.status_code} {r.text[:120]}", None
            return True, f"id={r.json()['id']}", r.json()["id"]

        pid = check("1-register", spec["name"], register)
        if pid:
            ids[spec["name"]] = pid

    # ------------------------------------------------------------------ 2. connection
    for spec in PROVIDERS:
        pid = ids.get(spec["name"])
        if not pid:
            record("2-connexion", spec["name"], None, "not registered")
            continue

        def connect(pid=pid):
            r = client.post(f"/api/providers/{pid}/test")
            body = r.json() if r.content else {}
            ok = r.status_code == 200 and bool(body.get("ok") or body.get("success"))
            return ok, f"HTTP {r.status_code} {str(body)[:110]}", body

        check("2-connexion", spec["name"], connect)

    # ------------------------------------------------- 3. direct record CRUD per provider
    for spec in PROVIDERS:
        pid = ids.get(spec["name"])
        if not pid:
            continue
        host = f"direct-{spec['type']}.vxlab.test"

        if spec["kind"] == "dns":
            def create(pid=pid, host=host):
                r = client.post(f"/api/providers/{pid}/dns-records",
                                json={"domain": host, "answer": DNS_IP})
                return r.status_code == 201, f"HTTP {r.status_code} {r.text[:110]}", None

            def read_back(pid=pid, host=host):
                r = client.get(f"/api/providers/{pid}/dns-records")
                rows = r.json() if r.status_code == 200 else []
                if isinstance(rows, dict):
                    rows = rows.get("records") or rows.get("rewrites") or []
                hit = [x for x in rows
                       if str(x.get("domain", "")).lower() == host]
                answer = str(hit[0].get("answer") or hit[0].get("ip") or "") if hit else ""
                return bool(hit) and answer == DNS_IP, f"{len(rows)} record(s), answer={answer!r}", None

            def delete(pid=pid, host=host):
                r = client.delete(f"/api/providers/{pid}/dns-records/{host}",
                                  params={"answer": DNS_IP})
                return r.status_code in (200, 204), f"HTTP {r.status_code} {r.text[:110]}", None

            def gone(pid=pid, host=host):
                r = client.get(f"/api/providers/{pid}/dns-records")
                rows = r.json() if r.status_code == 200 else []
                if isinstance(rows, dict):
                    rows = rows.get("records") or rows.get("rewrites") or []
                hit = [x for x in rows if str(x.get("domain", "")).lower() == host]
                return not hit, f"{len(rows)} record(s) left, match={len(hit)}", None

            check("3-crud-dns", f"{spec['name']} create", create)
            check("3-crud-dns", f"{spec['name']} read back", read_back)
            check("3-crud-dns", f"{spec['name']} delete", delete)
            check("3-crud-dns", f"{spec['name']} gone after delete", gone)

        else:
            created: dict = {}

            def create(pid=pid, host=host, created=created):
                r = client.post(f"/api/providers/{pid}/proxy-hosts", json={
                    "domain_names": [host], "forward_host": UPSTREAM_HOST,
                    "forward_port": UPSTREAM_PORT, "scheme": "http",
                })
                created["reply"] = r.json() if r.content else {}
                return r.status_code == 201, f"HTTP {r.status_code} {r.text[:110]}", None

            def read_back(pid=pid, host=host, created=created):
                p = live(pid)
                hosts = p.list_hosts() or []
                hit = [h for h in hosts
                       if host in (h.get("domains") or h.get("domain_names") or [])]
                if hit:
                    created["id"] = hit[0].get("id")
                origin = f"{hit[0].get('host')}:{hit[0].get('port')}" if hit else ""
                return bool(hit), f"{len(hosts)} host(s), origin={origin!r}", None

            def delete(pid=pid, created=created):
                hid = created.get("id")
                if hid is None:
                    return False, "no host id captured", None
                r = client.delete(f"/api/providers/{pid}/proxy-hosts/{hid}")
                return r.status_code in (200, 204), f"HTTP {r.status_code} {r.text[:110]}", None

            def gone(pid=pid, host=host):
                hosts = live(pid).list_hosts() or []
                hit = [h for h in hosts
                       if host in (h.get("domains") or h.get("domain_names") or [])]
                return not hit, f"{len(hosts)} host(s) left, match={len(hit)}", None

            check("3-crud-proxy", f"{spec['name']} create", create)
            check("3-crud-proxy", f"{spec['name']} read back", read_back)
            check("3-crud-proxy", f"{spec['name']} delete", delete)
            check("3-crud-proxy", f"{spec['name']} gone after delete", gone)

    # ------------------------------------------------- 4. service lifecycle + drift
    pairs = [("lab-npm", "lab-adguard"), ("lab-zoraxy", "lab-technitium"),
             ("lab-npm", "lab-pihole")]

    for n, (proxy_name, dns_name) in enumerate(pairs, start=1):
        proxy_id, dns_id = ids.get(proxy_name), ids.get(dns_name)
        label = f"{proxy_name.removeprefix('lab-')}+{dns_name.removeprefix('lab-')}"
        if not (proxy_id and dns_id):
            record("4-service", label, None, "provider missing")
            continue

        sub = f"svc{n}"
        host = f"{sub}.vxlab.test"
        state: dict = {}

        def create_service(proxy_id=proxy_id, dns_id=dns_id, sub=sub, state=state):
            r = client.post("/api/services", json={
                "subdomain": sub, "domain": "vxlab.test",
                "target_ip": UPSTREAM_HOST, "target_port": UPSTREAM_PORT,
                "forward_scheme": "http", "dns_ip": DNS_IP,
                "proxy_provider_id": proxy_id, "dns_provider_id": dns_id,
            })
            # 207 is "service created, but a provider refused its half of the work".
            # The service exists, so the rest of the lifecycle is still worth running --
            # skipping it hides which of the two providers actually failed.
            if r.status_code not in (201, 207):
                return False, f"HTTP {r.status_code} {r.text[:130]}", None
            body = r.json()
            state["sid"] = body.get("id")
            state["errors"] = body.get("errors") or []
            ok = r.status_code == 201 and not state["errors"]
            return ok, f"HTTP {r.status_code} id={state['sid']} errors={state['errors']}", None

        def route_present(proxy_id=proxy_id, host=host):
            hosts = live(proxy_id).list_hosts() or []
            hit = [h for h in hosts
                   if host in (h.get("domains") or h.get("domain_names") or [])]
            origin = f"{hit[0].get('host')}:{hit[0].get('port')}" if hit else ""
            return bool(hit), f"origin={origin!r}", None

        def rewrite_present(dns_id=dns_id, host=host):
            rows = live(dns_id).list_rewrites() or []
            hit = [r for r in rows if str(r.get("domain", "")).lower() == host]
            answer = str(hit[0].get("answer") or hit[0].get("ip") or "") if hit else ""
            return bool(hit) and answer == DNS_IP, f"answer={answer!r}", None

        def drift_clean(state=state):
            r = client.get(f"/api/services/{state['sid']}/drift")
            b = r.json()
            return bool(b.get("ok")), f"issues={[i['type'] for i in b.get('issues', [])]}", None

        def break_dns(dns_id=dns_id, host=host):
            ok = live(dns_id).delete_rewrite(host, DNS_IP)
            return bool(ok), "rewrite deleted on the provider", None

        def drift_sees_dns(state=state):
            r = client.get(f"/api/services/{state['sid']}/drift")
            b = r.json()
            types = [i["type"] for i in b.get("issues", [])]
            return "missing_dns_rewrite" in types, f"ok={b.get('ok')} issues={types}", None

        def break_proxy(proxy_id=proxy_id, host=host):
            p = live(proxy_id)
            hosts = p.list_hosts() or []
            hit = next((h for h in hosts
                        if host in (h.get("domains") or h.get("domain_names") or [])), None)
            if not hit:
                return False, "route already gone", None
            ok = p.update_host(hit.get("id"), host, UPSTREAM_HOST, 9999, "http", False, None)
            return bool(ok), "origin port forced to 9999 on the provider", None

        def drift_sees_proxy(state=state):
            r = client.get(f"/api/services/{state['sid']}/drift")
            b = r.json()
            types = [i["type"] for i in b.get("issues", [])]
            return "proxy_origin_mismatch" in types, f"ok={b.get('ok')} issues={types}", None

        def reconcile(state=state):
            r = client.post(f"/api/services/{state['sid']}/reconcile")
            b = r.json() if r.content else {}
            after = (b.get("after") or {})
            return r.status_code == 200 and bool(after.get("ok")), \
                f"HTTP {r.status_code} after.ok={after.get('ok')} " \
                f"issues={[i['type'] for i in after.get('issues', [])]}", None

        def delete_service(state=state):
            r = client.delete(f"/api/services/{state['sid']}")
            return r.status_code in (200, 204), f"HTTP {r.status_code} {r.text[:130]}", None

        def route_gone(proxy_id=proxy_id, host=host):
            hosts = live(proxy_id).list_hosts() or []
            hit = [h for h in hosts
                   if host in (h.get("domains") or h.get("domain_names") or [])]
            return not hit, f"{len(hosts)} host(s) left, match={len(hit)}", None

        def rewrite_gone(dns_id=dns_id, host=host):
            rows = live(dns_id).list_rewrites() or []
            hit = [r for r in rows if str(r.get("domain", "")).lower() == host]
            return not hit, f"{len(rows)} rewrite(s) left, match={len(hit)}", None

        check("4-service", f"{label} create", create_service)
        if not state.get("sid"):
            record("4-service", f"{label} (rest of lifecycle)", None, "not created")
            continue
        check("4-service", f"{label} route on proxy", route_present)
        check("4-service", f"{label} rewrite on dns", rewrite_present)
        check("5-drift", f"{label} clean after push", drift_clean)
        check("5-drift", f"{label} break dns behind vauxtra", break_dns)
        check("5-drift", f"{label} drift detects missing rewrite", drift_sees_dns)
        check("5-drift", f"{label} reconcile", reconcile)
        check("5-drift", f"{label} break proxy origin behind vauxtra", break_proxy)
        check("5-drift", f"{label} drift detects origin mismatch", drift_sees_proxy)
        check("5-drift", f"{label} reconcile again", reconcile)
        check("6-delete", f"{label} delete service", delete_service)
        check("6-delete", f"{label} route gone from proxy", route_gone)
        check("6-delete", f"{label} rewrite gone from dns", rewrite_gone)

    # ------------------------------------------------------------------ summary
    print()
    print("=" * 100)
    failed = [r for r in RESULTS if r[2] == "FAIL"]
    skipped = [r for r in RESULTS if r[2] == "SKIP"]
    passed = [r for r in RESULTS if r[2] == "PASS"]
    print(f"{len(passed)} pass, {len(failed)} fail, {len(skipped)} skip "
          f"({len(RESULTS)} probes)")
    if failed:
        print("\nFailures:")
        for phase, name, _s, detail in failed:
            print(f"  {phase:22} {name:46} {detail[:100]}")
    return 1 if failed else 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 2
finally:
    client_cm.__exit__(None, None, None)
    for p in reversed(patches):
        p.stop()
    tmpdir.cleanup()

sys.exit(code)
