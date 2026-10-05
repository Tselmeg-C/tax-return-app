---
name: Bug
about: Something does not work as expected
labels: ["bug"]
---

## What happened

What you saw. Do not paste document contents, Steuer-IDs, tokens or other secrets.

## Expected

What should have happened.

## Steps to reproduce

1. …

**Plan refs:** `plan.md` §…
**Environment:** local / Codespace / Railway, browser or Telegram, commit or deploy

## Acceptance criteria

- [ ] The bug no longer reproduces with the steps above
- [ ] A regression test covers it

## QA / test plan

- Tests to add or run
- Manual steps

## Definition of Done

- [ ] CI green (lint, typecheck, tests, plus eval gate / golden tests where relevant)
- [ ] `CLAUDE.md` / docs updated for anything a future session needs to know
- [ ] Deployed (or merged, before deploy exists)
- [ ] No document contents, Steuer-ID or secrets in logs or fixtures
- [ ] Follow-ups discovered during the session filed as new issues
