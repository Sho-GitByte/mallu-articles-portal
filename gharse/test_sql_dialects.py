"""Prove the SQL is valid for BOTH dialects without needing a Postgres server.

The app is written once, in SQLite's ? style, and store.py renders the schema per
dialect. That only holds if nobody sneaks a sqlite-only construct into a query, so
this test derives every statement the project can emit and parses it with sqlglot.

    python test_sql_dialects.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import sqlglot
import store

SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|WITH)\b", re.I)
# sqlglot wants a literal where the driver wants a bound parameter.
PLACEHOLDER = re.compile(r"\?")

SQLITE_ONLY = [
    ("AUTOINCREMENT", re.compile(r"\bAUTOINCREMENT\b", re.I)),
    ("PRAGMA", re.compile(r"\bPRAGMA\b", re.I)),
    ("BLOB column type", re.compile(r"\bBLOB\b", re.I)),
    ("INTEGER PRIMARY KEY", re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\b", re.I)),
    ("two-argument MAX()", re.compile(r"\bMAX\s*\([^)]*,", re.I)),
]

failures = []
checked = 0


def check(sql, dialect, origin):
    """Parse one statement, and refuse sqlite-only syntax on the Postgres side."""
    global checked
    probe = PLACEHOLDER.sub("NULL", sql)
    try:
        sqlglot.parse_one(probe, dialect=dialect)
    except Exception as e:
        failures.append(f"{origin}: does not parse as {dialect}: {e}\n    {sql[:160]}")
        return
    if dialect == "postgres":
        for name, pattern in SQLITE_ONLY:
            if pattern.search(sql):
                failures.append(f"{origin}: {name} is not valid Postgres\n    {sql[:160]}")
    checked += 1


def ddl_for(kind):
    """Render store.py's schema exactly as it would be issued against that database."""
    types = store._TYPES[kind]
    out = [s.format(**types) for s in store.SCHEMA]
    out += list(store.INDEXES)
    for table, cols in store.NEW_COLUMNS.items():
        for col, decl in cols.items():
            out.append(f"ALTER TABLE {table} ADD COLUMN {col} {decl.format(**types)}")
    return out


def columns_of(ddl_statements):
    """{table: {column, ...}} parsed out of the rendered DDL, so the two dialects
    cannot silently drift apart."""
    shape = {}
    for stmt in ddl_statements:
        if not stmt.lstrip().upper().startswith("CREATE TABLE"):
            continue
        tree = sqlglot.parse_one(stmt, dialect="postgres" if "SERIAL" in stmt or "BYTEA" in stmt else "sqlite")
        table = tree.find(sqlglot.exp.Table).name
        shape[table] = {c.name for c in tree.find_all(sqlglot.exp.ColumnDef)}
    return shape


TEMPLATE = re.compile(r"\{(PK|BLOB|MONEY)\}")
# A docstring can open with the word SELECT and mean nothing of the sort.
SQL_BODY = re.compile(r"\b(FROM|INTO|SET|TABLE|VALUES|INDEX)\b", re.I)


def _docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _fstring_parts(tree):
    """Constants that are pieces of an f-string are fragments of a statement, not
    statements — store.py builds "ALTER TABLE {t} ..." that way."""
    inner = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in ast.walk(node):
                if isinstance(part, ast.Constant):
                    inner.add(id(part))
    return inner


def sql_literals(path):
    """Every SQL-looking string constant in a source file, found by parsing it —
    never by hand-copying, so this cannot go stale. Schema templates are skipped
    here because ddl_for() already checks them rendered, in both dialects."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    inner = _fstring_parts(tree) | _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and SQL_START.match(node.value)
                and SQL_BODY.search(node.value)
                and not TEMPLATE.search(node.value)
                and id(node) not in inner):
            found.append((node.lineno, node.value))
    return found


print("— schema, both dialects")
ddl = {k: ddl_for(k) for k in ("sqlite", "postgres")}
for kind, statements in ddl.items():
    for stmt in statements:
        check(stmt, kind, f"store.py schema [{kind}]")
    print(f"  {kind:9} {len(statements)} statements")

shape_sqlite, shape_pg = columns_of(ddl["sqlite"]), columns_of(ddl["postgres"])
if shape_sqlite != shape_pg:
    only_s = {t: shape_sqlite.get(t, set()) - shape_pg.get(t, set()) for t in shape_sqlite}
    only_p = {t: shape_pg.get(t, set()) - shape_sqlite.get(t, set()) for t in shape_pg}
    failures.append(f"the two dialects define different schemas\n    sqlite-only: "
                    f"{ {k: v for k, v in only_s.items() if v} }\n    postgres-only: "
                    f"{ {k: v for k, v in only_p.items() if v} }")
else:
    print(f"  identical shape: {len(shape_sqlite)} tables, "
          f"{sum(len(v) for v in shape_sqlite.values())} columns")

print("— queries in the source, parsed under both dialects")
for name in ("app.py", "store.py"):
    path = os.path.join(ROOT, name)
    literals = sql_literals(path)
    for lineno, sql in literals:
        # A query the app assembles from fragments is checked fragment-first: the
        # opening fragment carries the statement, the rest are appended clauses.
        for dialect in ("sqlite", "postgres"):
            check(sql, dialect, f"{name}:{lineno}")
    print(f"  {name:9} {len(literals)} statements x 2 dialects")

# Fragments appended to a query at runtime (" AND l.kind=?") never reach a parser on
# their own, but they must still be dialect-neutral.
print("— appended query fragments")
frag_bad = 0
for name in ("app.py", "store.py"):
    tree = ast.parse(open(os.path.join(ROOT, name), encoding="utf-8").read())
    inner = _fstring_parts(tree) | _docstrings(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and not SQL_START.match(node.value) and id(node) not in inner):
            v = node.value
            if re.match(r"^\s*(AND|OR|ORDER BY|GROUP BY|LIMIT|HAVING|JOIN|WHERE)\b", v, re.I):
                for label, pattern in SQLITE_ONLY:
                    if pattern.search(v):
                        failures.append(f"{name}:{node.lineno}: fragment uses {label}\n    {v}")
                        frag_bad += 1
print(f"  {frag_bad} problems")

print()
if failures:
    print(f"FAILED — {len(failures)} problem(s):")
    for f in failures:
        print("  •", f)
    sys.exit(1)
print(f"OK — {checked} statements parse cleanly, no sqlite-only syntax on the Postgres path.")
