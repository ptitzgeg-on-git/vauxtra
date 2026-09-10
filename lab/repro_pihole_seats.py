#!/usr/bin/env python
"""Witness for the Pi-hole API-seat leak: does an operation give its session back?

Pi-hole v6 caps concurrent API sessions at `webserver.api.max_sessions` (default 16) and
holds each for `webserver.session.timeout` (default 1800s). Vauxtra builds a fresh
`PiholeProvider` for every request -- `create_provider(row)` -- so every call logs in
again. Before the fix only `test_connection` released the session it opened;
`list_rewrites`, `add_rewrite`, `delete_rewrite` and `update_rewrite` did not.

`list_rewrites` is the call the drift check makes on every pass, so sixteen passes empty
the pool and Pi-hole then refuses EVERY login for half an hour -- the operator's own
browser included, because it draws on the same pool.

Two things are measured per call, because they fail independently:
  login  -- did Pi-hole grant a session? (a seat was free)
  seat   -- was it handed back before the provider went out of scope?

A restart does NOT clear the leaked sessions; they are persisted. Run against a Pi-hole
whose pool is empty:
    docker compose rm -sf pihole && docker compose up -d pihole
    python lab/repro_pihole_seats.py
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

from app.providers.pihole import PiholeProvider  # noqa: E402

URL = "http://127.0.0.1:3082"
PW = "vauxtra-lab-pw"
CALLS = 20


def one_call(kind: str) -> tuple[bool, bool, str]:
    """One operation through a brand-new provider, exactly as an API request would.

    Returns (login granted, seat returned, detail).
    """
    p = PiholeProvider(URL, "", PW)

    granted = {"ok": False}
    real_ensure = p._ensure_auth

    def watched_ensure() -> bool:
        # Read the login outcome at the source: after the fix the sid is gone by the
        # time the call returns, so the provider's own state can no longer tell us
        # whether Pi-hole let us in.
        granted["ok"] = real_ensure()
        return granted["ok"]

    p._ensure_auth = watched_ensure

    if kind == "list":
        rows = p.list_rewrites()
        detail = f"{len(rows)} rewrite(s)"
    else:
        ok = p.test_connection()
        detail = "connection ok" if ok else "connection REFUSED"

    return granted["ok"], p._v6_sid is None, detail


def run(kind: str, label: str, count: int) -> tuple[int, int]:
    print(f"-- {label} --")
    first_refusal, leaked = None, 0
    for i in range(1, count + 1):
        granted, returned, detail = one_call(kind)
        if not granted and first_refusal is None:
            first_refusal = i
        if not returned:
            leaked += 1
        print(
            f"  {i:2}. {kind:5} login[{'ok ' if granted else 'REFUSED'}]"
            f" seat[{'returned' if returned else 'LEAKED  '}] {detail}"
        )
    print()
    return first_refusal, leaked


print("Each line is one Vauxtra request that touches Pi-hole.\n")

first_refusal, leaked = run("list", "list_rewrites (the call the drift check makes)", CALLS)

if first_refusal:
    print(f"Pi-hole started refusing logins at call #{first_refusal} of {CALLS}:")
    print("  the seats ran out mid-run (max_sessions defaults to 16).")
else:
    print(f"All {CALLS} calls were granted a session.")
print(f"Sessions left behind: {leaked}/{CALLS}.")

print()
run("test", "test_connection on fresh providers", 5)

if not first_refusal and not leaked:
    print("VERDICT: every operation returned its seat -- the pool never fills.")
else:
    print("VERDICT: seats are still leaking; only the 1800s timeout releases them.")
