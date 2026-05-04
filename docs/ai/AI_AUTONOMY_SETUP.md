# AI Autonomy Setup (Workspace)

Updated: 2026-05-04

## Goal

Enable maximum safe autonomy for coding agents in this workspace, especially for UI validation loops.

## Activated In Workspace

File: `.vscode/settings.json`

```json
{
  "workbench.browser.enableChatTools": true
}
```

### Effect

- Allows agents to access integrated-browser tooling in chat.
- Enables direct page interaction/inspection workflows needed for end-to-end UI checks.

## What Was Not Changed

- No global VS Code user settings were modified.
- No extension was force-installed by automation.
- No security-sensitive policy was relaxed beyond browser chat tooling.

## For Other AIs / Agents

1. Open pages with the integrated browser tools.
2. Use the enabled browser chat tooling to inspect and drive UI steps.
3. Keep tests scoped to workspace services (`127.0.0.1:8888` or project-defined ports).
4. Record any additional activation in this file before using it.

## Current Verification Endpoints

- Backend/UI entrypoint: `http://127.0.0.1:8888`
- Alternate backend test port used during validation: `http://127.0.0.1:8890`

## Change Log

- 2026-05-04: Added workspace setting `workbench.browser.enableChatTools=true` for autonomous UI validation.
