"""Data Analyst Agent: Gemini + one read-only SQL tool backed by the Postgres MCP server."""
import asyncio
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

import metrics
from db import mcp_session, query

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

MAX_STEPS = 6
MAX_ROWS = 500

SYSTEM_PROMPT = f"""You are a data analyst for an online-course subscription business.
Answer questions by querying the PostgreSQL database with the run_sql tool, then reply
concisely with the key numbers (formatted, e.g. $1.2M, 4.5%) and one or two sentences of
interpretation. State any assumption you make. Never invent numbers.

Tables (schema public):
- users(user_id, signup_date date, country, plan ['monthly','annual','free','financial_aid'],
  persona ['upskiller','career_switcher','student','hobbyist'], age_band, device_primary)
- payments(payment_id, user_id, paid_at date, amount_usd numeric, plan ['monthly'=$49,'annual'=$399],
  is_refunded int 0/1)
- enrollments(enrollment_id, user_id, course_id, course_url, specialization_id, course_no,
  enrolled_at TEXT (formats 'YYYY-MM-DD' or 'DD.MM.YYYY'), completed_at TEXT 'YYYY-MM-DD' or NULL,
  last_week_reached, n_weeks, progress_pct numeric 0-100,
  funnel_state ['registered','viewed','explored','certified'], is_certified int)
- weekly_activity(enrollment_id, week_number, active_date date, minutes_watched, quiz_attempts, quiz_score)
- dim_course(course_id, course_url, course, domain, level ['Beginner','Intermediate','Advanced','Unknown'],
  partner, n_weeks, total_minutes, specialization_name, review_score, n_reviews, mean_stars)
- reviews(review_id, course_url, review_date text, review_content, stars int); review_link(enrollment_id, review_id)
- specializations(...), courses_content(...), weeks_content(...), specialization_content(...)

Data notes:
- Payments cover 2021-01-01 to 2023-02-26. Treat "now" as max(paid_at) = 2023-02-26, not today.
  Feb 2023 is a partial month.
- Join payments/enrollments to users on user_id; enrollments to dim_course on course_id.
- users.country mixes 'USA'/'United States', 'DE'/'Germany', 'UK'/'United Kingdom' and has NULLs.
- Revenue cannot be attributed to a course; payments are per user.

Metric definitions (use these exactly):
{metrics.__doc__}

SQL rules: one read-only SELECT (or WITH ... SELECT) statement in PostgreSQL syntax, no semicolons.
Aggregate in SQL rather than pulling raw rows; results are capped at {MAX_ROWS} rows.
"""

RUN_SQL = types.FunctionDeclaration(
    name="run_sql",
    description="Run one read-only PostgreSQL SELECT query and return the result as CSV.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"sql": types.Schema(type=types.Type.STRING, description="A single SELECT statement")},
        required=["sql"],
    ),
)

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum|call|merge|comment)\b",
    re.IGNORECASE,
)


def check_sql(sql: str) -> str:
    """Extra guard on top of the MCP server's READ ONLY transaction."""
    sql = sql.strip().rstrip(";").strip()
    if ";" in sql:
        raise ValueError("Only a single statement is allowed.")
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        raise ValueError("Only SELECT queries are allowed.")
    if FORBIDDEN.search(sql):
        raise ValueError("Query contains a forbidden keyword.")
    return f"select * from ({sql}) as q limit {MAX_ROWS}"


async def _ask(question: str) -> dict:
    load_dotenv(ENV_PATH, override=True)  # pick up .env edits without restarting Streamlit
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing - add it to .env")
    # Retry transient errors (429 rate limit, 503 high demand) with backoff.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=5, initial_delay=2, http_status_codes=[429, 500, 503])))
    model = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[types.Tool(function_declarations=[RUN_SQL])],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0.1,
    )
    contents = [types.Content(role="user", parts=[types.Part(text=question)])]
    steps = []

    async with mcp_session() as session:
        for _ in range(MAX_STEPS):
            response = await client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
            calls = response.function_calls
            if not calls:
                return {"answer": response.text or "(no answer)", "steps": steps}

            contents.append(response.candidates[0].content)
            replies = []
            for call in calls:
                sql = (call.args or {}).get("sql", "")
                try:
                    df = await query(session, check_sql(sql))
                    result = {"rows": len(df), "csv": df.to_csv(index=False)}
                    steps.append({"sql": sql, "result": df})
                except Exception as e:
                    result = {"error": str(e)}
                    steps.append({"sql": sql, "error": str(e)})
                replies.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=replies))

    return {"answer": "I could not finish the analysis within the step limit. Try a narrower question.",
            "steps": steps}


def ask(question: str) -> dict:
    """Answer a question. Returns {'answer': str, 'steps': [{'sql', 'result' | 'error'}]}."""
    try:
        return asyncio.run(_ask(question))
    except BaseExceptionGroup as group:  # MCP's task groups wrap the real error
        while isinstance(group, BaseExceptionGroup):
            group = group.exceptions[0]
        raise group from None
