"""Read-only database access through the Postgres MCP server.

The app never connects to Postgres directly: it starts the official
@modelcontextprotocol/server-postgres server over stdio and calls its `query`
tool, which runs every statement inside a READ ONLY transaction.
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


def _server_params() -> StdioServerParameters:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing - add it to .env")
    return StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres", url],
        env={**os.environ},
    )


@asynccontextmanager
async def mcp_session():
    """Open one MCP session (one server process) for a batch of queries."""
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    # node-postgres returns NUMERIC/BIGINT as strings; convert columns that are fully numeric.
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().sum() == df[col].notna().sum():
                df[col] = converted
    return df


async def query(session: ClientSession, sql: str) -> pd.DataFrame:
    try:
        result = await session.call_tool("query", {"sql": sql})
    except MCPError as e:
        raise RuntimeError(f"Database error: {e}") from None
    text = result.content[0].text if result.content else "[]"
    if getattr(result, "isError", False):
        raise RuntimeError(text)
    return _to_frame(json.loads(text))


def run_queries(queries: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Run several named queries in a single MCP session (sync helper for Streamlit)."""

    async def _run():
        async with mcp_session() as session:
            return {name: await query(session, sql) for name, sql in queries.items()}

    return asyncio.run(_run())
