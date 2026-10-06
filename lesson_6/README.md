# Subscription Analytics & Data Analyst Agent

## What this agent does
A Streamlit app for an online-course subscription business with two parts: a **KPI dashboard** and
**one AI data analyst agent** powered by Gemini. Together they answer three kinds of questions:
how much the business earns (revenue), who is leaving or about to leave (churn risk), and any ad-hoc
question about the data in plain English.

### 1. Dashboard
**KPI tiles** (hover the ⓘ icon on each tile for how it is calculated):

| Metric | How it is calculated |
|---|---|
| Net revenue | Sum of all payments that were not refunded |
| MRR | Monthly-plan net revenue in the last full month + annual net revenue of the last 12 months ÷ 12 |
| ARR | MRR × 12 |
| ARPU | Net revenue ÷ number of paying users |
| LTV proxy (per plan) | Average payments per user × plan price ($49 monthly, $399 annual) |
| Churn — monthly plan | Share of users who paid last month but not this month (average of last 12 months) |
| Churn — annual plan | Share of annual subscriptions not renewed within 1 month after the renewal date |
| Avg lifespan | Months from first to last payment + 1 (annual plan: payments × 12), per plan |
| Refund rate | Refunded payments ÷ all payments (also shown as share of gross $) |
| At-risk payers | Currently paying, but watched < 30 minutes in the last 28 days |
| Stalled payers | Currently paying, with a course stuck in *registered/viewed*, progress < 50%, no activity for 28+ days |

"Currently paying" = a monthly payment in the last 35 days or an annual payment in the last 12 months.

**Tabs with charts and tables:**
- **Revenue** — monthly net revenue stacked by plan; paying users per month.
- **Churn** — churn rate by month for both plans; lifespan and LTV per plan.
- **Refunds** — refund rate by plan, persona, age band, country or device, compared with the overall rate.
- **Segments** — users, paying users, net revenue and ARPU by plan, persona, age band, country or device;
  enrollments, completion rate and progress by course domain or level; the enrollment funnel
  (registered → viewed → explored → certified).
- **At-risk & stalled** — who these subscribers are (by persona and plan) and full lists to download as
  CSV, e.g. for a retention email campaign.

### 2. Data Analyst Agent
At the bottom of the page there is a text box. Ask a question in plain English and the agent answers
with the key numbers and a short interpretation.

How it works, step by step:
1. Your question is sent to **Gemini** together with instructions describing the database tables,
   the data quirks and the same metric definitions the dashboard uses, so its answers are consistent.
2. Gemini decides which data it needs and calls its only tool, **`run_sql`**, with a SQL query.
3. The query is checked (single `SELECT` only, no write keywords, max 500 rows) and run through the
   Postgres **MCP server**.
4. The result goes back to Gemini. If needed it runs follow-up queries (up to 6 steps), e.g. to
   check a detail or fix an error in its own SQL.
5. Gemini writes the final answer. Every query it ran is shown under the answer (expand
   *Query 1, 2, …*) with its result table, so you can verify the numbers.

Example questions:
- *Which persona has the highest monthly-plan churn?*
- *What was net revenue in 2022 by country?*
- *How many at-risk subscribers are on mobile?*
- *Which course domains have the lowest completion rate?*
- *Compare the refund rate of students and career switchers.*
- *What is the average review score of Beginner vs Advanced courses?*

If Gemini is busy (429/503 errors) the agent retries automatically; if it still fails, a short error
message is shown instead of an answer.

### 3. Safety
- **Read-only by design:** the app never connects to the database directly. All queries, from both the
  dashboard and the agent, go through the MCP server (`@modelcontextprotocol/server-postgres`), which
  wraps each query in a READ ONLY transaction, using a database user with read-only rights.
- **Extra guard in the agent:** only one `SELECT`/`WITH` statement per call; `INSERT`, `UPDATE`,
  `DELETE`, `DROP` and similar keywords are rejected before anything reaches the database.
- **No secrets in code:** the Gemini key and the database connection string live only in `.env`,
  which is git-ignored.

### Project files

| File | Purpose |
|---|---|
| `app.py` | Streamlit dashboard + "Ask the agent" box |
| `agent.py` | Gemini agent: instructions, `run_sql` tool, SQL guard, retry logic |
| `metrics.py` | SQL and definitions for every dashboard metric |
| `db.py` | Starts the MCP server and runs queries through it |
| `semantic_layer.yaml` | Tables, keys, joins between tables, dimensions, KPI definitions (with SQL) and known data issues |

## What data it uses
PostgreSQL database `coursera_capstone` (Neon), read via MCP:

| Table | Content |
|---|---|
| `users` | 40k users: plan (monthly / annual / free / financial_aid), persona, age band, country, device |
| `payments` | 88k payments, Jan 2021 – Feb 2023: date, amount (monthly $49, annual $399), plan, refund flag |
| `enrollments` | 95k course enrollments: funnel state, progress %, completion |
| `weekly_activity` | Learning activity: minutes watched, quiz attempts and scores |
| `dim_course` | 3k courses: domain, level, partner, ratings |
| `reviews`, `specializations`, content tables | Available to the agent for extra questions |

Data notes:
- The data ends on 2023-02-26, so "now" means the last payment date, not today. Feb 2023 is a partial month.
- Inconsistent country names (USA / United States, DE, UK) and two date formats in `enrolled_at`
  are normalized in SQL.
- Metric definitions are in `metrics.py` and in the dashboard sidebar.

## How to launch
**Requirements:** Python 3.10+ and Node.js (for `npx`, which runs the MCP server).

1. Create `.env` from the template and fill it in. `.env` is git-ignored; never commit it.
   ```bash
   cp .env.example .env
   ```
   ```
   GEMINI_API_KEY=your-key          # from https://aistudio.google.com/apikey
   GEMINI_MODEL=gemini-3.5-flash-lite
   DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/coursera_capstone?sslmode=require
   ```
2. Install dependencies (once):
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Run the app from the project folder, then open http://localhost:8501:
   ```bash
   .venv/bin/streamlit run app.py
   ```
   The first load takes about 20 s (data is fetched through MCP, then cached for an hour; the sidebar's
   **Refresh data** button reloads it). After changing code, stop the app with Ctrl-C and run it again.
   Changes to `.env` are picked up without a restart.
