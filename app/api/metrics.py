"""Prometheus-compatible /metrics endpoint.

Two families here carry a roll-up member beside the parts it already contains:
`vauxtra_services_total{status="all"}` and `vauxtra_webhooks_total{state="all"}`. Their
HELP text says so, because `sum()` over either family answers with twice the truth. Read
the `all` series on its own, or add up the parts, but never the whole family at once.
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
    """A label value as the text exposition format wants it.

    Every label written below used to be a literal from this file, so nothing needed
    quoting and nothing was quoted. The log levels are now read out of the column instead
    of from a tuple written above them -- which is the point of that change, a level this
    file has never heard of is reported rather than dropped -- and a value arriving from a
    table is a value that can hold a quote. One unescaped quote does not spoil its own
    line: it closes the label set early and leaves the rest of the scrape unparseable.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _gauge(name: str, value, labels: dict | None = None) -> str:
    if labels:
        label_str = ",".join(f'{k}="{_escape(str(v))}"' for k, v in labels.items())
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

        # ── Providers ────────────────────────────────────────────────────────
        # Two passes over the same rows rather than one pass emitting both families. The
        # text exposition format asks for every sample of a family in a single group behind
        # its own HELP and TYPE, and OpenMetrics refuses interleaving outright; alternating
        # them also left `vauxtra_providers_enabled` with no HELP and no TYPE at all, the
        # only family in this file without them and a lint failure under `promtool`.
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

        # ── Logs (last 24 h) ──────────────────────────────────────────────────
        # `add_log` folds "warn" into "warning" before the insert, so the column holds
        # "warning" and this loop asked for a spelling nothing writes. Two warnings in the
        # database were reported here as `level="warn" 0`, and the bucket that held them was
        # never emitted at all: alerting on warnings off this endpoint watched a line that
        # could only ever read flat. `GET /api/logs` has folded the two spellings together
        # since the normalisation landed -- this was the reader that had not.
        #
        # The vocabulary is no longer written out a second time here, either. What the column
        # holds is what gets counted, folded through the same function the insert uses, so a
        # level this file has never heard of is reported instead of dropped. `LOG_LEVELS` is
        # zero-filled on top of that: a quiet hour has to read 0 rather than go absent, or an
        # `absent()` alarm fires on a healthy instance.
        lines.append("# HELP vauxtra_logs_24h Log entries in the last 24 hours grouped by level")
        lines.append("# TYPE vauxtra_logs_24h gauge")
        log_rows = conn.execute(
            """SELECT level, COUNT(*) as n FROM logs
               WHERE created_at > datetime('now', '-24 hours')
               GROUP BY level"""
        ).fetchall()
        log_counts: dict[str, int] = dict.fromkeys(LOG_LEVELS, 0)
        for r in log_rows:
            # A row with no level would otherwise be published as `level=""`, and Prometheus
            # reads an empty label value as the label not being there at all -- the count
            # would land on a series with no `level`, beside the ones that have one.
            level = normalise_log_level(r["level"]) or "unspecified"
            log_counts[level] = log_counts.get(level, 0) + r["n"]
        for level in sorted(log_counts):
            lines.append(_gauge("vauxtra_logs_24h", log_counts[level], {"level": level}))

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
        lines.append(
            '# HELP vauxtra_webhooks_total Webhooks; state="enabled" is a subset of state="all"'
        )
        lines.append("# TYPE vauxtra_webhooks_total gauge")
        lines.append(_gauge("vauxtra_webhooks_total", wh_total, {"state": "all"}))
        lines.append(_gauge("vauxtra_webhooks_total", wh_enabled, {"state": "enabled"}))

        # ── Webhook delivery log ──────────────────────────────────────────────
        # Zero-filled from `WEBHOOK_DELIVERY_STATUSES`, and published whether or not the
        # table holds anything, for the reason `vauxtra_logs_24h` is: an absent series and a
        # count of zero are the same picture to a person and opposite answers to `absent()`.
        # This family used to be emitted only `if dlq_rows`, so the instance that had never
        # failed a delivery answered an alarm on failures with no-data. `docs/HOWTO.md`
        # declared the vocabulary `pending, delivered, failed` beside it all the while, a
        # promise kept only while the table happened to hold all three at once -- which is
        # what the fixture in `tests/test_metrics_endpoint.py` was seeding.
        #
        # A status the column holds that this build has never heard of is counted beside the
        # three rather than dropped, again as the log levels are.
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

        # ── Templates ─────────────────────────────────────────────────────────
        # Both queries above and below ran inside `except Exception: pass` until now. They
        # were written in the same commit that created the two tables, when an instance
        # could still predate them; `init_db()` has created both with `CREATE TABLE IF NOT
        # EXISTS` on every boot since, and it runs in the lifespan before a scrape can
        # arrive. What the guard could still catch is a real failure, and what it did with
        # one was drop the series without a word -- the other half of the same defect, since
        # zero-filling a family that a swallowed error can still make vanish is half a
        # remedy. The six sibling sections in this function have never had a guard: a broken
        # query surfaces as a 500, which a scrape reads as `up 0` and an operator can see.
        tpl_count = conn.execute("SELECT COUNT(*) FROM service_templates").fetchone()[0]
        lines.append("# HELP vauxtra_templates_total Total service templates")
        lines.append("# TYPE vauxtra_templates_total gauge")
        lines.append(_gauge("vauxtra_templates_total", tpl_count))

        # ── Schema version ────────────────────────────────────────────────────
        sv_row = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        if sv_row:
            lines.append("# HELP vauxtra_schema_version Current DB schema version")
            lines.append("# TYPE vauxtra_schema_version gauge")
            lines.append(_gauge("vauxtra_schema_version", sv_row["value"]))

    finally:
        conn.close()

    return "\n".join(lines) + "\n"
