import json

from app.providers.adguard    import AdGuardProvider
from app.providers.npm        import NPMProvider
from app.providers.pihole     import PiholeProvider
from app.providers.cloudflare import CloudflareProvider
from app.providers.cloudflare_tunnel import CloudflareTunnelProvider
from app.providers.traefik    import TraefikProvider
from app.config import decrypt_secret

PROVIDER_TYPES = {
    "npm": {
        "label": "Nginx Proxy Manager", "category": "proxy", "available": True,
        "description": "Nginx Proxy Manager",
        "category_label": "Reverse Proxy",
        "category_color": "bg-green-500/10 text-green-700 dark:text-green-400",
        "provider_color": "bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-400",
        "capabilities": {
            "proxy": True,
            "dns": False,
            "public_dns": False,
            "supports_auto_public_target": False,
            "supports_tunnel": False,
        },
        "icon": "ti-lock", "color": "blue",
        "placeholder_url": "http://192.168.1.10:81",
        "user_label": "Email", "pass_label": "Password",
        "user_placeholder": "admin@example.com",
        "guided_steps": [
            {
                "title": "Enter your NPM URL",
                "body": "Nginx Proxy Manager's admin panel is typically at http://<npm-host>:81. Enter the full URL below.",
                "fields": [
                    {"key": "url", "label": "NPM URL", "placeholder": "http://npm:81",
                     "hint": "Default admin port is 81. Use the internal hostname or IP.", "input_type": "url"},
                ],
            },
            {
                "title": "NPM Credentials",
                "body": "In NPM go to Users → Add User. Create a user with \"Manage Proxy Hosts\" permission. Enter its credentials below.",
                "fields": [
                    {"key": "username", "label": "Email", "placeholder": "user@example.com",
                     "hint": "The NPM user email.", "input_type": "text"},
                    {"key": "password", "label": "Password", "placeholder": "(NPM user password)",
                     "input_type": "password"},
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
                "title": "AdGuard Home credentials",
                "body": "AdGuard Home uses the same username/password as the web admin panel (port 3000 by default).",
                "fields": [
                    {"key": "url", "label": "AdGuard URL", "placeholder": "http://adguard:3000",
                     "hint": "Default port is 3000.", "input_type": "url"},
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
                "title": "Pi-hole URL and API key",
                "body": "Find your API token in Pi-hole Settings → API / Web interface → Show API token.\nEnter the Pi-hole URL and token below.",
                "fields": [
                    {"key": "url", "label": "Pi-hole URL", "placeholder": "http://10.0.0.53",
                     "hint": "IP or hostname of your Pi-hole. No /admin suffix needed.", "input_type": "url"},
                    {"key": "password", "label": "API Token / Admin password",
                     "placeholder": "(paste API token or admin password)",
                     "hint": "Settings → API / Web interface → Show API token.", "input_type": "password"},
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
                "body": "Vauxtra reads Traefik config read-only — it does not write any files.\n\nExpose the API at a reachable URL (e.g. --api.insecure=true or via a dedicated router). Credentials are optional unless you added BasicAuth.",
                "fields": [
                    {"key": "url", "label": "Traefik API URL", "placeholder": "http://traefik:8080",
                     "hint": "The Traefik API endpoint. No auth required unless configured.", "input_type": "url"},
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
    "pihole":            (PiholeProvider,             False),
    "traefik":           (TraefikProvider,            False),
    "cloudflare":        (CloudflareProvider,         True),
    "cloudflare_tunnel": (CloudflareTunnelProvider,   True),
}


def create_provider(provider_row):
    ptype = provider_row["type"]
    url   = provider_row["url"]
    user  = provider_row["username"]
    pwd   = decrypt_secret(provider_row["password"])

    entry = _PROVIDER_REGISTRY.get(ptype)
    if entry is None:
        raise ValueError(f"Provider '{ptype}' not yet supported")

    cls, needs_extra = entry
    if needs_extra:
        extra_raw = provider_row["extra"] if "extra" in provider_row.keys() else "{}"
        try:
            extra = json.loads(extra_raw) if extra_raw else {}
        except Exception:
            extra = {}
        return cls(url, user, pwd, extra)

    return cls(url, user, pwd)
