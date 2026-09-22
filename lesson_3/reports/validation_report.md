# Validation Report — payments.csv

- **Run:** 2026-09-22T03:06:35
- **Source (read-only):** `/sessions/rcw-01pyngfm83zwvzpgezwsydwc/mnt/payments/payments.csv`
- **Rules:** `/sessions/rcw-01pyngfm83zwvzpgezwsydwc/mnt/payments/rules/cleaning_rules.md`
- **Shape:** 87,924 rows × 6 columns — payment_id, user_id, paid_at, amount_usd, plan, is_refunded
- **Date range:** 2021-01-01 → 2023-02-26
- **Nulls:** 0 · **empty strings:** 0

## 1. Confirmed issues

Rules whose check condition is **provably violated** (4 of 6).

### R1 — Shadow duplicate payments (_X)
- **Check:** payment_id !~ ^P\d{8}$ AND base id exists as valid payment
- **Result:** FAIL — **40** record(s) matched
- **Evidence:** 40/40 malformed ids are provable duplicates of an existing valid base payment
- **Examples:** P00073588_X, P00051546_X, P00012740_X, P00038638_X, P00048406_X
- **Changed columns (on fix):** (rows removed)
- **Action:** per rules/cleaning_rules.md — **requires user confirmation before row removal**

### R2 — payment_id format & uniqueness
- **Check:** every payment_id matches ^P\d{8}$ and is unique
- **Result:** FAIL — **40** record(s) matched
- **Evidence:** pattern violations=40, unique=True
- **Examples:** P00073588_X, P00051546_X, P00012740_X, P00038638_X, P00048406_X
- **Changed columns (on fix):** payment_id
- **Action:** per rules/cleaning_rules.md — **requires user confirmation before row removal**

### R3 — Sentinel user U9999999
- **Check:** no user_id == 'U9999999'; all user_id ~ ^U\d{7}$
- **Result:** FAIL — **40** record(s) matched
- **Evidence:** sentinel rows=40, all user_id well-formed=True
- **Examples:** P00073588_X, P00051546_X, P00012740_X
- **Changed columns (on fix):** (rows removed)
- **Action:** per rules/cleaning_rules.md — **requires user confirmation before row removal**

### R5 — Refund revenue-safety (net_amount_usd)
- **Check:** net_amount_usd exists AND =0 iff is_refunded=1
- **Result:** FAIL — **1,597** record(s) matched
- **Evidence:** MISSING net_amount_usd: 1597 refunded rows carry $115,003 positive revenue
- **Changed columns (on fix):** net_amount_usd (added)
- **Action:** per rules/cleaning_rules.md — **requires user confirmation before row removal**

## 2. Assumptions / potential issues

Anomalies / rules needing human judgment. **Not auto-acted on; no rows changed.**

### A1 — Trailing partial month (right-censoring)
- **Evidence:** max paid_at=2023-02-26; 2023-02 has 2989 rows vs median 3849/mo
- **Action:** Exclude/flag final partial month in trend reporting; confirm cutoff
- **Status:** Not a data error — do not delete rows

### A2 — Early cohort (left-censoring)
- **Evidence:** 1251 users first appear in 2021-01 (window start)
- **Action:** Treat window-start month as left-boundary cohort; don't infer tenure from first observed payment
- **Status:** Advisory

### A3 — Scope & currency assumptions
- **Evidence:** no payment-status field; no explicit currency column (USD assumed by name)
- **Action:** Confirm success-only scope and USD with source before cross-dataset joins
- **Status:** Requires human confirmation

## 3. Checks performed

| Rule | Check | Result | Matched |
|---|---|---|---|
| R1 Shadow duplicate payments (_X) | payment_id !~ ^P\d{8}$ AND base id exists as valid payment | FAIL | 40 |
| R2 payment_id format & uniqueness | every payment_id matches ^P\d{8}$ and is unique | FAIL | 40 |
| R3 Sentinel user U9999999 | no user_id == 'U9999999'; all user_id ~ ^U\d{7}$ | FAIL | 40 |
| R4 Type & date-format validity | amount_usd numeric; is_refunded in {0,1}; paid_at valid YYYY-MM-DD | PASS | 0 |
| R5 Refund revenue-safety (net_amount_usd) | net_amount_usd exists AND =0 iff is_refunded=1 | FAIL | 1,597 |
| R6 Plan <-> price coherence | monthly<=>49.0 and annual<=>399.0 | PASS | 0 |

## 4. Canonical metrics (under the rules)

- Gross booked `SUM(amount_usd)`: **$6,393,226**
- Net realized `SUM(amount_usd WHERE is_refunded=0)`: **$6,278,223**
- Refunds: **1,597** · Rows: **87,924** · Distinct users: **19,216**

**Summary:** 2/6 confirmed-issue checks passed; 4 confirmed issue(s), 3 assumption(s). **Source unchanged.**
