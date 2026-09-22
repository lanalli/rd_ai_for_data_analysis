# Cleaning Rules — `payments.csv`

Derived from `reports/data_quality_audit.md`. Each rule states its **check condition**, the **action** taken on violation, the **columns changed**, and the **validation check** that confirms the rule held after cleaning.

**Inputs / outputs**
- Source (never modified): `payments/payments.csv` — 87,924 rows
- Cleaned output: `payments/payments_clean.csv` — 87,884 rows, 7 columns
- Run log: `payments/reports/cleaning_log.txt`

**Design principles**
- The raw file is preserved so the workflow is reproducible and auditable.
- Only **confirmed defects** (audit §1) trigger row removal or value edits. **Potential issues and censoring** (audit §2) are advisory — they are documented, not silently altered, because deleting real rows would destroy information.
- Refunded rows are **kept** (they are real events); their revenue impact is handled by a derived column, not by deletion.

---

## Rule 1 — Remove shadow duplicate payments (`_X`)

- **Check condition:** `payment_id` does **not** match `^P\d{8}$` (i.e. carries the `_X` suffix). Confirmed duplicate: the base id (`payment_id` with `_X` stripped) already exists as a valid row with identical `paid_at`, `amount_usd`, `plan`.
- **Action if violated:** **Drop the row.** It double-counts a real payment (audit §1.1). 40 rows matched; all 40 confirmed to have an existing valid base payment.
- **Changed columns:** none altered in place — whole rows removed. Row count 87,924 → 87,884.
- **Validation check:** `no _X suffix remains` (PASS); `all payment_id match ^P\d{8}$` (PASS); `removed exactly the 40 _X ids` (PASS); `no clean rows are new / all exist in original` (PASS).

## Rule 2 — Enforce `payment_id` format & uniqueness

- **Check condition:** every `payment_id` matches `^P\d{8}$` and is unique.
- **Action if violated:** non-conforming ids are removed by Rule 1; a residual duplicate id would be quarantined for manual review (none occurred).
- **Changed columns:** `payment_id` (rows removed; no value rewriting).
- **Validation check:** `all payment_id match ^P\d{8}$` (PASS); `payment_id unique` (PASS).

## Rule 3 — Remove sentinel user `U9999999`

- **Check condition:** `user_id == 'U9999999'` (placeholder, not a real user; audit §1.2), and every `user_id` matches `^U\d{7}$`.
- **Action if violated:** **Drop the row.** In this file the sentinel rows are exactly the 40 `_X` rows, so Rule 1 and Rule 3 remove the same set (union = 40). Kept as a distinct rule so the sentinel is caught even if it ever appears on a well-formed id.
- **Changed columns:** none in place — rows removed.
- **Validation check:** `no U9999999 sentinel remains` (PASS); `all user_id match ^U\d{7}$` (PASS).

## Rule 4 — Normalize data types & date format

- **Check condition:** `amount_usd` parses as numeric; `is_refunded` ∈ {0,1} as integer; `paid_at` is a valid `YYYY-MM-DD` date.
- **Action if violated:** cast `amount_usd`→float, `is_refunded`→int (0/1), `paid_at`→canonical ISO `YYYY-MM-DD`. Any value that fails to parse would be quarantined (none did). Addresses audit §2.3 and §4.3.
- **Changed columns:** `amount_usd`, `is_refunded`, `paid_at` (type normalized; values unchanged, dates re-serialized to ISO).
- **Validation check:** `all paid_at ISO date & parseable` (PASS); `amount_usd in {49.0,399.0}` (PASS); `is_refunded in {0,1}` (PASS); `no nulls/empties` (PASS).

## Rule 5 — Make refunds revenue-safe (`net_amount_usd`)

- **Check condition:** for every row, realized revenue must be directly summable without a separate filter. Refunded rows (`is_refunded == 1`) must not contribute positive revenue (audit §1.4).
- **Action if violated:** add derived column `net_amount_usd = amount_usd` when `is_refunded == 0`, else `0.0`. `amount_usd` is retained as gross/booked. Net revenue = `SUM(net_amount_usd)`; gross = `SUM(amount_usd)`.
- **Changed columns:** **added** `net_amount_usd`. `amount_usd` and `is_refunded` unchanged.
- **Validation check:** `net=0 exactly when refunded` (PASS); `net=gross when not refunded` (PASS). Result: gross $6,389,866; net $6,274,863; 1,597 refunds.

## Rule 6 — Plan ↔ price coherence (integrity guard)

- **Check condition:** `plan == 'monthly'` ⇔ `amount_usd == 49.0`; `plan == 'annual'` ⇔ `amount_usd == 399.0`.
- **Action if violated:** flag row for manual review (do not auto-correct — cannot know which field is wrong). No violations found; guard retained for future loads.
- **Changed columns:** none.
- **Validation check:** `plan<->price coherent` (PASS).

---

## Advisory rules (documented, no row changes)

These are audit §2 items. They distort analysis but are not data errors to delete; handle them at query/report time.

- **Rule A1 — Trailing partial month (right-censoring).** *Check:* max `paid_at` (2023-02-26) falls mid-month; Feb-2023 has ~2,989 rows vs ~4,000 typical. *Action:* exclude or flag the final partial month in any trend/MoM/churn chart; annotate the data cutoff. *Validation:* confirm reporting layer filters or labels the boundary month.
- **Rule A2 — Early cohort (left-censoring).** *Check:* first observed `paid_at` = 2021-01 for 1,251 users, above the natural new-user rate. *Action:* treat 2021-01 as a left-boundary cohort; do not infer signup date or tenure from the first observed payment. *Validation:* tenure/LTV logic excludes the boundary cohort or marks it censored.
- **Rule A3 — Scope & currency assumptions.** *Check:* no payment-status field (success/failed/disputed) and no explicit currency column. *Action:* confirm with source that the file is successful captures only and all amounts are USD; add a `currency` column if merging multi-currency data. *Validation:* documented source confirmation before cross-dataset joins.

---

## Canonical metrics on the cleaned file

```
Gross booked revenue : SUM(amount_usd)                       = $6,389,866
Net realized revenue : SUM(net_amount_usd)                   = $6,274,863
                       (== SUM(amount_usd WHERE is_refunded=0))
Paid transactions    : COUNT(*)                              = 87,884
Refunds              : COUNT(*) WHERE is_refunded = 1        = 1,597 (1.82%)
Distinct users       : COUNT(DISTINCT user_id)               = 19,215
```

## Post-clean validation summary

18/18 checks PASS, including: original file unchanged (87,924 rows); cleaned = 87,884 rows / 7 columns; ids conform and unique; no sentinel; no nulls; types & dates normalized; net-revenue logic correct; plan↔price coherent; no rows fabricated (all cleaned ids exist in the original) and exactly the 40 `_X` ids removed.
