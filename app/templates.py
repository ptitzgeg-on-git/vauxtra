"""Predefined service templates for common homelab routing patterns.

Templates are stored as constants (no database required).
Each template pre-fills common fields so the user only needs to
supply the subdomain, domain, and target IP.
"""

from typing import TypedDict


class ServiceTemplate(TypedDict):
    id: str
    name: str
    description: str
    forward_scheme: str
    target_port: int
    expose_mode: str
    websocket: bool


SERVICE_TEMPLATES: list[ServiceTemplate] = [
    {
        "id": "webapp-http",
        "name": "Web Application (HTTP)",
        "description": "Standard web app served over plain HTTP. Suitable for internal services behind a reverse proxy that handles TLS.",
        "forward_scheme": "http",
        "target_port": 80,
        "expose_mode": "proxy_dns",
        "websocket": False,
    },
    {
        "id": "webapp-https",
        "name": "Web Application (HTTPS)",
        "description": "Web app that already serves HTTPS internally (e.g., Proxmox, TrueNAS). The reverse proxy will forward without re-encrypting.",
        "forward_scheme": "https",
        "target_port": 443,
        "expose_mode": "proxy_dns",
        "websocket": False,
    },
    {
        "id": "api-backend",
        "name": "API Backend",
        "description": "REST or GraphQL API running on a non-standard port. HTTP traffic, no WebSocket.",
        "forward_scheme": "http",
        "target_port": 3000,
        "expose_mode": "proxy_dns",
        "websocket": False,
    },
    {
        "id": "websocket-app",
        "name": "WebSocket Application",
        "description": "Application requiring persistent WebSocket connections (e.g., Home Assistant, Uptime Kuma).",
        "forward_scheme": "http",
        "target_port": 8080,
        "expose_mode": "proxy_dns",
        "websocket": True,
    },
    {
        "id": "cloudflare-tunnel",
        "name": "Cloudflare Tunnel",
        "description": "Expose a service through Cloudflare Tunnel — no port-forwarding or public IP required.",
        "forward_scheme": "http",
        "target_port": 80,
        "expose_mode": "tunnel",
        "websocket": False,
    },
    {
        "id": "media-server",
        "name": "Media Server",
        "description": "Jellyfin, Plex, or similar media server. HTTP on default port with WebSocket support for live playback.",
        "forward_scheme": "http",
        "target_port": 8096,
        "expose_mode": "proxy_dns",
        "websocket": True,
    },
    {
        "id": "static-files",
        "name": "Static File Server",
        "description": "Nginx or Caddy serving static assets. Plain HTTP, no WebSocket needed.",
        "forward_scheme": "http",
        "target_port": 80,
        "expose_mode": "proxy_dns",
        "websocket": False,
    },
]
