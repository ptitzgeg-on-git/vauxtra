# Providers Lab (Isolated from Main Vauxtra)

This folder contains a standalone providers stack used only for Vauxtra integration tests.

## Scope

- Pi-hole: http://localhost:18081/admin
- AdGuard Home: http://localhost:13000
- Nginx Proxy Manager: http://localhost:18082
- Technitium DNS: http://localhost:15389
- Traefik dashboard: http://localhost:18090
- Whoami via Traefik entrypoint: http://localhost:18083

Main Vauxtra app remains separate on http://localhost:8888.

## Rules

- Do not reuse this compose file for production.
- Do not mix this stack with the main runtime compose.
- Keep all test state under `lab/providers/state`.

## Quick Start

1. Rebuild the lab with known test credentials from repo root:
   - `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 reset`
2. Start again later without wiping state:
   - `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 start`
3. Check status with:
   - `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 status`

The script bootstraps browser-ready test accounts and prints the current URLs and credentials summary after `start`, `restart`, `reset`, and `status`.

## Stop

- `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 stop`

## Tear Down

- `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 down`

## Reset

- `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 reset`

`reset` only deletes `lab/providers/state` and recreates the isolated lab with deterministic test credentials. It does not touch the main Vauxtra runtime.
