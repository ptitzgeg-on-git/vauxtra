#!/usr/bin/env bash
# Bring the whole lab up from nothing: seed, start, wait, bootstrap.
#
#   ./up.sh          start (or restart) the lab and seed it
#   ./up.sh --fresh  destroy every container and volume first
#
# Then:  python lab/harness.py    (from the repository root)
set -euo pipefail

cd "$(dirname "$0")"

if [ "${1:-}" = "--fresh" ]; then
  echo "== Tearing the lab down =="
  docker compose down -v --remove-orphans
  rm -rf state
fi

# AdGuard rewrites its configuration file as it runs, so the seed is copied into a
# working directory rather than mounted. `state/` is gitignored; `seed/` never changes.
echo "== Seeding AdGuard =="
if [ -f state/adguard/conf/AdGuardHome.yaml ]; then
  echo "  already seeded"
else
  mkdir -p state/adguard/conf
  cp seed/adguard/AdGuardHome.yaml state/adguard/conf/AdGuardHome.yaml
  echo "  seed/adguard/AdGuardHome.yaml -> state/adguard/conf/"
fi

echo "== Starting containers =="
docker compose up -d

# Each of these answers on a different path, and none of them is ready when the container
# reports "started" -- NPM in particular runs migrations first.
echo "== Waiting for the APIs =="
wait_for() {
  local name="$1" url="$2" expect="${3:-200}"
  for _ in $(seq 1 60); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$url" || true)
    if [ "$code" = "$expect" ]; then
      printf '  %-12s ready\n' "$name"
      return 0
    fi
    sleep 1
  done
  printf '  %-12s TIMED OUT (last HTTP %s)\n' "$name" "${code:-none}"
  return 1
}

wait_for technitium "http://127.0.0.1:5380/api/user/login?user=admin&pass=vauxtra-lab-pw"
wait_for powerdns   "http://127.0.0.1:3084/api/v1/servers/localhost" 401
wait_for adguard    "http://127.0.0.1:3080/control/status" 401
wait_for npm        "http://127.0.0.1:3081/api/"
wait_for pihole     "http://127.0.0.1:3082/api/auth" 401
wait_for zoraxy     "http://127.0.0.1:3083/login.html"
wait_for upstream   "http://127.0.0.1:3090/"

echo
./bootstrap.sh
echo
echo "Lab ready. Run the harness from the repository root:  python lab/harness.py"
