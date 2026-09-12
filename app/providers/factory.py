import json
import logging
import os
from importlib import import_module
from urllib.parse import urlsplit, urlunsplit

from app.config import decrypt_secret
from app.providers.adguard import AdGuardProvider
from app.providers.cloudflare import CloudflareProvider
from app.providers.cloudflare_tunnel import CloudflareTunnelProvider
from app.providers.desec import DesecProvider
from app.providers.npm import NPMProvider
from app.providers.pihole import PiholeProvider
from app.providers.powerdns import PowerDNSProvider
from app.providers.technitium import TechnitiumProvider
from app.providers.traefik import TraefikProvider
from app.providers.zoraxy import ZoraxyProvider

logger = logging.getLogger(__name__)

PROVIDER_TYPES = {
    "npm": {
        "label": "Nginx Proxy Manager", "category": "proxy", "available": True,
        "description": "Reverse proxy with Let's Encrypt certificates",
        "category_label": "Reverse Proxy",
        "category_color": "bg-green-500/10 text-green-700 dark:text-green-400",
        "provider_color": "bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-400",
        "capabilities": {
            "proxy": True,
            "dns": False,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
            "certificates": True,
        },
        "icon": "ti-lock", "color": "blue",
        "placeholder_url": "http://192.168.1.10:81",
        "user_label": "Email", "pass_label": "Password",
        "user_placeholder": "admin@example.com",
        "guided_steps": [
            {
                "title": "Create a dedicated NPM user",
                "body": "Vauxtra needs a user account in Nginx Proxy Manager to manage proxy hosts.\n\n1. Open NPM at http://<npm-host>:81\n2. Go to Users (top-right menu) → Add User\n3. Fill in name, email and a strong password\n4. Under Permissions, enable Manage Proxy Hosts\n5. Save — then use that email and password in the next step\n\nTip: Using a dedicated Vauxtra user (instead of admin) limits blast radius.",
            },
            {
                "title": "Enter the NPM URL",
                "body": "Enter the URL of your NPM admin panel. The default port is 81.",
                "fields": [
                    {"key": "url", "label": "NPM URL", "placeholder": "http://192.168.1.10:81",
                     "hint": "Use the internal IP or hostname. Include the port (default: 81).", "input_type": "url"},
                ],
            },
            {
                "title": "NPM Credentials",
                "body": "Enter the email and password of the NPM user you created in step 1.",
                "fields": [
                    {"key": "username", "label": "Email", "placeholder": "vauxtra@example.com",
                     "hint": "The email you set when creating the NPM user.", "input_type": "text"},
                    {"key": "password", "label": "Password", "placeholder": "(NPM user password)",
                     "input_type": "password"},
                ],
            },
        ],
    },
    "zoraxy": {
        "label": "Zoraxy", "category": "proxy", "available": True,
        "description": "Zoraxy reverse proxy",
        "category_label": "Reverse Proxy",
        "category_color": "bg-sky-500/10 text-sky-600 dark:text-sky-400",
        "provider_color": "bg-sky-500/10 text-sky-600 border-sky-500/30 dark:text-sky-400",
        "capabilities": {
            "proxy": True,
            "dns": False,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
            "certificates": True,
        },
        "icon": "ti-route", "color": "sky",
        "placeholder_url": "http://192.168.1.10:8000",
        "user_label": "Username", "pass_label": "Password",
        "user_placeholder": "admin",
        "guided_steps": [
            {
                "title": "Prepare the Zoraxy web console",
                "body": "Vauxtra logs in to the Zoraxy web console with the same username and password you use in the browser, then manages proxy rules and reads certificates through its API.\n\nZoraxy has a single administrator account: Vauxtra will use it, so keep the console (port 8000) on your LAN or VPN and never expose it publicly.\n\nIf Zoraxy runs with -noauth, leave the username and password blank in step 3.\n\nNote: imported rules become managed by Vauxtra: editing or deleting the service edits or deletes the Zoraxy rule. Creating a service for a hostname that already has a rule in Zoraxy is refused; import the rule instead.",
            },
            {
                "title": "Enter the Zoraxy URL",
                "body": "Enter the URL of the Zoraxy web console. The default port is 8000.",
                "fields": [
                    {"key": "url", "label": "Zoraxy URL", "placeholder": "http://192.168.1.10:8000",
                     "hint": "Use the internal IP or hostname. Include the port (default: 8000).", "input_type": "url"},
                ],
            },
            {
                "title": "Zoraxy credentials",
                "body": "Enter the username and password of the Zoraxy web console. Leave both blank if Zoraxy runs with -noauth.",
                "fields": [
                    {"key": "username", "label": "Username", "placeholder": "admin",
                     "hint": "The web console login.", "input_type": "text", "optional": True},
                    {"key": "password", "label": "Password", "placeholder": "(web console password)",
                     "input_type": "password", "optional": True},
                ],
            },
        ],
    },
    "adguard": {
        "label": "AdGuard Home", "category": "dns", "available": True,
        "description": "DNS sinkhole & filtering",
        "category_label": "Local DNS",
        "category_color": "bg-teal-500/10 text-teal-600 dark:text-teal-400",
        "provider_color": "bg-teal-500/10 text-teal-600 border-teal-500/30 dark:text-teal-400",
        "capabilities": {
            "proxy": False,
            "dns": True,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        },
        "icon": "ti-shield-check", "color": "teal",
        "placeholder_url": "http://192.168.1.10:3000",
        "user_label": "Username", "pass_label": "Password",
        "user_placeholder": "admin",
        "guided_steps": [
            {
                "title": "AdGuard Home connection details",
                "body": "Vauxtra uses the AdGuard Home REST API with your web admin credentials.\n\nNo extra configuration is needed in AdGuard — just use the same username and password as the admin panel.\n\nDefault URL: http://<host>:3000\nDefault credentials set during first-run setup.",
                "fields": [
                    {"key": "url", "label": "AdGuard URL", "placeholder": "http://192.168.1.10:3000",
                     "hint": "Default port is 3000. Use the internal IP or hostname.", "input_type": "url"},
                    {"key": "username", "label": "Username", "placeholder": "admin", "input_type": "text"},
                    {"key": "password", "label": "Password", "placeholder": "(admin panel password)",
                     "input_type": "password"},
                ],
            },
        ],
    },
    "pihole": {
        "label": "Pi-hole", "category": "dns", "available": True,
        "description": "Local DNS & ad filtering",
        "category_label": "Local DNS",
        "category_color": "bg-red-500/10 text-red-600 dark:text-red-400",
        "provider_color": "bg-red-500/10 text-red-600 border-red-500/30 dark:text-red-400",
        "capabilities": {
            "proxy": False,
            "dns": True,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        },
        "icon": "ti-ad-circle-off", "color": "red",
        "placeholder_url": "http://192.168.1.10:80",
        "user_label": "Username", "pass_label": "API key / password",
        "user_placeholder": "admin",
        "guided_steps": [
            {
                "title": "Find your Pi-hole API token",
                "body": "Vauxtra uses the Pi-hole API to manage local DNS entries.\n\nTo find your API token:\n  Pi-hole v5:  Settings → API / Web interface → Show API token\n  Pi-hole v6:  Settings → API → Create / show API key\n\nAlternatively, you can use your admin panel password directly.\nThe URL is typically http://<pi-hole-ip> (port 80, no /admin suffix).",
            },
            {
                "title": "Pi-hole URL and credentials",
                "body": "Enter the Pi-hole URL and the API token (or admin password) you located in the previous step.",
                "fields": [
                    {"key": "url", "label": "Pi-hole URL", "placeholder": "http://192.168.1.53",
                     "hint": "IP or hostname only — no /admin suffix needed.", "input_type": "url"},
                    {"key": "password", "label": "API Token / Admin password",
                     "placeholder": "(paste API token or admin password)",
                     "hint": "Settings → API / Web interface → Show API token (v5) or Settings → API (v6).",
                     "input_type": "password"},
                ],
            },
        ],
    },
    "traefik": {
        "label": "Traefik", "category": "proxy", "available": True,
        "read_only": True,
        "description": "Dynamic reverse proxy (read-only)",
        "category_label": "Reverse Proxy",
        "category_color": "bg-blue-500/10 text-blue-600 dark:text-blue-400",
        "provider_color": "bg-blue-500/10 text-blue-600 border-blue-500/30 dark:text-blue-400",
        "capabilities": {
            "proxy": True,
            "dns": False,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        },
        "icon": "ti-route", "color": "cyan",
        "placeholder_url": "http://192.168.1.10:8080",
        "user_label": "Username (optional)", "pass_label": "Password (optional)",
        "user_placeholder": "",
        "guided_steps": [
            {
                "title": "Expose the Traefik API",
                "body": "Vauxtra reads Traefik in read-only mode — it never modifies your routing configuration.\n\nYou need to expose the Traefik API on a reachable URL. Two common ways:\n\n  Option A — Insecure (quick test):\n    Add --api.insecure=true to your Traefik static config.\n    API will be available at http://<host>:8080/api/\n\n  Option B — Secure router (recommended):\n    Create a dedicated Traefik entrypoint/router for /api/\n    Add BasicAuth middleware if you want credentials.\n\nLeave username/password blank if no auth is configured.",
                "fields": [
                    {"key": "url", "label": "Traefik API URL", "placeholder": "http://192.168.1.10:8080",
                     "hint": "Full URL to the Traefik API dashboard (no /api suffix needed).", "input_type": "url"},
                ],
            },
        ],
    },
    "cloudflare": {
        "label": "Cloudflare", "category": "dns", "available": True,
        "description": "DNS records via Cloudflare API",
        "category_label": "External DNS",
        "category_color": "bg-orange-500/10 text-orange-600 dark:text-orange-400",
        "provider_color": "bg-orange-500/10 text-orange-600 border-orange-500/30 dark:text-orange-400",
        "capabilities": {
            "proxy": False,
            "dns": True,
            "public_dns": True,
            "supports_auto_public_target": True,
            "supports_tunnel": False,
        },
        "icon": "ti-cloud", "color": "orange",
        "placeholder_url": "https://api.cloudflare.com",
        "user_label": "Zone ID (optional)", "pass_label": "API Token",
        "user_placeholder": "",
        "guided_steps": [
            {
                "title": "Create a Cloudflare API Token",
                "body": "Go to My Profile → API Tokens → Create Token.\nUse the \"Edit zone DNS\" template, or a Custom Token with:\n  • Zone → DNS → Edit (select your zone)\n\nCopy the generated token and paste it below.",
                "fields": [
                    {"key": "password", "label": "API Token", "placeholder": "(paste token here)",
                     "hint": "Zone-scoped token with DNS:Edit permission.", "input_type": "password"},
                ],
            },
            {
                "title": "Zone ID (usually not needed)",
                "body": "Your API token already defines which zones it can access.\n\nLeave this blank unless you want to override the token scope.\nVauxtra will auto-detect zones from your token permissions.",
                "fields": [
                    {"key": "username", "label": "Zone ID", "placeholder": "(leave blank - auto-detected from token)",
                     "hint": "Only needed if your token covers multiple zones and you want to restrict to one.",
                     "input_type": "text", "optional": True},
                ],
            },
        ],
    },
    "technitium": {
        "label": "Technitium DNS", "category": "dns", "available": True,
        "description": "Self-hosted authoritative DNS server",
        "category_label": "Local DNS",
        "category_color": "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
        "provider_color": "bg-indigo-500/10 text-indigo-600 border-indigo-500/30 dark:text-indigo-400",
        "capabilities": {
            "proxy": False,
            "dns": True,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        },
        "icon": "ti-server", "color": "indigo",
        "placeholder_url": "http://192.168.1.10:5380",
        "user_label": "Username", "pass_label": "Password",
        "user_placeholder": "admin",
        "guided_steps": [
            {
                "title": "Prepare your DNS zones",
                "body": "Vauxtra creates A records inside your existing Technitium zones.\n\nBefore connecting, you need at least one DNS zone set up in Technitium.\n\nTo create a zone:\n  1. Open Technitium at http://<host>:5380\n  2. Go to the Zones tab → Add Zone\n  3. Choose Primary Zone, enter your domain (e.g. home.lab or home.local)\n  4. Click Save\n\nVauxtra will auto-detect the correct zone for each service domain it manages.",
            },
            {
                "title": "Technitium credentials",
                "body": "Enter the URL of your Technitium web console and your admin credentials.\nDefault port is 5380.",
                "fields": [
                    {"key": "url", "label": "Technitium URL", "placeholder": "http://192.168.1.10:5380",
                     "hint": "Default port is 5380. Use the internal IP or hostname.", "input_type": "url"},
                    {"key": "username", "label": "Username", "placeholder": "admin", "input_type": "text"},
                    {"key": "password", "label": "Password", "placeholder": "(web UI password)",
                     "input_type": "password"},
                ],
            },
        ],
    },
    "powerdns": {
        "label": "PowerDNS", "category": "dns", "available": True,
        "description": "PowerDNS Authoritative Server zone records",
        "category_label": "Local DNS",
        "category_color": "bg-violet-500/10 text-violet-600 dark:text-violet-400",
        "provider_color": "bg-violet-500/10 text-violet-600 border-violet-500/30 dark:text-violet-400",
        "capabilities": {
            "proxy": False,
            "dns": True,
            # PowerDNS Authoritative *can* serve a public zone, but only if the zone is
            # delegated to it at a registrar — something Vauxtra cannot see from the API.
            # Declaring it public would relabel the address field "public WAN IP" for every
            # operator running it on a LAN, so the honest default is local.
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        },
        "icon": "ti-database", "color": "violet",
        "placeholder_url": "http://192.168.1.10:8081",
        "user_label": "Server ID (optional)", "pass_label": "API Key",
        "user_placeholder": "localhost",
        "guided_steps": [
            {
                "title": "Enable the PowerDNS HTTP API",
                "body": "The API is off by default. Add this to pdns.conf:\n\n  api=yes\n  api-key=<a long random string>\n  webserver=yes\n  webserver-address=0.0.0.0\n  webserver-port=8081\n  webserver-allow-from=<the IP running Vauxtra>/32\n\nRestart pdns, then keep the API key — it is the only credential Vauxtra needs.\n\nKeep webserver-allow-from as narrow as you can: anyone who reaches this API can rewrite every zone on the server.",
            },
            {
                "title": "Create at least one zone",
                "body": "Vauxtra writes records inside zones PowerDNS already hosts. It never creates a zone.\n\n  pdnsutil create-zone home.lab\n  pdnsutil add-record home.lab @ A 3600 192.168.1.10\n\nFor each service hostname, Vauxtra picks the longest hosted zone that contains it.",
            },
            {
                "title": "PowerDNS API details",
                "body": "Enter the address of the PowerDNS webserver and the API key you set above.\n\nLeave the server id blank unless you changed it — almost every install answers to \"localhost\".",
                "fields": [
                    {"key": "url", "label": "PowerDNS API URL", "placeholder": "http://192.168.1.10:8081",
                     "hint": "The webserver address and port, without /api/v1.", "input_type": "url"},
                    {"key": "username", "label": "Server ID", "placeholder": "localhost",
                     "hint": "Leave blank for the default.", "input_type": "text", "optional": True},
                    {"key": "password", "label": "API Key", "placeholder": "(the api-key from pdns.conf)",
                     "input_type": "password"},
                ],
            },
        ],
    },
    "desec": {
        "label": "deSEC", "category": "dns", "available": True,
        "description": "Public DNS records via the deSEC API",
        "category_label": "External DNS",
        "category_color": "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        "provider_color": "bg-emerald-500/10 text-emerald-600 border-emerald-500/30 dark:text-emerald-400",
        "capabilities": {
            "proxy": False,
            "dns": True,
            "public_dns": True,
            "supports_auto_public_target": True,
            "supports_tunnel": False,
        },
        "icon": "ti-shield-lock", "color": "emerald",
        "placeholder_url": "https://desec.io/api/v1",
        "user_label": "Domain (optional)", "pass_label": "API Token",
        "user_placeholder": "example.dedyn.io",
        "guided_steps": [
            {
                "title": "Register a domain with deSEC",
                "body": "deSEC is free, non-profit DNS hosting with DNSSEC on by default.\n\n  1. Create an account at https://desec.io/\n  2. Add your domain under Domain Management\n  3. At your registrar, point the nameservers at deSEC:\n       ns1.desec.io\n       ns2.desec.org\n\nA free dynDNS name under dedyn.io works too, and needs no registrar.\n\nVauxtra writes A, AAAA and CNAME records inside the domains this account holds.",
            },
            {
                "title": "Create an API token",
                "body": "In the deSEC interface, open Token Management → Create Token.\n\nName it (e.g. \"vauxtra\") and leave the permissions alone — a plain token already reads and writes DNS records. Do not tick \"Can manage tokens\".\n\nThe token is shown once. Copy it now and paste it below.",
                "fields": [
                    {"key": "password", "label": "API Token", "placeholder": "(paste token here)",
                     "hint": "Shown once, at creation. It grants write access to your DNS.",
                     "input_type": "password"},
                ],
            },
            {
                "title": "Domain and endpoint (usually blank)",
                "body": "Leave both fields empty for the public deSEC service — Vauxtra detects your domains from the token itself.\n\nFill the domain in only to pin this provider to one domain when the account holds several. Fill the URL in only if you run your own deSEC stack.",
                "fields": [
                    {"key": "username", "label": "Domain", "placeholder": "example.dedyn.io",
                     "hint": "Leave blank to use every domain the token can reach.",
                     "input_type": "text", "optional": True},
                    {"key": "url", "label": "API URL", "placeholder": "https://desec.io/api/v1",
                     "hint": "Leave blank for the public service.", "input_type": "url", "optional": True},
                ],
            },
        ],
    },
    "cloudflare_tunnel": {
        "label": "Cloudflare Tunnel", "category": "proxy", "available": True,
        "description": "Cloudflare Zero Trust Tunnel",
        "category_label": "Zero Trust",
        "category_color": "bg-orange-500/10 text-orange-600 dark:text-orange-400",
        "provider_color": "bg-orange-500/10 text-orange-600 border-orange-500/30 dark:text-orange-400",
        "capabilities": {
            "proxy": True,
            "dns": False,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": True,
        },
        "icon": "ti-cloud", "color": "indigo",
        "placeholder_url": "https://api.cloudflare.com/client/v4",
        "user_label": "Account ID", "pass_label": "API Token",
        "user_placeholder": "Cloudflare account ID",
        "guided_steps": [
            {
                "title": "Create a tunnel in Cloudflare Zero Trust",
                "body": "Go to dash.cloudflare.com → Zero Trust → Networks → Tunnels → Create a tunnel.\nChoose the Cloudflared connector type and give it a name (e.g. \"homelab\").\n\nVauxtra manages ingress routes inside the tunnel — it does not run cloudflared itself.",
            },
            {
                "title": "Paste your Tunnel ID",
                "body": "From the tunnel overview page, copy the Tunnel ID (UUID format). Paste it below.",
                "fields": [
                    {"key": "tunnel_id", "label": "Tunnel ID",
                     "placeholder": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                     "hint": "Zero Trust → Networks → Tunnels → click your tunnel → Overview tab.",
                     "input_type": "text"},
                ],
            },
            {
                "title": "Create a Cloudflare API Token",
                "body": "My Profile → API Tokens → Create Token → Custom Token.\nRequired permissions:\n  • Account → Cloudflare Tunnel → Edit\n  • Zone → DNS → Edit (select your zone)\n\nCopy the generated token and paste it below.",
                "fields": [
                    {"key": "password", "label": "API Token", "placeholder": "(paste token here)",
                     "hint": "Never share this token — it grants Tunnel and DNS write access.",
                     "input_type": "password"},
                ],
            },
            {
                "title": "Enter your Cloudflare Account ID",
                "body": "Your Account ID is a 32-character hex string shown in the right sidebar of dash.cloudflare.com (any zone overview page).",
                "fields": [
                    {"key": "username", "label": "Account ID",
                     "placeholder": "a1b2c3d4e5f6… (32 hex chars)",
                     "hint": "Right sidebar on dash.cloudflare.com → select any domain.",
                     "input_type": "text"},
                ],
            },
        ],
    },
}

# Registry mapping provider type → (class, needs_extra)
_PROVIDER_REGISTRY: dict[str, tuple[type, bool]] = {
    "adguard":           (AdGuardProvider,           False),
    "npm":               (NPMProvider,               False),
    "zoraxy":            (ZoraxyProvider,            False),
    "pihole":            (PiholeProvider,             False),
    "traefik":           (TraefikProvider,            False),
    "cloudflare":        (CloudflareProvider,         True),
    "cloudflare_tunnel": (CloudflareTunnelProvider,   True),
    "technitium":        (TechnitiumProvider,         False),
    "powerdns":          (PowerDNSProvider,           False),
    "desec":             (DesecProvider,              False),
}


def register_provider_type(
    provider_type: str,
    provider_class: type,
    meta: dict,
    *,
    needs_extra: bool = False,
) -> None:
    normalized_type = (provider_type or "").strip()
    if not normalized_type:
        raise ValueError("Provider type is required")
    if not isinstance(meta, dict):
        raise ValueError("Provider metadata must be a dict")

    PROVIDER_TYPES[normalized_type] = {
        "available": True,
        **meta,
    }
    _PROVIDER_REGISTRY[normalized_type] = (provider_class, needs_extra)


def _resolve_provider_class(class_or_path):
    if isinstance(class_or_path, type):
        return class_or_path
    if not isinstance(class_or_path, str) or "." not in class_or_path:
        raise ValueError("Provider class must be a class object or import path")

    module_name, _, attr_name = class_or_path.rpartition(".")
    module = import_module(module_name)
    return getattr(module, attr_name)


def _register_plugin_spec(spec: dict) -> None:
    provider_type = str(spec.get("type", "")).strip()
    provider_class = _resolve_provider_class(spec.get("class"))
    meta = spec.get("meta") or {}
    needs_extra = bool(spec.get("needs_extra", False))
    register_provider_type(provider_type, provider_class, meta, needs_extra=needs_extra)


def _load_external_provider_plugins() -> None:
    raw = os.environ.get("VAUXTRA_PROVIDER_PLUGINS", "")
    modules = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
    for module_name in modules:
        try:
            module = import_module(module_name)
            if hasattr(module, "register"):
                payload = module.register()
            elif hasattr(module, "PROVIDER_PLUGINS"):
                payload = module.PROVIDER_PLUGINS
            elif hasattr(module, "PROVIDER_PLUGIN"):
                payload = module.PROVIDER_PLUGIN
            else:
                raise ValueError(
                    f"Provider plugin module '{module_name}' does not expose register(), PROVIDER_PLUGIN, or PROVIDER_PLUGINS"
                )

            specs = payload if isinstance(payload, list) else [payload]
            for spec in specs:
                _register_plugin_spec(spec)
        except Exception as exc:
            logger.warning("Failed to load provider plugin '%s': %s", module_name, exc)


_load_external_provider_plugins()


def _is_container_runtime() -> bool:
    return os.path.exists("/.dockerenv")


def _rewrite_loopback_url(url: str) -> str:
    """Map localhost URLs to a host-reachable endpoint when running in Docker."""
    if not url:
        return url

    if os.environ.get("VAUXTRA_REWRITE_LOCALHOST", "true").strip().lower() in {"0", "false", "no", "off"}:
        return url

    if not _is_container_runtime():
        return url

    parsed = urlsplit(url)
    host = (parsed.hostname or "").strip().lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return url

    target_host = os.environ.get("VAUXTRA_LOCALHOST_ALIAS", "host.docker.internal").strip() or "host.docker.internal"
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"

    netloc = f"{userinfo}{target_host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def create_provider(provider_row):
    ptype = provider_row["type"]
    url   = _rewrite_loopback_url(provider_row["url"])
    user  = provider_row["username"]
    pwd   = decrypt_secret(provider_row["password"])

    entry = _PROVIDER_REGISTRY.get(ptype)
    if entry is None:
        raise ValueError(f"Provider '{ptype}' not yet supported")

    cls, needs_extra = entry
    if needs_extra:
        extra_raw = provider_row["extra"] or "{}"
        try:
            extra = json.loads(extra_raw) if extra_raw else {}
        except Exception:
            extra = {}
        return cls(url, user, pwd, extra)

    return cls(url, user, pwd)


def certificate_provider_types() -> list[str]:
    """Provider types whose certificate store Vauxtra can read, in PROVIDER_TYPES order.

    The certificate routes and the expiry scanner used to hard-code `type='npm'`, so a
    second proxy exposing its store stayed invisible to both. The capability flag is where
    a provider declares it, plugin-registered types included, so it is also where the SQL
    should read it from.
    """
    return [
        ptype for ptype, meta in PROVIDER_TYPES.items()
        if (meta.get("capabilities") or {}).get("certificates")
    ]


def host_id_is_hostname(proxy, provider_type: str | None = None) -> bool:
    """Whether `proxy` hands hosts back under their hostname rather than a stable id.

    The provider instance is asked first, so a class that declares
    `HOST_ID_IS_HOSTNAME` answers for itself. An object that does not declare it -- a
    test double standing in for a real provider -- is judged by the class registered for
    its row's type, which is what would have been built for that row.
    """
    declared = getattr(proxy, "HOST_ID_IS_HOSTNAME", None)
    if declared is not None:
        return bool(declared)
    entry = _PROVIDER_REGISTRY.get(provider_type or "")
    return bool(getattr(entry[0], "HOST_ID_IS_HOSTNAME", False)) if entry else False
