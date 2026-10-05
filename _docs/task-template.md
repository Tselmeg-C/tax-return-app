## Goal

One or two sentences on what should be true when this is done.

**Plan refs:** `plan.md` §…
**Depends on:** #ISSUE-NUMBER

## Scope

**In**

- What this task builds

**Out**

- Something that does not belong in this task, moved to #TASK-NUMBER

## ADLC

Only for tasks that touch an LLM feature.

- **Spec:** schemas, prompts or field lists this task defines
- **Eval:** dataset and metrics used
- **Gate:** thresholds that must pass before merge
- **Observe:** metrics to watch after deploy

## Acceptance criteria

- [ ] A statement you can check by looking at the result
- [ ] One line per case, including the awkward ones

## QA / test plan

- Unit, integration, eval or golden tests to run
- Manual steps

## Definition of Done

- [ ] CI green (lint, typecheck, tests, plus eval gate / golden tests where relevant)
- [ ] `CLAUDE.md` / docs updated for anything a future session needs to know
- [ ] Deployed (or merged, before deploy exists)
- [ ] No document contents, Steuer-ID or secrets in logs or fixtures
- [ ] Follow-ups discovered during the session filed as new issues

## User input needed

Only if the task needs something from the user (keys, sample documents, accounts).
