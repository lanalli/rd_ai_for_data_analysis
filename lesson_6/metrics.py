"""SQL for every dashboard metric.

The data ends in Feb 2023, so "now" is the last payment date (AS_OF), not today.
Metric definitions (also given to the agent):
- Net revenue: sum of non-refunded payments.
- MRR: monthly-plan net revenue in the last full month + annual net revenue from
  the 12 months up to that month's end / 12. ARR = MRR x 12.
- ARPU: net revenue / paying users.
- LTV proxy: avg non-refunded payments per user x plan price.
- Monthly churn: users who paid (monthly plan) in month M-1 but not in month M.
- Annual churn: annual payments whose renewal date (+12 months, 1 month grace)
  has passed without a new annual payment.
- Lifespan: months from first to last payment + 1 (annual: payments x 12).
- At-risk-but-paying: paying now (monthly paid in last 35 days / annual in last
  12 months) and < 30 minutes watched in the 28 days before AS_OF.
- Stalled-progress payers: paying users with enrollments still registered/viewed,
  progress < 50%, enrolled 28+ days ago and no activity in the last 28 days.
"""
from db import run_queries

AS_OF = "(select max(paid_at) from payments)"

# enrolled_at is text in two formats: YYYY-MM-DD and DD.MM.YYYY
ENROLLED_DATE = "(case when e.enrolled_at like '__.__.____' then to_date(e.enrolled_at, 'DD.MM.YYYY') else e.enrolled_at::date end)"

PAYING_NOW = f"""
    select user_id, max(plan) as plan, max(paid_at) as last_paid
    from payments
    where is_refunded = 0
      and ((plan = 'monthly' and paid_at > {AS_OF} - 35)
        or (plan = 'annual'  and paid_at > {AS_OF} - interval '12 months'))
    group by user_id
"""

# users.country mixes names and codes (USA/US names, DE, UK) and has NULLs
COUNTRY = ("(case u.country when 'USA' then 'United States' when 'DE' then 'Germany' "
           "when 'UK' then 'United Kingdom' else coalesce(u.country, 'Unknown') end)")


def user_col(dim: str) -> str:
    return COUNTRY if dim == "country" else f"u.{dim}"


KPIS = f"""
with m as (select date_trunc('month', {AS_OF}) - interval '1 month' as m0),
net as (select * from payments where is_refunded = 0)
select
  to_char({AS_OF}, 'YYYY-MM-DD') as as_of,
  (select to_char(m0, 'YYYY-MM') from m) as mrr_month,
  (select sum(amount_usd) from net)::float8 as net_revenue,
  (select sum(amount_usd) from payments)::float8 as gross_revenue,
  (select count(distinct user_id) from net)::int as paying_users,
  (select count(*) from ({PAYING_NOW}) x)::int as paying_now,
  ((select coalesce(sum(amount_usd), 0) from net, m
     where plan = 'monthly' and date_trunc('month', paid_at) = m0)
   + (select coalesce(sum(amount_usd), 0) from net, m
     where plan = 'annual' and paid_at >= m0 - interval '11 months'
       and paid_at < m0 + interval '1 month') / 12)::float8 as mrr,
  (select avg(is_refunded) from payments)::float8 as refund_rate,
  (select sum(amount_usd) filter (where is_refunded = 1) / sum(amount_usd) from payments)::float8
    as refund_rate_usd
"""

LTV = """
select plan,
       count(distinct user_id) as users,
       (count(*)::float8 / count(distinct user_id)) as avg_payments,
       max(amount_usd)::float8 as price,
       (count(*)::float8 / count(distinct user_id)) * max(amount_usd)::float8 as ltv_proxy
from payments where is_refunded = 0
group by plan order by plan
"""

REVENUE_TREND = """
select to_char(date_trunc('month', paid_at), 'YYYY-MM') as month, plan,
       coalesce(sum(amount_usd) filter (where is_refunded = 0), 0)::float8 as net_revenue,
       count(distinct user_id) as payers
from payments group by 1, 2 order by 1, 2
"""

CHURN_MONTHLY = f"""
with um as (
  select distinct user_id, date_trunc('month', paid_at)::date as m
  from payments where plan = 'monthly'
)
select to_char(a.m + interval '1 month', 'YYYY-MM') as month,
       count(*) as active_start,
       count(*) filter (where b.user_id is null) as churned,
       avg((b.user_id is null)::int)::float8 as churn_rate
from um a
left join um b on b.user_id = a.user_id and b.m = a.m + interval '1 month'
where a.m + interval '1 month' < date_trunc('month', {AS_OF})  -- only fully observed months
group by a.m order by a.m
"""

CHURN_ANNUAL = f"""
with ap as (select user_id, paid_at from payments where plan = 'annual'),
due as (
  select a.user_id, (a.paid_at + interval '12 months')::date as due_date,
         exists (select 1 from ap b where b.user_id = a.user_id and b.paid_at > a.paid_at
                   and b.paid_at <= a.paid_at + interval '13 months') as renewed
  from ap a
)
select to_char(date_trunc('month', due_date), 'YYYY-MM') as month,
       count(*) as due, count(*) filter (where not renewed) as churned,
       avg((not renewed)::int)::float8 as churn_rate
from due where due_date + interval '1 month' <= {AS_OF}
group by 1 order by 1
"""

LIFESPAN = """
select plan, count(*) as users,
       avg(case when plan = 'annual' then n * 12
                else (extract(year from age(last_paid, first_paid)) * 12
                      + extract(month from age(last_paid, first_paid)) + 1) end)::float8 as avg_months
from (select user_id, plan, min(paid_at) as first_paid, max(paid_at) as last_paid, count(*) as n
      from payments group by 1, 2) x
group by plan order by plan
"""

AT_RISK = f"""
with paying as ({PAYING_NOW}),
recent as (
  select e.user_id, sum(w.minutes_watched)::float8 as minutes_28d,
         count(distinct w.active_date) as active_days_28d
  from weekly_activity w join enrollments e using (enrollment_id)
  where w.active_date > {AS_OF} - 28 and w.active_date <= {AS_OF}
  group by e.user_id
)
select p.user_id, p.plan, to_char(p.last_paid, 'YYYY-MM-DD') as last_paid,
       coalesce(r.minutes_28d, 0) as minutes_28d, coalesce(r.active_days_28d, 0) as active_days_28d,
       u.persona, u.age_band, {COUNTRY} as country, u.device_primary
from paying p join users u using (user_id) left join recent r using (user_id)
where coalesce(r.minutes_28d, 0) < 30
order by minutes_28d, p.last_paid desc
"""

STALLED = f"""
with paying as ({PAYING_NOW}),
last_act as (select enrollment_id, max(active_date) as last_active from weekly_activity group by 1)
select e.user_id, p.plan, e.enrollment_id, c.course, c.domain, c.level, e.funnel_state,
       e.progress_pct::float8 as progress_pct, e.last_week_reached, e.n_weeks,
       to_char({ENROLLED_DATE}, 'YYYY-MM-DD') as enrolled_at, to_char(l.last_active, 'YYYY-MM-DD') as last_active
from enrollments e
join paying p using (user_id)
left join last_act l using (enrollment_id)
left join dim_course c on c.course_id = e.course_id
where e.completed_at is null
  and e.funnel_state in ('registered', 'viewed')
  and e.progress_pct < 50
  and {ENROLLED_DATE} <= {AS_OF} - 28
  and (l.last_active is null or l.last_active <= {AS_OF} - 28)
order by enrolled_at
"""

FUNNEL = """
select funnel_state, count(*) as enrollments, avg(progress_pct)::float8 as avg_progress
from enrollments group by 1
order by array_position(array['registered','viewed','explored','certified'], funnel_state::text)
"""

USER_DIMS = ["plan", "persona", "age_band", "country", "device_primary"]
COURSE_DIMS = ["domain", "level"]


def refund_by(dim: str) -> str:
    col = "p.plan" if dim == "plan" else user_col(dim)
    return f"""
select {col} as segment, count(*) as payments,
       avg(p.is_refunded)::float8 as refund_rate,
       coalesce(sum(p.amount_usd) filter (where p.is_refunded = 1), 0)::float8 as refunded_usd
from payments p join users u using (user_id)
group by 1 order by refund_rate desc
"""


def segment_by(dim: str) -> str:
    return f"""
with pay as (select user_id, sum(amount_usd) filter (where is_refunded = 0) as net
             from payments group by 1)
select {user_col(dim)} as segment, count(*) as users, count(pay.user_id) as paying_users,
       coalesce(sum(pay.net), 0)::float8 as net_revenue,
       (coalesce(sum(pay.net), 0) / nullif(count(pay.user_id), 0))::float8 as arpu
from users u left join pay using (user_id)
group by 1 order by net_revenue desc
"""


def course_segment_by(dim: str) -> str:
    return f"""
select c.{dim} as segment, count(*) as enrollments,
       count(distinct e.user_id) as learners,
       count(distinct e.user_id) filter (where u.plan in ('monthly', 'annual')) as paying_learners,
       avg((e.funnel_state = 'certified')::int)::float8 as completion_rate,
       avg(e.progress_pct)::float8 as avg_progress
from enrollments e join dim_course c on c.course_id = e.course_id join users u using (user_id)
group by 1 order by enrollments desc
"""


def load_all():
    """Fetch every dashboard dataset in one MCP session."""
    queries = {
        "kpis": KPIS, "ltv": LTV, "revenue_trend": REVENUE_TREND,
        "churn_monthly": CHURN_MONTHLY, "churn_annual": CHURN_ANNUAL,
        "lifespan": LIFESPAN, "at_risk": AT_RISK, "stalled": STALLED, "funnel": FUNNEL,
    }
    queries |= {f"refund_{d}": refund_by(d) for d in USER_DIMS}
    queries |= {f"segment_{d}": segment_by(d) for d in USER_DIMS}
    queries |= {f"course_{d}": course_segment_by(d) for d in COURSE_DIMS}
    return run_queries(queries)
