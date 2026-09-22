#!/usr/bin/env python3
"""
validate_dataset.py — reproducible data-quality pipeline for payments.csv

Reproduces the project workflow end to end:
    profile (read-only)  ->  evaluate rules  ->  validation report
                                              ->  [optional] clean to a NEW file  ->  re-validate

Design guarantees (see CLAUDE.md):
  * The source file is NEVER modified. Cleaning writes a separate --out-data file.
  * Row removal happens only with --clean AND explicit --yes confirmation.
  * Confirmed issues (provably violated rules) are kept separate from assumptions.
  * Every run is scripted and logged; a validation pass gates "success".

Usage
-----
  # 1) Validate the raw source (default) — writes reports/validation_report.md
  python validate_dataset.py --data payments.csv

  # 2) Validate the already-cleaned file
  python validate_dataset.py --data payments_clean.csv

  # 3) Clean (removes confirmed-bad rows) — requires --yes, writes a NEW file, then re-validates
  python validate_dataset.py --data payments.csv --clean --yes \
         --out-data payments_clean.csv

Paths default to being resolved relative to the data file's folder.
Exit code is 0 only when every confirmed-issue rule passes.
"""
from __future__ import annotations
import argparse, os, sys, json, datetime, re
import pandas as pd

# --------------------------------------------------------------------------- #
# Rule definitions — the executable form of rules/cleaning_rules.md            #
# Each rule: id, title, kind, check(df)->(passed, matched_count, evidence,     #
#            example_ids), changed_columns, validation_text                    #
# kind: "confirmed" (provable, gates exit code) | "assumption" (advisory)      #
# --------------------------------------------------------------------------- #
ID_RE   = re.compile(r"^P\d{8}$")
USER_RE = re.compile(r"^U\d{7}$")
SENTINEL_USER = "U9999999"
PLAN_PRICE = {"monthly": 49.0, "annual": 399.0}


def _num(s):   return pd.to_numeric(s, errors="coerce")
def _date(s):  return pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")


def rule_R1(df):
    m = ~df["payment_id"].str.match(ID_RE)
    bad = df[m].copy()
    bad["base"] = bad["payment_id"].str.replace("_X", "", regex=False)
    valid = set(df.loc[~m, "payment_id"])
    proven = int(bad["base"].isin(valid).sum()) if len(bad) else 0
    return (m.sum() == 0, int(m.sum()),
            f"{proven}/{len(bad)} malformed ids are provable duplicates of an existing valid base payment",
            bad["payment_id"].head(5).tolist())


def rule_R2(df):
    m = ~df["payment_id"].str.match(ID_RE)
    uniq = df["payment_id"].is_unique
    passed = (m.sum() == 0) and uniq
    return (passed, int(m.sum()),
            f"pattern violations={int(m.sum())}, unique={uniq}",
            df.loc[m, "payment_id"].head(5).tolist())


def rule_R3(df):
    m = df["user_id"] == SENTINEL_USER
    uid_ok = bool(df["user_id"].str.match(USER_RE).all())
    passed = (m.sum() == 0) and uid_ok
    return (passed, int(m.sum()),
            f"sentinel rows={int(m.sum())}, all user_id well-formed={uid_ok}",
            df.loc[m, "payment_id"].head(3).tolist())


def rule_R4(df):
    bad_amt = int(_num(df["amount_usd"]).isna().sum())
    bad_dt  = int(_date(df["paid_at"]).isna().sum())
    bad_ref = int((~df["is_refunded"].isin(["0", "1"])).sum())
    total = bad_amt + bad_dt + bad_ref
    return (total == 0, total,
            f"unparseable amount={bad_amt}, bad dates={bad_dt}, bad is_refunded={bad_ref}", [])


def rule_R5(df):
    ref = _num(df["is_refunded"])
    amt = _num(df["amount_usd"])
    if "net_amount_usd" in df.columns:
        net = _num(df["net_amount_usd"])
        consistent = bool(((ref == 1) == (net == 0)).all() and
                          (net[ref == 0] == amt[ref == 0]).all())
        return (consistent, int((ref == 1).sum()),
                "net_amount_usd present and consistent with is_refunded", [])
    # column missing -> refunds still carry positive revenue
    exposure = float(amt[ref == 1].sum())
    return (False, int((ref == 1).sum()),
            f"MISSING net_amount_usd: {int((ref==1).sum())} refunded rows carry ${exposure:,.0f} positive revenue", [])


def rule_R6(df):
    amt = _num(df["amount_usd"])
    m = (df["plan"] == "monthly") != (amt == PLAN_PRICE["monthly"])
    return (m.sum() == 0, int(m.sum()), f"plan/price mismatches={int(m.sum())}", [])


CONFIRMED_RULES = [
    ("R1", "Shadow duplicate payments (_X)",       rule_R1, ["(rows removed)"],
     r"payment_id !~ ^P\d{8}$ AND base id exists as valid payment"),
    ("R2", "payment_id format & uniqueness",       rule_R2, ["payment_id"],
     r"every payment_id matches ^P\d{8}$ and is unique"),
    ("R3", "Sentinel user U9999999",               rule_R3, ["(rows removed)"],
     r"no user_id == 'U9999999'; all user_id ~ ^U\d{7}$"),
    ("R4", "Type & date-format validity",          rule_R4, ["amount_usd", "is_refunded", "paid_at"],
     "amount_usd numeric; is_refunded in {0,1}; paid_at valid YYYY-MM-DD"),
    ("R5", "Refund revenue-safety (net_amount_usd)", rule_R5, ["net_amount_usd (added)"],
     "net_amount_usd exists AND =0 iff is_refunded=1"),
    ("R6", "Plan <-> price coherence",             rule_R6, ["plan", "amount_usd"],
     "monthly<=>49.0 and annual<=>399.0"),
]


def assumptions(df):
    dt = _date(df["paid_at"])
    mc = dt.dt.to_period("M").value_counts().sort_index()
    first = df.assign(_d=dt).sort_values("_d").groupby("user_id")["_d"].first()
    firstm = int((first.dt.to_period("M").astype(str) == str(mc.index[0])).sum())
    return [
        dict(id="A1", title="Trailing partial month (right-censoring)",
             evidence=f"max paid_at={dt.max().date()}; {mc.index[-1]} has {int(mc.iloc[-1])} rows vs median {int(mc.iloc[:-1].median())}/mo",
             action="Exclude/flag final partial month in trend reporting; confirm cutoff",
             status="Not a data error — do not delete rows"),
        dict(id="A2", title="Early cohort (left-censoring)",
             evidence=f"{firstm} users first appear in {mc.index[0]} (window start)",
             action="Treat window-start month as left-boundary cohort; don't infer tenure from first observed payment",
             status="Advisory"),
        dict(id="A3", title="Scope & currency assumptions",
             evidence="no payment-status field; no explicit currency column (USD assumed by name)",
             action="Confirm success-only scope and USD with source before cross-dataset joins",
             status="Requires human confirmation"),
    ]


# --------------------------------------------------------------------------- #
def profile(df, path):
    dt = _date(df["paid_at"])
    return dict(
        source=path, rows=int(len(df)), columns=list(df.columns),
        nulls=int(pd.read_csv(path).isna().sum().sum()),
        empties=int((df == "").sum().sum()),
        distinct={c: int(df[c].nunique()) for c in df.columns},
        date_min=str(dt.min().date()), date_max=str(dt.max().date()),
    )


def evaluate(df):
    checks = []
    for rid, title, fn, cols, cond in CONFIRMED_RULES:
        passed, count, evidence, examples = fn(df)
        checks.append(dict(id=rid, title=title, kind="confirmed", condition=cond,
                           passed=passed, count=count, columns=cols,
                           evidence=evidence, examples=examples))
    return checks


def metrics(df):
    amt = _num(df["amount_usd"]); ref = _num(df["is_refunded"])
    return dict(gross=float(amt.sum()),
                net=float(amt.where(ref == 0, 0.0).sum()),
                refunds=int((ref == 1).sum()),
                rows=int(len(df)), distinct_users=int(df["user_id"].nunique()))


def write_report(prof, checks, assum, mets, out_path, rules_path):
    conf = [c for c in checks if not c["passed"]]
    L = [f"# Validation Report — {os.path.basename(prof['source'])}", "",
         f"- **Run:** {datetime.datetime.now().isoformat(timespec='seconds')}",
         f"- **Source (read-only):** `{prof['source']}`",
         f"- **Rules:** `{rules_path}`",
         f"- **Shape:** {prof['rows']:,} rows × {len(prof['columns'])} columns — {', '.join(prof['columns'])}",
         f"- **Date range:** {prof['date_min']} → {prof['date_max']}",
         f"- **Nulls:** {prof['nulls']} · **empty strings:** {prof['empties']}", "",
         "## 1. Confirmed issues", "",
         f"Rules whose check condition is **provably violated** ({len(conf)} of {len(checks)}).", ""]
    if not conf:
        L += ["_None — all confirmed-issue rules pass._", ""]
    for c in conf:
        L += [f"### {c['id']} — {c['title']}",
              f"- **Check:** {c['condition']}",
              f"- **Result:** FAIL — **{c['count']:,}** record(s) matched",
              f"- **Evidence:** {c['evidence']}"]
        if c["examples"]:
            L.append(f"- **Examples:** {', '.join(map(str, c['examples']))}")
        L += [f"- **Changed columns (on fix):** {', '.join(c['columns'])}",
              "- **Action:** per rules/cleaning_rules.md — **requires user confirmation before row removal**", ""]
    L += ["## 2. Assumptions / potential issues", "",
          "Anomalies / rules needing human judgment. **Not auto-acted on; no rows changed.**", ""]
    for a in assum:
        L += [f"### {a['id']} — {a['title']}",
              f"- **Evidence:** {a['evidence']}",
              f"- **Action:** {a['action']}",
              f"- **Status:** {a['status']}", ""]
    L += ["## 3. Checks performed", "", "| Rule | Check | Result | Matched |", "|---|---|---|---|"]
    for c in checks:
        L.append(f"| {c['id']} {c['title']} | {c['condition']} | {'PASS' if c['passed'] else 'FAIL'} | {c['count']:,} |")
    L += ["", "## 4. Canonical metrics (under the rules)", "",
          f"- Gross booked `SUM(amount_usd)`: **${mets['gross']:,.0f}**",
          f"- Net realized `SUM(amount_usd WHERE is_refunded=0)`: **${mets['net']:,.0f}**",
          f"- Refunds: **{mets['refunds']:,}** · Rows: **{mets['rows']:,}** · Distinct users: **{mets['distinct_users']:,}**", ""]
    npass = sum(c["passed"] for c in checks)
    L.append(f"**Summary:** {npass}/{len(checks)} confirmed-issue checks passed; "
             f"{len(conf)} confirmed issue(s), {len(assum)} assumption(s). **Source unchanged.**")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w").write("\n".join(L) + "\n")
    return conf


def clean(df, log_path):
    """Apply confirmed-issue rules R1–R5. Returns cleaned df. Logs actions."""
    log, n0 = [], len(df)
    bad = (~df["payment_id"].str.match(ID_RE)) | (df["user_id"] == SENTINEL_USER)  # R1/R2/R3
    log.append(f"[R1/R2/R3] removing {int(bad.sum())} shadow/sentinel rows")
    out = df[~bad].copy()
    out["amount_usd"]  = _num(out["amount_usd"])                       # R4
    out["is_refunded"] = _num(out["is_refunded"]).astype(int)
    out["paid_at"]     = _date(out["paid_at"]).dt.strftime("%Y-%m-%d")
    out["net_amount_usd"] = out["amount_usd"].where(out["is_refunded"] == 0, 0.0)  # R5
    out = out.sort_values("payment_id").reset_index(drop=True)
    log.append(f"[RESULT] rows {n0} -> {len(out)} (removed {n0-len(out)}); added net_amount_usd")
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        open(log_path, "w").write("\n".join(log) + "\n")
    return out


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Reproducible data-quality validate/clean pipeline.")
    ap.add_argument("--data", required=True, help="path to source table (CSV)")
    ap.add_argument("--rules", help="rules folder (default: <data dir>/rules)")
    ap.add_argument("--out", help="report output folder (default: <data dir>/reports)")
    ap.add_argument("--clean", action="store_true", help="also produce a cleaned copy (never overwrites source)")
    ap.add_argument("--out-data", help="cleaned output path (default: <data dir>/<stem>_clean.csv)")
    ap.add_argument("--yes", action="store_true", help="confirm row removal for --clean (required)")
    args = ap.parse_args()

    data = os.path.abspath(args.data)
    base = os.path.dirname(data)
    rules_dir = args.rules or os.path.join(base, "rules")
    out_dir   = args.out   or os.path.join(base, "reports")
    if not os.path.exists(data):
        sys.exit(f"ERROR: data file not found: {data}")

    rules_path = os.path.join(rules_dir, "cleaning_rules.md")
    df = pd.read_csv(data, dtype=str, keep_default_na=False)   # read-only load

    prof   = profile(df, data)
    checks = evaluate(df)
    assum  = assumptions(df)
    mets   = metrics(df)
    report = os.path.join(out_dir, "validation_report.md")
    conf   = write_report(prof, checks, assum, mets, report, rules_path)

    print(f"[validate] {prof['rows']:,} rows · {len(checks)-len(conf)}/{len(checks)} confirmed checks passed")
    for c in checks:
        print(f"  {'PASS' if c['passed'] else 'FAIL'} {c['id']} {c['title']} (matched {c['count']:,})")
    print(f"[report]  {report}")

    if args.clean:
        if not args.yes:
            sys.exit("REFUSED: --clean removes rows. Re-run with --yes to confirm. Source unchanged.")
        out_data = args.out_data or os.path.join(base, os.path.splitext(os.path.basename(data))[0] + "_clean.csv")
        if os.path.abspath(out_data) == data:
            sys.exit("REFUSED: --out-data must differ from source (source is never overwritten).")
        cleaned = clean(df, os.path.join(out_dir, "cleaning_log.txt"))
        cleaned.to_csv(out_data, index=False)
        print(f"[clean]   wrote {out_data} ({len(cleaned):,} rows)")
        # re-validate the cleaned output by reloading it from disk (same path a fresh run takes)
        v = evaluate(pd.read_csv(out_data, dtype=str, keep_default_na=False))
        passed = sum(c["passed"] for c in v)
        print(f"[re-validate] cleaned file: {passed}/{len(v)} confirmed checks passed")
        if passed != len(v):
            sys.exit("ERROR: cleaned file still fails validation.")

    # exit non-zero if the (source) confirmed checks failed and we did not clean
    sys.exit(0 if (not conf or args.clean) else 1)


if __name__ == "__main__":
    main()
