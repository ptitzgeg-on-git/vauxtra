"""Which tunnel routes Cloudflare Access stands in front of, read from Access itself.

A tunnel publishes a hostname to the whole internet. Whether anyone has to log in first is
decided somewhere else entirely, by an Access application, and nothing in Vauxtra looked
there: a route created through the tunnel was open to everyone until the operator went to
the Zero Trust dashboard and wrote an application by hand, and the panel never said which
routes had one.

Three readings have to be kept apart, because the API does not keep them apart for us.
Measured on 2026-11-14 with a tunnel token that carries no Access permission, on an account
holding eighteen applications:

  * `GET /accounts/{id}/access/apps` answered **200, success, an empty list**;
  * `GET /accounts/{id}/access/policies` answered the same;
  * `GET /zones/{id}/access/apps` answered 403, code 10000.

So an empty list is not "no application", and reading it as "every route is open" would be
wrong in the reassuring direction for nobody and the alarming one for everybody. An empty
answer is `unverified`; it becomes `missing` when the edge contradicts it (a probe was
redirected to `*.cloudflareaccess.com`, see `app/probe.py`), and only a non-empty list is
read as the truth.

The functions below are pure: the provider fetches, these classify, and the tests run
without a network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlparse

# What the operator has to add to the tunnel token, named the way the dashboard names it.
READ_PERMISSION = "Access: Apps and Policies Read"
EDIT_PERMISSION = "Access: Apps and Policies Edit"

PERMISSION_STATES = ("ok", "unverified", "missing", "error")
HOST_STATES = ("protected", "partial", "open", "unknown")

def is_permission_refusal(resp: Mapping[str, Any]) -> bool:
    """True when Cloudflare said the token may not read this, rather than "nothing here"."""
    if resp.get("status") in (401, 403):
        return True
    return any(
        isinstance(e, Mapping) and e.get("code") in (10000, 9109, 12006)
        for e in resp.get("errors") or []
    )


def _host_and_path(value: str) -> tuple[str, str]:
    raw = (value or "").strip().lower()
    if not raw:
        return "", ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    return (parsed.hostname or "").strip("."), (parsed.path or "").rstrip("*").rstrip("/")


def app_patterns(app: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every (host pattern, path) an application covers. Paths are '' for the whole host."""
    values: list[str] = []
    for dest in app.get("destinations") or []:
        if isinstance(dest, Mapping) and str(dest.get("type") or "public") == "public":
            values.append(str(dest.get("uri") or ""))
    values.extend(str(d) for d in app.get("self_hosted_domains") or [])
    values.append(str(app.get("domain") or ""))
    seen: list[tuple[str, str]] = []
    for value in values:
        pattern = _host_and_path(value)
        if pattern[0] and pattern not in seen:
            seen.append(pattern)
    return seen


def _includes_everyone(policy: Mapping[str, Any]) -> bool:
    for rule in policy.get("include") or []:
        if isinstance(rule, Mapping) and "everyone" in rule:
            return True
    return False


def summarize_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """What the panel may show of a policy: its name and decision, never its rules.

    The include rules of a policy are e-mail addresses and IP lists. They are the operator's
    to read in the dashboard, and a `read` key has no business carrying them away.
    """
    return {
        "id": str(policy.get("id") or ""),
        "name": str(policy.get("name") or ""),
        "decision": str(policy.get("decision") or ""),
        "everyone": _includes_everyone(policy),
        "app_count": int(policy.get("app_count") or 0),
    }


def _app_weakness(app: Mapping[str, Any]) -> str:
    """'' when the app keeps strangers out, else the policy that lets them in.

    Access denies whatever no policy matches, so an application whose only policy bypasses a
    list of IPs is restrictive, not weak: those addresses pass, everyone else is refused.
    What opens an application is a policy whose include rule is `everyone`: a bypass lets the
    whole internet through untouched, an allow lets in anyone who can receive a one-time PIN.
    """
    for policy in app.get("policies") or []:
        if not isinstance(policy, Mapping) or not _includes_everyone(policy):
            continue
        decision = str(policy.get("decision") or "")
        if decision == "bypass":
            return "everyone_bypass"
        if decision == "allow":
            return "everyone_allow"
    return ""


def _specificity(pattern: str) -> tuple[int, int]:
    """Cloudflare lets the most specific application win: an exact name before a wildcard."""
    return (0 if "*" in pattern else 1, len(pattern))


def classify_host(hostname: str, apps: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The Access state of one hostname, given applications read with permission.

    The application that decides the root of the host is the most specific one covering it
    whole: an exact `listo.example.com` beats `*.example.com`, which is how Access itself
    resolves the overlap. Applications that cover only a path leave the rest of the host to
    that one, or to nobody, so on their own they make the host `partial`.
    """
    host = (hostname or "").strip().strip(".").lower()
    covering: list[dict[str, Any]] = []
    policy_names: set[str] = set()
    root: tuple[tuple[int, int], Mapping[str, Any]] | None = None
    for app in apps:
        matched = [(pat, path) for pat, path in app_patterns(app) if fnmatchcase(host, pat)]
        if not matched:
            continue
        covering.append({"id": str(app.get("id") or ""), "name": str(app.get("name") or "")})
        policy_names.update(
            str(p.get("name")) for p in app.get("policies") or []
            if isinstance(p, Mapping) and p.get("name")
        )
        for pattern, path in matched:
            if not path and (root is None or _specificity(pattern) > root[0]):
                root = (_specificity(pattern), app)
    if root is not None:
        weakness = _app_weakness(root[1])
        if weakness == "everyone_bypass":
            state, reason = "open", weakness
        elif weakness:
            state, reason = "partial", weakness
        else:
            state, reason = "protected", ""
    elif covering:
        state, reason = "partial", "path_only"
    else:
        state, reason = "open", "no_application"
    return {"state": state, "reason": reason, "apps": covering, "policies": sorted(policy_names)}


def unknown_host(reason: str) -> dict[str, Any]:
    return {"state": "unknown", "reason": reason, "apps": [], "policies": []}


def new_application_body(hostname: str, policy_ids: list[str], session_duration: str = "24h") -> dict:
    """The smallest self-hosted application that puts a login in front of one hostname."""
    host = hostname.strip().strip(".").lower()
    return {
        "name": host,
        "type": "self_hosted",
        "domain": host,
        "destinations": [{"type": "public", "uri": host}],
        "session_duration": session_duration,
        "app_launcher_visible": False,
        "policies": [{"id": pid, "precedence": i + 1} for i, pid in enumerate(policy_ids)],
    }
