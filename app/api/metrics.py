"""Prometheus-compatible /metrics endpoint."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.models import get_db

router = APIRouter()


def _gauge(name: str, value, labels: dict | None = None) -> str:
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{label_str}}} {value}"
    return f"{name} {value}"


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    """
    Expose Vauxtra operational metrics in Prometheus text exposition format.
    Intended to be scraped by a Prometheus instance.
    No authentication required (standard Prometheus scrape path).
    """
    conn = get_db()
    lines: list[str] = []
    try:
        # ── Services ─────────────────────────────────────────────────────────
        svc_rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM services GROUP BY status"
        ).fetchall()
        svc_counts = {r["status"]: r["n"] for r in svc_rows}
        total_svcs = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]

        lines.append("# HELP vauxtra_services_total Total services grouped by status")
        lines.append("# TYPE vauxtra_services_total gauge")
        for status in ("ok", "error", "unknown"):
            lines.append(_gauge("vauxtra_services_total", svc_counts.get(status, 0), {"status": status}))
        lines.append(_gauge("vauxtra_services_total", total_svcs, {"status": "all"}))

        enabled_svcs = conn.execute("SELECT COUNT(*) FROM services WHERE enabled=1").fetchone()[0]
        disabled_svcs = total_svcs - enabled_svcs
        lines.append("# HELP vauxtra_services_enabled Services split by enabled/disabled state")
        lines.append("# TYPE vauxtra_services_enabled gauge")
        lines.append(_gauge("vauxtra_services_enabled", enabled_svcs, {"state": "enabled"}))
        lines.append(_gauge("vauxtra_services_enabled", disabled_svcs, {"state": "disabled"}))

        # ── Providers ────────────────────────────────────────────────────────
        lines.append("# HELP vauxtra_providers_total Total providers grouped by type")
        lines.append("# TYPE vauxtra_providers_total gauge")
        prov_rows = conn.execute(
            "SELECT type, COUNT(*) as n, SUM(enabled) as en FROM providers GROUP BY type"
        ).fetchall()
        for r in prov_rows:
            lines.append(_gauge("vauxtra_providers_total", r["n"], {"type": r["type"]}))
            lines.append(_gauge("vauxtra_providers_enabled", r["en"] or 0, {"type": r["type"]}))

        # ── Logs (last 24 h) ──────────────────────────────────────────────────
        lines.append("# HELP vauxtra_logs_24h Log entries in the last 24 hours grouped by level")
        lines.append("# TYPE vauxtra_logs_24h gauge")
        log_rows = conn.execute(
            """SELECT level, COUNT(*) as n FROM logs
               WHERE created_at > datetime('now', '-24 hours')
               GROUP BY level"""
        ).fetchall()
        log_counts = {r["level"]: r["n"] for r in log_rows}
        for level in ("info", "ok", "warn", "error"):
            lines.append(_gauge("vauxtra_logs_24h", log_counts.get(level, 0), {"level": level}))

        # ── Uptime events (last 24 h) ─────────────────────────────────────────
        lines.append("# HELP vauxtra_uptime_events_24h Uptime check results in the last 24 hours")
        lines.append("# TYPE vauxtra_uptime_events_24h gauge")
        uptime_rows = conn.execute(
            """SELECT status, COUNT(*) as n FROM uptime_events
               WHERE created_at > datetime('now', '-24 hours')
               GROUP BY status"""
        ).fetchall()
        uptime_counts = {r["status"]: r["n"] for r in uptime_rows}
        for status in ("ok", "error"):
            lines.append(_gauge("vauxtra_uptime_events_24h", uptime_counts.get(status, 0), {"status": status}))

        # ── Webhooks ──────────────────────────────────────────────────────────
        wh_total = conn.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0]
        wh_enabled = conn.execute("SELECT COUNT(*) FROM webhooks WHERE enabled=1").fetchone()[0]
        lines.append("# HELP vauxtra_webhooks_total Total configured webhooks")
        lines.append("# TYPE vauxtra_webhooks_total gauge")
        lines.append(_gauge("vauxtra_webhooks_total", wh_total, {"state": "all"}))
        lines.append(_gauge("vauxtra_webhooks_total", wh_enabled, {"state": "enabled"}))

        # ── Webhook delivery log ──────────────────────────────────────────────
        try:
            dlq_rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM webhook_delivery_log GROUP BY status"
            ).fetchall()
            if dlq_rows:
                lines.append("# HELP vauxtra_webhook_delivery_total Webhook delivery log entries by status")
                lines.append("# TYPE vauxtra_webhook_delivery_total gauge")
                for r in dlq_rows:
                    lines.append(_gauge("vauxtra_webhook_delivery_total", r["n"], {"status": r["status"]}))
        except Exception:
            pass

        # ── Templates ─────────────────────────────────────────────────────────
        try:
            tpl_count = conn.execute("SELECT COUNT(*) FROM service_templates").fetchone()[0]
            lines.append("# HELP vauxtra_templates_total Total service templates")
            lines.append("# TYPE vauxtra_templates_total gauge")
            lines.append(_gauge("vauxtra_templates_total", tpl_count))
        except Exception:
            pass

        # ── Schema version ────────────────────────────────────────────────────
        sv_row = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        if sv_row:
            lines.append("# HELP vauxtra_schema_version Current DB schema version")
            lines.append("# TYPE vauxtra_schema_version gauge")
            lines.append(_gauge("vauxtra_schema_version", sv_row["value"]))

    finally:
        conn.close()

    return "\n".join(lines) + "\n"
