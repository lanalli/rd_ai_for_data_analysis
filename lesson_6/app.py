"""Streamlit dashboard + Data Analyst Agent.  Run:  streamlit run app.py"""
import pandas as pd
import plotly.express as px
import streamlit as st

import agent
import metrics

st.set_page_config(page_title="Subscription Analytics Agent", layout="wide", initial_sidebar_state="collapsed")
st.markdown("<style>[data-testid='stMetricValue'] {font-size: 1.7rem;}</style>", unsafe_allow_html=True)

PLAN_COLORS = {"monthly": "#2a78d6", "annual": "#eb6834"}
BAR_COLOR = "#2a78d6"
DIM_LABELS = {"plan": "Plan", "persona": "Persona", "age_band": "Age band", "country": "Country",
              "device_primary": "Device", "domain": "Course domain", "level": "Course level"}


@st.cache_data(ttl=3600, show_spinner="Loading data through MCP...")
def load():
    return metrics.load_all()


def money(x: float) -> str:
    return f"${x / 1e6:.2f}M" if abs(x) >= 1e6 else f"${x / 1e3:.1f}K" if abs(x) >= 1e4 else f"${x:,.0f}"


def style(fig, y_title=None, pct=False):
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10), legend_title_text="",
                      yaxis_title=y_title, xaxis_title=None)
    if pct:
        fig.update_yaxes(tickformat=".0%")
    return fig


# ---------- sidebar ----------
with st.sidebar:
    st.header("About")
    st.write("All numbers are read through the Postgres **MCP server** (read-only). "
             "The data ends in Feb 2023, so metrics are measured *as of the last payment date*.")
    if st.button("Refresh data"):
        st.cache_data.clear()
    with st.expander("Metric definitions"):
        st.markdown(metrics.__doc__)

try:
    d = load()
except Exception as e:
    st.error(f"Could not load data: {e}")
    st.stop()

k = d["kpis"].iloc[0]
ltv = d["ltv"].set_index("plan")
life = d["lifespan"].set_index("plan")
cm, ca = d["churn_monthly"], d["churn_annual"]
churn_m = cm.tail(12)["churned"].sum() / cm.tail(12)["active_start"].sum()
churn_a = ca.tail(12)["churned"].sum() / ca.tail(12)["due"].sum()
mrr = k["mrr"]

st.title("Subscription Analytics & Data Analyst Agent")
st.caption(f"As of **{k['as_of']}** · MRR measured for {k['mrr_month']} · churn = average of the last 12 observed months")

# ---------- KPI tiles ----------
at_risk, stalled = d["at_risk"], d["stalled"]
tiles = [
    ("Net revenue", money(k["net_revenue"]), f"Gross {money(k['gross_revenue'])}, refunds excluded"),
    ("MRR", money(mrr), f"Measured for {k['mrr_month']}"),
    ("ARR", money(mrr * 12), "MRR × 12"),
    ("ARPU", f"${k['net_revenue'] / k['paying_users']:,.0f}", f"{k['paying_users']:,} paying users"),
    ("LTV · monthly", f"${ltv.loc['monthly', 'ltv_proxy']:,.0f}",
     f"{ltv.loc['monthly', 'avg_payments']:.1f} payments × $49"),
    ("LTV · annual", f"${ltv.loc['annual', 'ltv_proxy']:,.0f}",
     f"{ltv.loc['annual', 'avg_payments']:.2f} payments × $399"),
    ("At-risk payers", f"{len(at_risk):,}",
     f"{len(at_risk) / k['paying_now']:.0%} of {k['paying_now']:,} currently paying; < 30 min watched in 28 days"),
    ("Stalled payers", f"{stalled['user_id'].nunique():,}", f"{len(stalled):,} stalled enrollments"),
    ("Churn · monthly", f"{churn_m:.1%}", "Per month: paid last month, not this month"),
    ("Churn · annual", f"{churn_a:.1%}", "Share of renewals not renewed"),
    ("Lifespan (mo)", f"{life.loc['monthly', 'avg_months']:.1f} / {life.loc['annual', 'avg_months']:.1f}",
     "Monthly plan / annual plan, observed months"),
    ("Refund rate", f"{k['refund_rate']:.2%}", f"{k['refund_rate_usd']:.2%} of gross $"),
]
for row in range(0, len(tiles), 4):
    for col, (label, value, tip) in zip(st.columns(4), tiles[row:row + 4]):
        col.metric(label, value, help=tip, border=True)

tab_rev, tab_churn, tab_ref, tab_seg, tab_risk = st.tabs(
    ["Revenue", "Churn", "Refunds", "Segments", "At-risk & stalled"])

# ---------- revenue ----------
with tab_rev:
    rev = d["revenue_trend"]
    fig = px.bar(rev, x="month", y="net_revenue", color="plan", color_discrete_map=PLAN_COLORS,
                 title="Monthly net revenue by plan (Feb 2023 is partial)")
    st.plotly_chart(style(fig, "Net revenue ($)"), width="stretch")
    fig = px.line(rev, x="month", y="payers", color="plan", color_discrete_map=PLAN_COLORS,
                  title="Paying users per month")
    st.plotly_chart(style(fig, "Users"), width="stretch")

# ---------- churn ----------
with tab_churn:
    both = pd.concat([cm.assign(plan="monthly"), ca.assign(plan="annual")])[["month", "churn_rate", "plan"]]
    fig = px.line(both, x="month", y="churn_rate", color="plan", color_discrete_map=PLAN_COLORS, markers=True,
                  title="Churn rate by month (monthly: didn't pay next month · annual: didn't renew)")
    st.plotly_chart(style(fig, "Churn rate", pct=True), width="stretch")
    lt = life.join(ltv[["avg_payments", "ltv_proxy"]]).reset_index()
    st.dataframe(lt, hide_index=True, column_config={
        "avg_months": st.column_config.NumberColumn("Avg lifespan (months)", format="%.1f"),
        "avg_payments": st.column_config.NumberColumn("Avg payments", format="%.2f"),
        "ltv_proxy": st.column_config.NumberColumn("LTV proxy", format="$%.0f")})

# ---------- refunds ----------
with tab_ref:
    dim = st.selectbox("Refund rate by", metrics.USER_DIMS, format_func=DIM_LABELS.get, key="ref_dim")
    r = d[f"refund_{dim}"].sort_values("refund_rate")
    fig = px.bar(r, x="refund_rate", y="segment", orientation="h", color_discrete_sequence=[BAR_COLOR],
                 hover_data={"payments": ":,", "refunded_usd": ":$,.0f"},
                 title=f"Refund rate by {DIM_LABELS[dim].lower()} (overall {k['refund_rate']:.2%})")
    fig.add_vline(x=k["refund_rate"], line_dash="dot", line_color="gray")
    fig.update_xaxes(tickformat=".1%")
    st.plotly_chart(style(fig), width="stretch")
    st.caption("Small segments (few payments) can show noisy refund rates — check the payments count on hover.")

# ---------- segments ----------
with tab_seg:
    dim = st.selectbox("Segment by", metrics.USER_DIMS + metrics.COURSE_DIMS, format_func=DIM_LABELS.get,
                       key="seg_dim")
    if dim in metrics.USER_DIMS:
        s = d[f"segment_{dim}"]
        fig = px.bar(s.sort_values("net_revenue"), x="net_revenue", y="segment", orientation="h",
                     color_discrete_sequence=[BAR_COLOR], hover_data={"paying_users": ":,", "arpu": ":$,.0f"},
                     title=f"Net revenue by {DIM_LABELS[dim].lower()}")
        st.plotly_chart(style(fig), width="stretch")
        st.dataframe(s, hide_index=True, width="stretch", column_config={
            "net_revenue": st.column_config.NumberColumn("Net revenue", format="$%.0f"),
            "arpu": st.column_config.NumberColumn("ARPU", format="$%.0f")})
    else:
        s = d[f"course_{dim}"]
        top = s.head(15).sort_values("enrollments")
        fig = px.bar(top, x="enrollments", y="segment", orientation="h", color_discrete_sequence=[BAR_COLOR],
                     hover_data={"paying_learners": ":,", "completion_rate": ":.1%"},
                     title=f"Enrollments by {DIM_LABELS[dim].lower()}" + (" (top 15)" if len(s) > 15 else ""))
        st.plotly_chart(style(fig), width="stretch")
        st.caption("Revenue is per user, not per course, so course segments show enrollments and learners.")
        st.dataframe(s, hide_index=True, width="stretch", column_config={
            "completion_rate": st.column_config.NumberColumn("Completion rate", format="%.3f"),
            "avg_progress": st.column_config.NumberColumn("Avg progress %", format="%.1f")})
    f = d["funnel"]
    fig = px.bar(f, x="funnel_state", y="enrollments", color_discrete_sequence=[BAR_COLOR],
                 title="Enrollment funnel")
    st.plotly_chart(style(fig, "Enrollments"), width="stretch")

# ---------- at-risk & stalled ----------
with tab_risk:
    st.subheader(f"At-risk but paying — {len(at_risk):,} users")
    st.caption("Currently paying, < 30 minutes watched in the 28 days before the as-of date.")
    cols = st.columns(2)
    for col, dim in zip(cols, ["persona", "plan"]):
        cnt = at_risk[dim].value_counts().rename_axis(dim).reset_index(name="users")
        col.plotly_chart(style(px.bar(cnt, x=dim, y="users", color_discrete_sequence=[BAR_COLOR],
                                      title=f"At-risk users by {dim}"), "Users"), width="stretch")
    st.dataframe(at_risk, hide_index=True, width="stretch", height=300)
    st.download_button("Download at-risk list (CSV)", at_risk.to_csv(index=False), "at_risk.csv")

    st.subheader(f"Stalled-progress payers — {stalled['user_id'].nunique():,} users")
    st.caption("Paying users with enrollments stuck in registered/viewed, progress < 50%, no activity in 28 days.")
    st.dataframe(stalled, hide_index=True, width="stretch", height=300)
    st.download_button("Download stalled list (CSV)", stalled.to_csv(index=False), "stalled.csv")

# ---------- agent ----------
st.divider()
st.header("Ask the Data Analyst Agent")
st.caption("Examples: *Which persona has the highest monthly churn?* · *What was net revenue in 2022 by country?* · "
           "*How many at-risk subscribers are on mobile?*")
with st.form("ask"):
    question = st.text_area("Your question", height=80)
    submitted = st.form_submit_button("Ask")

if submitted and question.strip():
    with st.spinner("Thinking and querying the database..."):
        try:
            st.session_state["last"] = (question, agent.ask(question.strip()))
        except Exception as e:
            st.session_state["last"] = (question, {"answer": f"Error: {e}", "steps": []})

if "last" in st.session_state:
    q, res = st.session_state["last"]
    st.markdown(f"**Q:** {q}")
    st.markdown(res["answer"])
    for i, step in enumerate(res["steps"], 1):
        with st.expander(f"Query {i}" + (" (error)" if "error" in step else "")):
            st.code(step["sql"], language="sql")
            if "error" in step:
                st.error(step["error"])
            else:
                st.dataframe(step["result"], hide_index=True)
