"""Prometheus-compatible /metrics endpoint.

vauxtra_services_total{status="all"} and vauxtra_webhooks_total{state="all"} are roll-ups
of their own family: never sum() a whole family, read `all` or add the parts.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.models import (
    LOG_LEVELS,
    WEBHOOK_DELIVERY_STATUSES,
    get_db,
    normalise_log_level,
)

router = APIRouter()


def _escape(value: str) -> str:
    """Escape a label value for the text exposition format (some values come from the database)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _gauge(name: str, value, labels: dict | None = None) -> str:
    if labels:
        label_str = ",".join(f'{k}="{_escape(str(v))}"' for k, v in labels.items())
        return f"{name}{{{label_str}}} {value}"
    return f"{name} {value}"


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    """Render Vauxtra metrics in Prometheus text format. Unauthenticated, like any scrape path."""
    conn = get_db()
    lines: list[str] = []
    try:
        svc_rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM services GROUP BY status"
        ).fetchall()
        svc_counts = {r["status"]: r["n"] for r in svc_rows}
        total_svcs = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]

        lines.append(
            '# HELP vauxtra_services_total Services by status; status="all" repeats their sum'
        )
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

        # Two passes so each family is one contiguous group with its own HELP and TYPE.
        prov_rows = conn.execute(
            "SELECT type, COUNT(*) as n, SUM(enabled) as en FROM providers GROUP BY type"
        ).fetchall()
        lines.append("# HELP vauxtra_providers_total Total providers grouped by type")
        lines.append("# TYPE vauxtra_providers_total gauge")
        for r in prov_rows:
            lines.append(_gauge("vauxtra_providers_total", r["n"], {"type": r["type"]}))
        lines.append("# HELP vauxtra_providers_enabled Enabled providers grouped by type")
        lines.append("# TYPE vauxtra_providers_enabled gauge")
        for r in prov_rows:
            lines.append(_gauge("vauxtra_providers_enabled", r["en"] or 0, {"type": r["type"]}))

        # Levels are read from the column and normalised like add_log; LOG_LEVELS are
        # zero-filled so absent() alerts do not fire on a quiet hour.
        lines.append("# HELP vauxtra_logs_24h Log entries in the last 24 hours grouped by level")
        lines.append("# TYPE vauxtra_logs_24h gauge")
        log_rows = conn.execute(
            """SELECT level, COUNT(*) as n FROM logs
               WHERE created_at > datetime('now', '-24 hours')
               GROUP BY level"""
        ).fetchall()
        log_counts: dict[str, int] = dict.fromkeys(LOG_LEVELS, 0)
        for r in log_rows:
            # An empty label value would read as no label at all.
            level = normalise_log_level(r["level"]) or "unspecified"
            log_counts[level] = log_counts.get(level, 0) + r["n"]
        for level in sorted(log_counts):
            lines.append(_gauge("vauxtra_logs_24h", log_counts[level], {"level": level}))

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

        wh_total = conn.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0]
        wh_enabled = conn.execute("SELECT COUNT(*) FROM webhooks WHERE enabled=1").fetchone()[0]
        lines.append(
            '# HELP vauxtra_webhooks_total Webhooks; state="enabled" is a subset of state="all"'
        )
        lines.append("# TYPE vauxtra_webhooks_total gauge")
        lines.append(_gauge("vauxtra_webhooks_total", wh_total, {"state": "all"}))
        lines.append(_gauge("vauxtra_webhooks_total", wh_enabled, {"state": "enabled"}))

        # Zero-filled and always emitted, like the log levels; unknown statuses are kept.
        lines.append("# HELP vauxtra_webhook_delivery_total Webhook delivery log entries by status")
        lines.append("# TYPE vauxtra_webhook_delivery_total gauge")
        dlq_rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM webhook_delivery_log GROUP BY status"
        ).fetchall()
        dlq_counts: dict[str, int] = dict.fromkeys(WEBHOOK_DELIVERY_STATUSES, 0)
        for r in dlq_rows:
            # `unspecified` rather than an empty label value, for the reason spelt out over
            # the log levels: Prometheus reads `status=""` as the label not being there.
            status = (r["status"] or "").strip() or "unspecified"
            dlq_counts[status] = dlq_counts.get(status, 0) + r["n"]
        for status in sorted(dlq_counts):
            lines.append(_gauge("vauxtra_webhook_delivery_total", dlq_counts[status], {"status": status}))

        # No try/except: init_db creates these tables, and a failing query should show as a
        # failed scrape rather than a silently missing series.
        tpl_count = conn.execute("SELECT COUNT(*) FROM service_templates").fetchone()[0]
        lines.append("# HELP vauxtra_templates_total Total service templates")
        lines.append("# TYPE vauxtra_templates_total gauge")
        lines.append(_gauge("vauxtra_templates_total", tpl_count))

        sv_row = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        if sv_row:
            lines.append("# HELP vauxtra_schema_version Current DB schema version")
            lines.append("# TYPE vauxtra_schema_version gauge")
            lines.append(_gauge("vauxtra_schema_version", sv_row["value"]))

    finally:
        conn.close()

    return "\n".join(lines) + "\n"
