# store.py — GharSe data layer.
#
# One app, two databases. Locally (and in tests) it is a SQLite file and needs no
# configuration at all. In production it is hosted Postgres, because Streamlit
# Community Cloud's filesystem is ephemeral: when the app sleeps or redeploys, a
# SQLite file goes with it, and with it every kitchen, order and payout record.
#
# The app writes ordinary SQL with ? placeholders and never imports a driver; this
# module owns the dialect differences, the schema, the migrations, password hashing
# and India-correct time.

import hashlib
import hmac
import os
import re
import secrets as _secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

SQLITE_PATH = os.environ.get("GHARSE_DB_PATH", "gharse.db")

# India never observes DST, so a fixed offset is exactly right and needs no tzdata.
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - only on images without tzdata
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

DEFAULT_SETTINGS = {
    "platform_upi": "",          # the UPI ID customers pay into; set by admin before go-live
    "brand_name": "GharSe",
    "platform_pct": "8",         # onboarding, verification, the app itself
    "ops_pct": "2",              # payment processing, support, coordination
    "commission_pct": "8",       # legacy single rate; kept so old databases still read
    "delivery_fee": "15",        # home delivery, per order
    "pickup_point_fee": "5",     # a batched drop at a PG / office / apartment gate
    "self_pickup_fee": "0",
    "min_order": "60",
}

DEMO_ADMIN_PASSWORD = "admin@123"

# ----------------------------------------------------------------------------- secrets
def secret(*path, default=None):
    """Read st.secrets without exploding when there is no secrets file — which is the
    normal case locally, in CI and in the tests."""
    try:
        import streamlit as st
        node = st.secrets
        for key in path:
            node = node[key]
        return node
    except Exception:
        return default

def _database_url():
    return (secret("database", "url", default=None) or os.environ.get("DATABASE_URL") or "").strip()

DATABASE_URL = _database_url()
KIND = "postgres" if DATABASE_URL else "sqlite"

def admin_password():
    """The password the admin account is created with. A deployment sets this; a laptop
    gets the demo default and the UI nags about it until it is changed."""
    return (secret("admin", "password", default=None)
            or os.environ.get("GHARSE_ADMIN_PASSWORD")
            or DEMO_ADMIN_PASSWORD)

def admin_password_is_default():
    return admin_password() == DEMO_ADMIN_PASSWORD

# ----------------------------------------------------------------------------- time
def now_ist():
    return datetime.now(IST)

def today_ist():
    """The date it is in Bengaluru right now. Using date.today() here would mean a
    UTC-hosted app disagrees with its users about what day it is for 5½ hours."""
    return now_ist().date()

def now_iso():
    return now_ist().isoformat(timespec="seconds")

# ----------------------------------------------------------------------------- connection
_lock = threading.Lock()
_conn = None
_ready = False

def _connect():
    if KIND == "postgres":
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        return conn
    conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def connect():
    """One connection for the process, reused. Streamlit reruns the whole script on
    every interaction; opening a fresh remote connection per query turned one page
    render into dozens of round trips."""
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn

def _reset():
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _conn = None

def _translate(sql):
    """App SQL is written once, in SQLite's ? style. psycopg2 wants %s, and treats a
    literal % as formatting, so that has to be doubled first."""
    if KIND != "postgres":
        return sql
    return sql.replace("%", "%%").replace("?", "%s")

def _run(sql, params, want_rows):
    stmt = _translate(sql)
    with _lock:
        for attempt in (1, 2):
            try:
                conn = connect()
                cur = conn.cursor()
                cur.execute(stmt, tuple(params))
                if want_rows:
                    rows = cur.fetchall()
                    cur.close()
                    return rows
                out = None
                if cur.description is not None:          # RETURNING id
                    row = cur.fetchone()
                    if row is not None:
                        out = row["id"] if not isinstance(row, tuple) else row[0]
                elif KIND == "sqlite":
                    out = cur.lastrowid
                cur.close()
                if KIND == "sqlite":
                    conn.commit()
                return out
            except Exception:
                # A dropped connection to a sleeping Postgres is ordinary; retry once.
                if attempt == 2:
                    raise
                _reset()

def q(sql, params=()):
    """SELECT. Rows are mapping-like in both dialects: row["column"] works."""
    return _run(sql, params, True)

# Tables whose INSERTs hand back a new id. settings is keyed by its own text key and
# has no id column, so it must never get a RETURNING clause.
_ID_TABLES = {"users", "providers", "customers", "listings", "orders", "plans",
              "subscriptions", "requests", "bids", "payouts", "notifications"}
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)

def execute(sql, params=()):
    """INSERT/UPDATE/DELETE. Returns the new row id for inserts into id tables."""
    if KIND == "postgres":
        m = _INSERT_RE.match(sql)
        if m and m.group(1).lower() in _ID_TABLES and "returning" not in sql.lower():
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
    return _run(sql, params, False)

# ----------------------------------------------------------------------------- schema
# One definition, rendered per dialect. {PK} {BLOB} {MONEY} are the only differences.
_TYPES = {
    "sqlite": {"PK": "INTEGER PRIMARY KEY AUTOINCREMENT", "BLOB": "BLOB", "MONEY": "REAL"},
    # float4 (Postgres REAL) cannot hold rupees without drift — DOUBLE PRECISION matches
    # what SQLite has been storing all along.
    "postgres": {"PK": "SERIAL PRIMARY KEY", "BLOB": "BYTEA", "MONEY": "DOUBLE PRECISION"},
}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id {PK},
        username TEXT UNIQUE NOT NULL,
        pw_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        linked_id INTEGER,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS providers (
        id {PK},
        display_name TEXT, owner_name TEXT, phone TEXT, email TEXT,
        area TEXT, pincode TEXT, radius_km {MONEY} DEFAULT 3,
        kinds TEXT, categories TEXT, cuisines TEXT, languages TEXT,
        diet TEXT, bio TEXT,
        fssai_no TEXT, kyc_done INTEGER DEFAULT 0, hygiene_done INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0, verify_note TEXT,
        photo {BLOB}, upi_id TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS customers (
        id {PK},
        full_name TEXT, phone TEXT, email TEXT, area TEXT,
        stay_type TEXT, pickup_point TEXT,
        diet TEXT, spice TEXT, avoid TEXT, budget_per_meal {MONEY},
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS listings (
        id {PK},
        provider_id INTEGER, kind TEXT, category TEXT, title TEXT, description TEXT,
        cuisine TEXT, diet TEXT, price {MONEY}, unit TEXT, cost_price {MONEY},
        market_price {MONEY},
        avail_date TEXT, slot TEXT, capacity INTEGER DEFAULT 0, sold INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1, photo {BLOB}, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS orders (
        id {PK},
        listing_id INTEGER, provider_id INTEGER, customer_id INTEGER,
        qty INTEGER, item_total {MONEY}, delivery_fee {MONEY}, platform_fee {MONEY},
        ops_fee {MONEY} DEFAULT 0, provider_payout {MONEY}, customer_total {MONEY},
        delivery_mode TEXT, note TEXT, status TEXT, for_date TEXT, slot TEXT,
        payment_status TEXT, payment_ref TEXT, paid_at TEXT,
        rating INTEGER, review TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS plans (
        id {PK},
        provider_id INTEGER, title TEXT, description TEXT, slot TEXT,
        days INTEGER, price {MONEY}, diet TEXT, active INTEGER DEFAULT 1, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS subscriptions (
        id {PK},
        plan_id INTEGER, provider_id INTEGER, customer_id INTEGER,
        start_date TEXT, days INTEGER, price {MONEY}, delivery_mode TEXT,
        prefs TEXT, paused INTEGER DEFAULT 0, status TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS requests (
        id {PK},
        customer_id INTEGER, kind TEXT, category TEXT, title TEXT, description TEXT,
        area TEXT, budget {MONEY}, needed_by TEXT, status TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS bids (
        id {PK},
        request_id INTEGER, provider_id INTEGER, price {MONEY}, eta TEXT,
        note TEXT, status TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS price_bands (
        category TEXT PRIMARY KEY, kind TEXT, min_price {MONEY}, max_price {MONEY},
        note TEXT, updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id {PK},
        to_phone TEXT, to_name TEXT, to_role TEXT, kind TEXT, body TEXT,
        order_id INTEGER, status TEXT, error TEXT, sent_at TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS payouts (
        id {PK},
        provider_id INTEGER, amount {MONEY}, orders_count INTEGER,
        method TEXT, ref TEXT, note TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )""",
]

# The queries the app actually runs on every page render.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_orders_provider ON orders (provider_id)",
    "CREATE INDEX IF NOT EXISTS ix_orders_customer ON orders (customer_id)",
    "CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status)",
    "CREATE INDEX IF NOT EXISTS ix_orders_payment ON orders (payment_status)",
    "CREATE INDEX IF NOT EXISTS ix_listings_live ON listings (active, avail_date)",
    "CREATE INDEX IF NOT EXISTS ix_listings_provider ON listings (provider_id)",
    "CREATE INDEX IF NOT EXISTS ix_subs_provider ON subscriptions (provider_id)",
    "CREATE INDEX IF NOT EXISTS ix_notifications_status ON notifications (status)",
    "CREATE INDEX IF NOT EXISTS ix_payouts_provider ON payouts (provider_id)",
    "CREATE INDEX IF NOT EXISTS ix_bids_request ON bids (request_id)",
]

# Columns added after the first release. Kept so a database created by an earlier
# version keeps working instead of needing a wipe.
NEW_COLUMNS = {
    "providers": {"photo": "{BLOB}", "upi_id": "TEXT"},
    "listings": {"photo": "{BLOB}", "cost_price": "{MONEY}"},
    "orders": {"payment_status": "TEXT", "payment_ref": "TEXT", "paid_at": "TEXT",
               "ops_fee": "{MONEY} DEFAULT 0"},
}

def _render(sql):
    return sql.format(**_TYPES[KIND])

def _columns(table):
    if KIND == "postgres":
        rows = q("SELECT column_name AS name FROM information_schema.columns "
                 "WHERE table_schema = current_schema() AND table_name = ?", (table,))
    else:
        rows = q(f"PRAGMA table_info({table})")
    return {r["name"] for r in rows}

def migrate():
    for table, cols in NEW_COLUMNS.items():
        have = _columns(table)
        for col, decl in cols.items():
            if col not in have:
                execute(f"ALTER TABLE {table} ADD COLUMN {col} {_render(decl)}")

def init_db(force=False):
    """Idempotent, and run once per process: on Streamlit every interaction reruns the
    script top to bottom, and re-issuing the whole schema each time meant a dozen
    round trips to a remote database before a single pixel was drawn."""
    global _ready
    if _ready and not force:
        return
    for stmt in SCHEMA:
        execute(_render(stmt))
    for stmt in INDEXES:
        execute(stmt)
    migrate()
    if not q("SELECT 1 FROM users WHERE role='admin'"):
        execute("INSERT INTO users (username, pw_hash, role, created_at) VALUES (?,?,?,?)",
                ("admin", hash_pw(admin_password()), "admin", now_iso()))
    for k, v in DEFAULT_SETTINGS.items():
        if not q("SELECT 1 FROM settings WHERE key=?", (k,)):
            execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
    _ready = True

# ----------------------------------------------------------------------------- settings
def setting(key, cast=str):
    rows = q("SELECT value FROM settings WHERE key=?", (key,))
    val = rows[0]["value"] if rows else DEFAULT_SETTINGS.get(key, "0")
    try:
        return cast(val)
    except (TypeError, ValueError):
        return cast(DEFAULT_SETTINGS.get(key, "0"))

def set_setting(key, value):
    execute("INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

# ----------------------------------------------------------------------------- passwords
# Salted PBKDF2-SHA256. The first release stored bare SHA-256, which is one cheap
# guess per password; those hashes are still accepted so nobody is locked out, and
# each one is replaced the next time its owner signs in.
_ITERATIONS = 240_000
_LEGACY_RE = re.compile(r"^[0-9a-f]{64}$")

def hash_pw(plain, *, salt=None, iterations=_ITERATIONS):
    salt = salt or _secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"

def verify_pw(plain, stored):
    if not stored:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters, salt, want = stored.split("$", 3)
            got = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt), int(iters))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(got.hex(), want)
    if _LEGACY_RE.match(stored):
        return hmac.compare_digest(hashlib.sha256(plain.encode()).hexdigest(), stored)
    return False

def needs_rehash(stored):
    return bool(stored) and not stored.startswith("pbkdf2_sha256$")
