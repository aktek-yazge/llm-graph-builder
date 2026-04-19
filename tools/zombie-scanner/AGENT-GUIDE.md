# Zombie Code Cleanup — Agent Guide

This guide defines the protocol an AI agent follows when asked to identify and
remove dead code from the llm-graph-builder repository.

## Prerequisites

```bash
bash tools/zombie-scanner/run.sh          # full scan
bash tools/zombie-scanner/run.sh --quick  # ruff + knip only (fast, high confidence)
```

Output lands in `.zombie-reports/<timestamp>/` with a `latest` symlink.

## 7-Step Cleanup Protocol

### Step 1 — Generate Report

```bash
bash tools/zombie-scanner/run.sh
```

If the report already exists and is recent (< 1 hour), skip regeneration.

### Step 2 — Read and Triage

Read `.zombie-reports/latest/zombie-report.md`.

Start with **HIGH confidence** findings only (`confidence >= 0.8`). These have
multiple independent tools agreeing the code is dead. Skip LOW confidence items
on the first pass.

### Step 3 — Cross-check with MegaMemory

Before deleting anything, query megamemory:

```
understand("<module or feature name>")
```

If megamemory returns a concept marked as "planned", "in-progress", or the user
has explicitly noted it should stay, **do not delete**. Add it to the whitelist
instead.

### Step 4 — Delete with Surgical Precision

- **Dead files**: delete the file entirely.
- **Dead symbols** (functions, classes, variables): remove the symbol. If the
  file becomes empty or near-empty after removal, consider deleting the file.
- **Unused imports**: remove the import line. Run the formatter afterwards.
- **Unused dependencies**: remove from `package.json` / `pyproject.toml`.

Always delete one logical group at a time (e.g., one module, one component),
then test before proceeding.

### Step 5 — Test Gate

After each deletion group, run the relevant test suite:

| Package | Command |
|---------|---------|
| backend | `cd backend && python -m pytest` |
| agent-builder/backend | `cd agent-builder/backend && python -m pytest` |
| celery_worker | `cd celery_worker && python -m pytest` |
| frontend | `cd frontend && npx tsc --noEmit && npx vite build` |
| agent-builder/frontend | `cd agent-builder/frontend && npx tsc --noEmit && npx vite build` |

If tests fail, revert the deletion and investigate. The finding may be a false
positive — add it to the whitelist.

### Step 6 — Update Knowledge Graph

After successful deletion:

- If the deleted code had a megamemory concept, call `update_concept` to note
  the removal, or `remove_concept` if the entire feature is gone.
- If nothing was tracked in megamemory, no action needed.

### Step 7 — Handle False Positives

For findings that turned out to be false positives:

- **Python**: add the symbol to `tools/zombie-scanner/config/vulture-whitelist.py`
- **Frontend**: add to `ignoreDependencies` or `ignore` in the relevant
  `tools/zombie-scanner/config/knip-*.json`

This prevents the same false positive from appearing in future scans.

## Confidence Scoring

| Sources agreeing | Confidence | Action |
|------------------|-----------|--------|
| 2+ tools | >= 0.80 | Safe to delete (after Step 3 check) |
| 1 tool, strong signal | 0.50-0.79 | Investigate before deleting |
| 1 tool, weak signal | < 0.50 | Likely false positive; whitelist or skip |

## What NOT to Delete

- Symbols in `vulture-whitelist.py` (already verified as needed)
- Code behind feature flags or environment variables
- Test fixtures and helpers
- Type stubs and declaration files (`.d.ts`)
- Entry points (`main.py`, `main.tsx`, `vite.config.ts`)
- Anything megamemory says is "planned" or "in-progress"

## Re-running After Cleanup

After completing a cleanup round, re-run the scanner to verify:

```bash
bash tools/zombie-scanner/run.sh --quick
```

The counts in the summary should decrease. If new findings appear, they were
previously masked by the deleted code's dependencies.
