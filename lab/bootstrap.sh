#!/usr/bin/env bash
# Bring the six lab providers from "container just started" to "Vauxtra can log in".
#
# Four of them cannot be seeded from the compose file alone, and each refuses in its own
# way -- worth knowing before blaming Vauxtra:
#
#   - Zoraxy starts with NO account at all. `/api/auth/login` answers 403 "CSRF token not
#     found" until you read the token out of /login.html, and only then does
#     `/api/auth/register` accept the first user. A second call is rejected with
#     "Root management account already exists".
#   - Nginx Proxy Manager 2.13 does NOT ship admin@example.com/changeme any more. `GET
#     /api/` answers `"setup": false`, and while it does, `POST /api/users` is open and
#     creates the first administrator. After that it needs a token.
#   - Technitium accepts a record only inside a zone it hosts. Vauxtra's provider derives
#     the zone from the last two labels of the hostname, so `vxlab.test` must exist before
#     `app.vxlab.test` can be written.
#   - PowerDNS is authoritative and nothing else: it starts with an empty database and no
#     zone at all, and a PATCH outside a hosted zone is a 404. Its provider looks for the
#     longest zone containing the name, so `vxlab.test` has to be created first. A second
#     creation answers 409, which is the success case on a re-run.
#
# AdGuard and Pi-hole are seeded earlier: AdGuard by the pre-hashed config up.sh copies
# into place (its four-step wizard has no API), Pi-hole by FTLCONF_webserver_api_password.
#
# Idempotent: run it as often as you like.
set -euo pipefail

PW="vauxtra-lab-pw"
ADGUARD="http://127.0.0.1:3080"
NPM="http://127.0.0.1:3081"
PIHOLE="http://127.0.0.1:3082"
ZORAXY="http://127.0.0.1:3083"
TECHNITIUM="http://127.0.0.1:5380"
POWERDNS="http://127.0.0.1:3084"

say() { printf '  %-12s %s\n' "$1" "$2"; }

echo "== Zoraxy =="
if [ "$(curl -sf "$ZORAXY/api/auth/checkLogin" || echo missing)" = "missing" ]; then
  say zoraxy "not answering yet"
else
  jar=$(mktemp)
  token=$(curl -s -c "$jar" "$ZORAXY/login.html" \
    | grep -oE 'zoraxy\.csrf\.Token[^>]*content="[^"]*"' \
    | grep -oE 'content="[^"]*"' | cut -d'"' -f2)
  reply=$(curl -s -b "$jar" -X POST "$ZORAXY/api/auth/register" \
    -H "X-CSRF-Token: $token" -d "username=admin&password=$PW")
  rm -f "$jar"
  say zoraxy "register -> $reply"
fi

echo "== Nginx Proxy Manager =="
setup=$(curl -s "$NPM/api/" | grep -o '"setup":[a-z]*' || true)
if [ "$setup" = '"setup":false' ]; then
  reply=$(curl -s -X POST "$NPM/api/users" -H 'Content-Type: application/json' -d "{
    \"name\":\"Vauxtra Lab\",\"nickname\":\"lab\",\"email\":\"admin@example.com\",
    \"roles\":[\"admin\"],\"is_disabled\":false,
    \"auth\":{\"type\":\"password\",\"secret\":\"$PW\"}}")
  say npm "first user -> $(echo "$reply" | head -c 60)"
else
  say npm "already set up ($setup)"
fi

echo "== Technitium =="
token=$(curl -s "$TECHNITIUM/api/user/login?user=admin&pass=$PW&includeInfo=true" \
  | grep -oE '"token":"[^"]*"' | cut -d'"' -f4)
if [ -z "$token" ]; then
  say technitium "login FAILED"
else
  reply=$(curl -s "$TECHNITIUM/api/zones/create?token=$token&zone=vxlab.test&type=Primary")
  say technitium "zone vxlab.test -> $(echo "$reply" | grep -oE '"status":"[^"]*"')"
fi

echo "== PowerDNS =="
code=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST "$POWERDNS/api/v1/servers/localhost/zones" \
  -H "X-API-Key: $PW" -H 'Content-Type: application/json' \
  -d '{"name":"vxlab.test.","kind":"Native","nameservers":["ns1.vxlab.test."]}')
case "$code" in
  201) say powerdns "zone vxlab.test created" ;;
  409) say powerdns "zone vxlab.test already there" ;;
  *)   say powerdns "zone vxlab.test FAILED (HTTP $code)" ;;
esac

echo "== Reachability =="
say adguard   "$(curl -s -o /dev/null -w '%{http_code}' -u "admin:$PW" "$ADGUARD/control/status")"
say pihole    "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$PIHOLE/api/auth" -H 'Content-Type: application/json' -d "{\"password\":\"$PW\"}")"
say npm       "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$NPM/api/tokens" -H 'Content-Type: application/json' -d "{\"identity\":\"admin@example.com\",\"secret\":\"$PW\"}")"
say zoraxy    "$(curl -s -o /dev/null -w '%{http_code}' "$ZORAXY/api/auth/checkLogin")"
say technitium "$(curl -s -o /dev/null -w '%{http_code}' "$TECHNITIUM/api/user/login?user=admin&pass=$PW")"
say powerdns  "$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $PW" "$POWERDNS/api/v1/servers/localhost")"
