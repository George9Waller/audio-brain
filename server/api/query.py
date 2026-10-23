"""Read-only SQL for Claude (MCP.md, "Read-only SQL"): the escape hatch when no endpoint answers the question.

Queries run on their own connection as the Postgres role `audio_brain_ro`, which can only SELECT, and not from the
tables in HIDDEN. Each is one statement (a prepared statement can't hold two, so nothing can `SET` its way out), in a
read-only transaction, stopped after TIMEOUT seconds and cut at MAX_ROWS. Vectors come back as `<vector 512>`; their
operators (`<=>`) still work inside the query.

The role is created, and its grants refreshed, after every `migrate` (api.apps), so new tables are readable without
a migration of their own. Its password is POSTGRES_RO_PASSWORD; with none (a local trust login), it has none.
"""

import json
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
from django.conf import settings
from django.db import connection
from psycopg import sql

ROLE = "audio_brain_ro"
HIDDEN = {"library_oauthtoken"}  # TIDAL tokens
HIDDEN_PREFIXES = ("auth_", "django_")  # Django's users, sessions and admin log: nothing about music
TIMEOUT = 10
MAX_ROWS = 500
MAX_TEXT = 2000


class QueryError(Exception):
    pass


def readable_tables(cursor):
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    return [t for (t,) in cursor.fetchall() if t not in HIDDEN and not t.startswith(HIDDEN_PREFIXES)]


def grant_read_access(**kwargs):
    """Create the role if it's missing, set its password and limits, and let it read exactly readable_tables()."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [ROLE])
        if not cursor.fetchone():
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(ROLE)).as_string(cursor.connection))
        role = sql.Identifier(ROLE)
        password = sql.Literal(settings.QUERY_DB_PASSWORD) if settings.QUERY_DB_PASSWORD else sql.NULL
        statements = [
            sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(role, password),
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role),
            sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(role, sql.Literal(f"{TIMEOUT}s")),
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(connection.settings_dict["NAME"]), role),
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role),
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role),
        ]
        tables = readable_tables(cursor)
        if tables:
            statements.append(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.SQL(", ").join(map(sql.Identifier, tables)), role))
        for statement in statements:
            cursor.execute(statement.as_string(cursor.connection))


def _value(value, vector):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if vector and isinstance(value, str):
        return f"<vector {value.count(',') + 1}>"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (UUID, timedelta)):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
        return value if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"
    text = str(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


def run(query, limit=MAX_ROWS):
    """{"columns", "rows", "truncated", "ms"} for one read-only SELECT (or WITH, EXPLAIN...)."""
    query = query.strip().rstrip(";")
    if not query:
        raise QueryError("empty query")
    limit = max(1, min(limit, MAX_ROWS))
    db = connection.settings_dict
    try:
        conn = psycopg.connect(dbname=db["NAME"], user=ROLE, password=settings.QUERY_DB_PASSWORD or None,
                               host=db["HOST"] or None, port=db["PORT"] or None, connect_timeout=5,
                               options=f"-c statement_timeout={TIMEOUT * 1000} -c default_transaction_read_only=on")
    except psycopg.OperationalError as error:
        raise QueryError(f"can't connect as {ROLE}: {error}") from error
    # the role's own timeout can be changed by the query itself; this can't
    watchdog = threading.Timer(TIMEOUT + 2, conn.cancel_safe)
    started = time.monotonic()
    try:
        conn.read_only = True
        with conn.cursor() as cursor:
            cursor.execute("SELECT oid FROM pg_type WHERE typname IN ('vector', 'halfvec', 'sparsevec')")
            vectors = {oid for (oid,) in cursor.fetchall()}
            watchdog.start()
            # a prepared statement holds exactly one command; % is doubled because parameters are being passed
            cursor.execute(query.replace("%", "%%"), (), prepare=True)
            if cursor.description is None:
                raise QueryError("the statement returned no rows")
            columns = [c.name for c in cursor.description]
            is_vector = [c.type_code in vectors for c in cursor.description]
            fetched = cursor.fetchmany(limit + 1)
    except psycopg.Error as error:
        message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
        if isinstance(error, psycopg.errors.QueryCanceled):
            message = f"stopped after {TIMEOUT} s; narrow it down, or aggregate first"
        raise QueryError(message) from error
    finally:
        watchdog.cancel()
        conn.close()
    rows = [[_value(v, vec) for v, vec in zip(row, is_vector)] for row in fetched[:limit]]
    return {"columns": columns, "rows": rows, "truncated": len(fetched) > limit, "ms": round((time.monotonic() - started) * 1000)}
