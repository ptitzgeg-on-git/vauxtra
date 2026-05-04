# AI Workspace Structure (Dev and Runtime Separation)

## Objective

Keep a clean, explicit separation between:

- Main Vauxtra runtime (application itself)
- Providers lab stack (integration testing only)
- Documentation and automation assets

## Canonical Layout

- `docker-compose.yml`: main runtime stack
- `docker-compose.dev.yml`: main runtime dev overlay
- `lab/providers/docker-compose.yml`: isolated providers lab stack
- `lab/providers/state/`: local provider lab state only
- `scripts/providers-lab.ps1`: one-command lifecycle for providers lab
- `docs/ai/`: guidance documents for AI/dev agents
- `docs/ai/AI_AUTONOMY_SETUP.md`: workspace-level AI autonomy activations and guardrails

## Non-Mixing Rules

1. Never edit `docker-compose.yml` to add provider lab services.
2. Never run providers lab with the same compose project name as main runtime.
3. Keep provider test data under `lab/providers/state` only.
4. Keep app runtime data under normal app data paths only.
5. If an AI agent needs providers for tests, it must use `scripts/providers-lab.ps1`.

## Operator Commands

From repo root:

- Start lab: `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 start`
- Stop lab: `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 stop`
- Status: `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 status`
- Logs: `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 logs`
- Tear down: `powershell -ExecutionPolicy Bypass -File .\scripts\providers-lab.ps1 down`

## AI Agent Guardrails

When an AI coding agent works in this repository:

- It should treat `lab/providers` as test infrastructure only.
- It should not modify provider credentials in tracked files.
- It should avoid destructive cleanup outside `lab/providers/state` unless explicitly requested.
- It should report clearly whether a change targets app runtime or providers lab.
