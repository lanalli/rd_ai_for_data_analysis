---
name: "validate-dataset"
description: "Profile a data table, apply rules from a /rules folder, and produce a validation report separating confirmed issues from assumptions — never changing source data without validation. Use to QA/validate a CSV or dataset."
---

# validate-dataset

Validate a dataset against documented rules and produce a report. Reusable across tables — the logic lives in the rules, not in this skill.

## Inputs
- **Required:** path to one or more tables (CSV/TSV/Parquet/Excel). Accept a file, a glob, or a folder of tables.
- **Optional:** `--rules <dir>` (default: `rules/` next to the data or in the project root) and `--out <dir>` (default: `reports/`).
- If no path is given, ask for it. If the rules folder is missing or empty, say so and offer to run profiling only.

## Procedure (in order — never skip or reorder)
1. **Profile first (read-only).** For each table: row/column counts, dtypes, null/empty/NA-token counts, distinct values, value distributions, format checks (ids, dates, enums), duplicates (exact and logical), outliers, and cross-field/relationship coherence. Load the raw file; do not write to it.
2. **Load rules** from the rules folder. Each rule file describes: **check condition · action on violation · changed columns · validation check**. Parse every rule; if a rule is ambiguous, flag it rather than guessing.
3. **Evaluate each rule** against the profiled data. Record per rule: pass/fail, matched record count, and example offending rows.
4. **Classify findings** into **Confirmed issues** (a rule's check condition is provably violated) vs **Assumptions / potential issues** (anomalies, censoring, scope/currency questions, or rules needing human confirmation). Keep these in separate sections — never merge them.
5. **Write the validation report** to `<out>/validation_report.md` (one section per table if multiple).
6. **Do NOT modify source data.** This skill validates and reports only. Cleaning is a separate step: if the user asks to clean, apply only confirmed-issue rules, write a **new** file (never overwrite the source), log changes, and re-run this validation on the output.

## Safety rules (hard requirements)
- Treat every input table as **read-only**. Never overwrite or edit the source.
- **Confirm with the user before removing or deleting** any rows, columns, or files. State what and how many. Before calling rows duplicates, prove it (e.g. a valid base key already exists with matching fields).
- Apply only **documented** rules from the rules folder — no ad-hoc edits.
- Do all checks with a **script** (Python/pandas) so runs are reproducible; log what was checked.
- Report success only after the validation pass completes.

## Rules folder format
One rule per entry, each stating:
- **Check condition** — the predicate that must hold.
- **Action if violated** — remove / flag / cast / quarantine (removal always requires user confirmation).
- **Changed columns** — which fields an action touches (none for validation-only).
- **Validation check** — how to confirm the rule holds after any fix.
Rules with destructive actions are treated as **confirmed** only when their check condition is objectively testable; otherwise list them under assumptions.

## Validation report format
```
# Validation Report — <table>
Run date · source path · row/column counts

## 1. Confirmed issues        # rule violated, provable — rule, evidence, count, changed columns
## 2. Assumptions / potential # anomalies & rules needing human confirmation — do not auto-act
## 3. Checks performed        # every rule + pass/fail + matched count
## 4. Canonical metrics       # key figures computed under the rules (if defined)
Summary: N/N checks passed; source unchanged.
```

## Suggested layout (optional bundled scripts)
```
validate-dataset/
  SKILL.md
  scripts/
    profile.py     # read-only profiling
    run_rules.py   # evaluate rules -> findings JSON
    report.py      # render validation_report.md
```

## Launching
- Natural language: "validate <path> using the rules in /rules" or "/validate-dataset path=data/payments.csv".
- Programmatic: `python scripts/run_rules.py --data <path> --rules rules/ --out reports/`.
Outputs: `reports/validation_report.md` (+ log). Source data is never modified.