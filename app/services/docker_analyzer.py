"""Docker container label analysis with confidence scoring.

Parses container labels from multiple label conventions to produce
a routing suggestion with a confidence level (high / medium / low).

Priority order:
  1. vauxtra.* labels  — explicit user intent           → high confidence
  2. Traefik v2/v3 labels — widely used, reliable       → high confidence
  3. Port heuristics alone (443 → https, 80 → http)     → low confidence
"""

from __future__ import annotations

import re
from typing import TypedDict


class ContainerSuggestion(TypedDict):
    subdomain: str
    target_port: int | None
    forward_scheme: str
    websocket: bool
    confidence: str          # "high" | "medium" | "low"
    source: str              # e.g. "vauxtra_label", "traefik_label", "port_heuristic"
    middlewares: list[str]   # Traefik middleware names if detected
    tls_resolver: str | None # ACME resolver name if detected


# ── Helpers ───────────────────────────────────────────────────────────────────

_SUBDOMAIN_RE = re.compile(r"[^a-z0-9-]+")

def _sanitize(raw: str) -> str:
    value = (raw or "service").strip().lower()
    value = _SUBDOMAIN_RE.sub("-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:63] or "service"


def _scheme_from_port(port: int | None) -> str:
    if port == 443:
        return "https"
    return "http"


# ── Traefik label parsing ─────────────────────────────────────────────────────

# Matches: traefik.http.routers.<name>.rule
_TRAEFIK_RULE_RE = re.compile(
    r"^traefik\.http\.routers\.([^.]+)\.rule$", re.IGNORECASE
)
# Matches: traefik.http.routers.<name>.middlewares
_TRAEFIK_MW_RE = re.compile(
    r"^traefik\.http\.routers\.([^.]+)\.middlewares$", re.IGNORECASE
)
# Matches: traefik.http.routers.<name>.tls.certresolver
_TRAEFIK_TLS_RE = re.compile(
    r"^traefik\.http\.routers\.([^.]+)\.tls\.certresolver$", re.IGNORECASE
)
# Matches: traefik.http.services.<name>.loadbalancer.server.port
_TRAEFIK_PORT_RE = re.compile(
    r"^traefik\.http\.services\.([^.]+)\.loadbalancer\.server\.port$",
    re.IGNORECASE,
)
# Matches Host(`sub.domain.tld`) or Host('sub.domain.tld')
_HOST_RULE_RE = re.compile(r"Host\([`'\"]([^`'\"]+)[`'\"]\)", re.IGNORECASE)


def _parse_traefik_labels(labels: dict[str, str]) -> dict | None:
    """
    Extract routing information from Traefik v2/v3 router labels.

    Returns a partial suggestion dict or None if no Traefik labels found.
    """
    # Collect per-router data
    routers: dict[str, dict] = {}

    for key, value in labels.items():
        m = _TRAEFIK_RULE_RE.match(key)
        if m:
            name = m.group(1)
            routers.setdefault(name, {})["rule"] = value
            continue

        m = _TRAEFIK_MW_RE.match(key)
        if m:
            name = m.group(1)
            routers.setdefault(name, {})["middlewares"] = [
                mw.strip() for mw in value.split(",") if mw.strip()
            ]
            continue

        m = _TRAEFIK_TLS_RE.match(key)
        if m:
            name = m.group(1)
            routers.setdefault(name, {})["tls_resolver"] = value.strip()
            continue

    # Collect per-service port overrides
    service_ports: dict[str, int] = {}
    for key, value in labels.items():
        m = _TRAEFIK_PORT_RE.match(key)
        if m:
            try:
                service_ports[m.group(1)] = int(value)
            except ValueError:
                pass

    if not routers:
        return None

    # Pick the first router with a parseable Host rule
    for router_name, rdata in routers.items():
        rule = rdata.get("rule", "")
        hosts = _HOST_RULE_RE.findall(rule)
        if not hosts:
            continue

        fqdn = hosts[0]  # use first matched hostname
        parts = fqdn.split(".")
        subdomain = parts[0] if len(parts) > 2 else fqdn

        # Port: look for a service with matching name, else None
        port = service_ports.get(router_name)

        # TLS resolver implies HTTPS scheme
        tls_resolver = rdata.get("tls_resolver")
        scheme = "https" if tls_resolver or port == 443 else "http"

        middlewares = rdata.get("middlewares", [])

        return {
            "subdomain": _sanitize(subdomain),
            "target_port": port,
            "forward_scheme": scheme,
            "websocket": False,
            "confidence": "high",
            "source": "traefik_label",
            "middlewares": middlewares,
            "tls_resolver": tls_resolver,
        }

    return None


# ── vauxtra.* label parsing ───────────────────────────────────────────────────

def _parse_vauxtra_labels(labels: dict[str, str], container_name: str) -> dict | None:
    """
    Parse explicit vauxtra.* labels.

    Recognized keys:
      vauxtra.subdomain   — override subdomain (defaults to container name)
      vauxtra.port        — target port
      vauxtra.scheme      — "http" or "https"
      vauxtra.websocket   — "true" / "1" / "yes"
    """
    # Only activate if at least one vauxtra.* label is present
    vauxtra_keys = {k for k in labels if k.startswith("vauxtra.")}
    if not vauxtra_keys:
        return None

    raw_subdomain = labels.get("vauxtra.subdomain") or labels.get("vauxtra.host") or container_name
    raw_port = labels.get("vauxtra.port")
    raw_scheme = labels.get("vauxtra.scheme", "").lower()
    raw_ws = labels.get("vauxtra.websocket", "").lower()

    port: int | None = None
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError:
            pass

    scheme = raw_scheme if raw_scheme in ("http", "https") else _scheme_from_port(port)
    websocket = raw_ws in ("1", "true", "yes")

    return {
        "subdomain": _sanitize(raw_subdomain),
        "target_port": port,
        "forward_scheme": scheme,
        "websocket": websocket,
        "confidence": "high",
        "source": "vauxtra_label",
        "middlewares": [],
        "tls_resolver": None,
    }


# ── Port heuristic ────────────────────────────────────────────────────────────

def _heuristic_from_port(port: int | None) -> dict:
    """Fallback suggestion based solely on exposed port numbers."""
    scheme = _scheme_from_port(port)
    return {
        "subdomain": None,          # caller fills in container name
        "target_port": port,
        "forward_scheme": scheme,
        "websocket": False,
        "confidence": "low",
        "source": "port_heuristic",
        "middlewares": [],
        "tls_resolver": None,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_container(
    labels: dict[str, str],
    container_name: str,
    detected_port: int | None,
) -> ContainerSuggestion:
    """
    Produce a routing suggestion for a running container.

    Resolution order (first match wins):
      1. vauxtra.* labels
      2. Traefik v2/v3 router labels
      3. Port-based heuristic (always succeeds, confidence=low)
    """
    # 1. Explicit vauxtra labels
    result = _parse_vauxtra_labels(labels, container_name)

    # 2. Traefik labels
    if result is None:
        result = _parse_traefik_labels(labels)

    # 3. Port heuristic fallback
    if result is None:
        result = _heuristic_from_port(detected_port)
        result["subdomain"] = _sanitize(container_name)
    else:
        # Fill missing port from Docker-detected port
        if result.get("target_port") is None:
            result["target_port"] = detected_port
        # Fill missing subdomain
        if not result.get("subdomain"):
            result["subdomain"] = _sanitize(container_name)

    return ContainerSuggestion(**result)
