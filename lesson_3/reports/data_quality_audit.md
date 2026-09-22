# Data Quality Audit — `payments.csv`

**Scope:** Structure, quality, consistency, and reliability-risk review of `payments/payments.csv`.
**File was not modified.** All checks read-only.
**Audit date:** 2026-09-21

---

## Dataset overview

| Property | Value |
|---|---|
| Rows (excl. header) | 87,924 |
| Columns | 6 — `payment_id`, `user_id`, `paid_at`, `amount_usd`, `plan`, `is_refunded` |
| Distinct `payment_id` | 87,924 (all unique) |
| Distinct `user_id` | 19,216 (19,215 real + 1 sentinel `U9999999`) |
| Date range (`paid_at`) | 2021-01-01 → 2023-02-26 |
| Plans | `monthly` (81,967 @ $49.00), `annual` (5,957 @ $399.00) |
| Refund flag | `is_refunded` = 0 (86,327) / 1 (1,597 = 1.82%) |
| Gross revenue (naïve `SUM(amount_usd)`) | **$6,393,226** |

The dataset is unusually regular: no true nulls, no empty strings, plan↔price is perfectly coherent, and subscription cadence is near-perfect (monthly gaps 28–31 days, annual gaps exactly 365 days). Against that clean backdrop, a small set of deliberately malformed records stands out sharply. **The headline risk is not messiness — it is that a handful of contaminated rows silently distort revenue, user counts, and joins while everything around them looks trustworthy.**

---

## 1. Existing problems — confirmed data issues

### 1.1 Forty duplicate "shadow" payment records inflate revenue and counts
**Evidence:** 40 rows carry a `payment_id` ending in `_X` (e.g. `P00073588_X`). For every one of them, the base ID (`P00073588`) **also exists** as a normal row with the same `paid_at`, `amount_usd`, and `plan`. The `_X` row is a copy of a real payment, re-stamped to a placeholder user.
**Affected fields/records:** `payment_id` (40 rows, indices 87884–87923); linked to 40 legitimate base payments.
**Impact:** Payment count is overstated by 40; gross revenue is overstated by **$3,360**. Because `payment_id` is still technically unique (the `_X` suffix), a naïve `COUNT(DISTINCT payment_id)` or `SUM(amount_usd)` will *not* catch the duplication.
**Fix / validation:** Strip the `_X` suffix and treat these as duplicates of their base IDs. Exclude them from all revenue/volume aggregates (recommended), or confirm with the source system whether `_X` denotes a legitimate re-processing event. Clean gross revenue excluding them = **$6,389,866**.

### 1.2 Sentinel/placeholder user `U9999999` pollutes all user-level metrics
**Evidence:** The 40 `_X` rows — and only those rows — are attributed to `user_id = U9999999`, a classic "all nines" sentinel. Their real owners are different users (e.g. `P00073588` belongs to `U0033481`).
**Affected fields/records:** `user_id` (40 rows).
**Impact:** `U9999999` appears as a single "user" with 40 payments and $3,360 of spend — an artificial whale that inflates payment counts per user, distorts ARPU/LTV, and corrupts any per-user cohort or retention analysis. The true users lose these payments' attribution.
**Fix / validation:** Filter `user_id = 'U9999999'` out of all user-level analysis, and (per 1.1) re-associate or drop the underlying records. Add `U9999999` to a sentinel/blocklist in any pipeline.

### 1.3 Malformed `payment_id` format (contract violation)
**Evidence:** 87,884 IDs match `P` + 8 digits (length 9); 40 are length 11 (`_X` suffix). No other format deviations.
**Affected fields/records:** `payment_id` (same 40 rows).
**Impact:** Any downstream system that validates or joins on the `P\d{8}` pattern will reject, mis-key, or drop these rows. If a join keys on the base ID, `_X` rows silently fail to match.
**Fix / validation:** Enforce the `P\d{8}` format at ingestion; quarantine non-conforming IDs rather than loading them.

### 1.4 Refunds are not reflected in the amount — no signed/net value
**Evidence:** 1,597 rows have `is_refunded = 1`, yet their `amount_usd` remains **positive** ($115,003 total). There are zero negative amounts and no separate refund-amount or refund-date column.
**Affected fields/records:** `amount_usd` / `is_refunded` interaction (1,597 rows).
**Impact:** `SUM(amount_usd)` reports **gross booked**, not net revenue. Anyone who sums the amount column without also filtering `is_refunded = 0` overstates realized revenue by **$115,003**. This is the single largest silent revenue distortion in the file.
**Fix / validation:** Define the revenue metric explicitly — net revenue = `SUM(amount_usd WHERE is_refunded = 0)`. Confirm whether a full refund voids the entire amount (assumed here) or allows partials (not representable in the current schema).

---

## 2. Potential issues — anomalies requiring validation

### 2.1 Right-censored final month makes the latest trend look like a decline
**Evidence:** Monthly volumes run ~3,900–4,500 through Jan 2023, then drop to **2,989 in Feb 2023**. The max date is 2023-02-26 — the month is a partial extract, not a real drop.
**Impact:** Any MoM trend, churn, or growth chart ending at Feb 2023 will show a false ~30% decline in the final period and could trigger wrong business conclusions.
**Validation:** Exclude or clearly flag the trailing partial month; annotate the data cutoff. Confirm the true extract date.

### 2.2 Left-censored history — early cohorts have truncated tenure
**Evidence:** 1,251 users have their *first* payment in the first month (2021-01), far above the natural new-user rate for surrounding months. These are almost certainly pre-existing subscribers whose earlier payments predate the extract window.
**Impact:** Tenure, LTV, and "first cohort" analyses will understate the age and lifetime value of Jan-2021 users, and acquisition curves will show a false spike at the window's start.
**Validation:** Treat 2021-01 as a left-boundary cohort; don't compute acquisition or tenure metrics that assume the first observed payment is the true signup.

### 2.3 `amount_usd` stored as floating point for currency
**Evidence:** Values are `49.0` / `399.0` (float), not integer cents or fixed decimals.
**Impact:** Low risk at this cardinality (only two values), but float money is a latent hazard for rounding once discounts, proration, taxes, or currency conversion enter — and for exact-equality joins.
**Validation:** Store money as integer minor units or `DECIMAL`; avoid float equality in downstream logic.

### 2.4 No failed / pending / disputed states represented
**Evidence:** `is_refunded` is the only status signal; there is no payment-status, chargeback, or attempt-outcome field.
**Impact:** It is ambiguous whether the file contains *only successful* charges or a mix. If failed attempts are silently excluded upstream, success-rate and dunning analyses are impossible from this file alone.
**Validation:** Confirm with the source whether `payments.csv` = successful captures only.

---

## 3. Business / reporting threats

| Threat | Mechanism | Magnitude |
|---|---|---|
| **Revenue overstated by refunds** | Summing `amount_usd` without filtering `is_refunded` | **+$115,003** (~1.8% of gross) |
| **Revenue & volume overstated by shadow duplicates** | 40 `_X` rows double-count real payments | **+$3,360 / +40 payments** |
| **Fake "whale" user** | `U9999999` aggregates 40 payments → distorts ARPU, LTV, top-user reports, cohorts | Skews per-user KPIs |
| **False recent decline** | Partial Feb-2023 month read as a real ~30% drop | Wrong trend/churn conclusions |
| **Understated early-cohort value** | Left-censored 2021-01 subscribers | Distorted LTV/tenure/acquisition |

**Combined revenue impact:** naïve gross `SUM(amount_usd)` = **$6,393,226**; true **net realized revenue = $6,274,863** (exclude the 40 duplicates *and* the 1,597 refunds). That is a **$118,363 (1.85%) overstatement** if the raw column is summed as-is — material for any revenue KPI, forecast, or board metric.

**Recommended canonical revenue definition:**
`SUM(amount_usd) WHERE is_refunded = 0 AND payment_id NOT LIKE '%\_X' AND user_id <> 'U9999999'`

---

## 4. Data integration risks

### 4.1 `payment_id` join breakage
The 40 `_X` IDs will not match a base-ID key and will fail `P\d{8}` validation. Joining this file to any payment-detail or ledger table on `payment_id` will drop or mis-map these rows. **Fix:** normalize/quarantine before joining.

### 4.2 `user_id` sentinel breaks user-dimension joins
`U9999999` will not exist in a real `users` dimension, producing 40 orphaned rows (inner join drops them; left join leaves nulls on user attributes). Any `GROUP BY user_id` treats the sentinel as a real user. **Fix:** exclude the sentinel before joining/aggregating.

### 4.3 Type coercion on merge
`is_refunded` (0/1) and `paid_at` (text) load as string/int depending on the reader; `amount_usd` is float. Merging with a dataset that types these differently (boolean refund flag, `DATE` column, `DECIMAL` amount) risks silent join misses or coercion errors. **Fix:** cast explicitly at load — `paid_at`→date, `is_refunded`→boolean, `amount_usd`→decimal.

### 4.4 No stable grain / surrogate for subscription
There is no `subscription_id` or plan-interval marker beyond `plan`. Reconstructing a subscription requires inferring it from `(user_id, plan)` + cadence. Users never mix plans here (0 cases) and never have same-day/same-month duplicate charges, so the inference is currently safe — but it is an assumption a future data load could break. **Fix:** carry a subscription key from the source if this must join to subscription/entitlement data.

### 4.5 No currency field
Only the column name asserts USD; there is no explicit currency code. Merging with multi-currency sources risks treating foreign amounts as USD. **Fix:** add/confirm an explicit `currency` column.

---

## Suggested remediation checklist

1. Drop or re-attribute the 40 `_X` / `U9999999` records before any aggregation or join.
2. Adopt the canonical net-revenue definition above; never sum `amount_usd` raw for a revenue KPI.
3. Flag and exclude the partial trailing month (2023-02) in trend reporting.
4. Treat 2021-01 as a left-censored cohort in tenure/LTV work.
5. Enforce `payment_id` = `P\d{8}` and a sentinel blocklist at ingestion.
6. Cast types explicitly on load (date / boolean / decimal); store money as minor units.
7. Confirm with the source: `_X` semantics, refund partiality, success-only scope, and currency.

---

### Checks performed (all passed, no issues found)
Exact duplicate rows: 0 · duplicate `payment_id`: 0 · null/empty/NA-token values: 0 · unparseable dates: 0 · future dates: 0 · negative amounts: 0 · plan↔price mismatches: 0 · users with mixed plans: 0 · same-day duplicate charges: 0 · out-of-band subscription gaps: 0.
