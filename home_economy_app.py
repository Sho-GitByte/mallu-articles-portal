# home_economy_app.py
# GharSe — Neighbourhood Home Economy Marketplace
# Home food · handmade products · home-based services, sold by verified women entrepreneurs.
# Single-file Streamlit app. Roles: Admin (platform) | Provider (home entrepreneur) | Customer
# Run locally:  pip install -r requirements.txt  &&  streamlit run home_economy_app.py

import streamlit as st
import sqlite3
import hashlib
import re
from datetime import datetime, date, timedelta
import pandas as pd

# ----------------------------------------------------------------------------- CONFIG
st.set_page_config(
    page_title="GharSe | Neighbourhood Home Economy",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = "gharse.db"

KINDS = ["Food", "Product", "Service"]

CATEGORIES = {
    "Food": [
        "Daily Meal (Veg)", "Daily Meal (Non-Veg)", "Breakfast", "Snacks & Tiffin",
        "Biryani & Rice", "Curries & Sides", "Sweets & Bakes", "Pickles & Masala",
        "Festival / Bulk Order", "Diet / High-Protein", "Kids Meal", "Elderly Meal",
    ],
    "Product": [
        "Flowers & Garlands", "Tailoring & Stitching", "Saree Work & Embroidery",
        "Handloom", "Handicraft & Decor", "Artificial Jewellery", "Candles & Soaps",
        "Gifts & Hampers", "Knitting & Crochet",
    ],
    "Service": [
        "Alterations", "Ironing & Laundry", "Mehendi", "Beauty at Home",
        "Tuition", "Day-care / Baby-sitting", "Event Cooking", "Packing & Assembly",
        "Petty Home Chores",
    ],
}

CUISINES = [
    "South Indian", "North Indian", "Bengali", "Assamese / North-East", "Kerala",
    "Karnataka", "Andhra", "Tamil", "Hyderabadi", "Maharashtrian", "Gujarati / Jain",
    "Chinese / Indo-Chinese", "Other",
]

DIETS = ["Vegetarian", "Non-Vegetarian", "Egg only", "Jain", "No preference"]
SPICE = ["Mild", "Medium", "Spicy"]

AREAS = [
    "HSR Layout", "Koramangala", "BTM Layout", "Bellandur", "Marathahalli",
    "Whitefield", "Electronic City", "Indiranagar", "Jayanagar", "JP Nagar",
    "Banashankari", "Rajajinagar", "Malleshwaram", "Yelahanka", "Hebbal",
    "CBD / MG Road", "Other",
]

LANGUAGES = ["Kannada", "Hindi", "English", "Tamil", "Telugu", "Malayalam", "Bengali", "Marathi", "Assamese"]

SLOTS = ["Breakfast (7–10 AM)", "Lunch (12–2:30 PM)", "Evening (4–6 PM)", "Dinner (7–9:30 PM)", "Anytime"]
UNITS = ["per meal", "per plate", "per piece", "per kg", "per dozen", "per set", "per hour", "per job"]

DELIVERY_MODES = ["Home delivery", "Community pickup point", "Self pickup"]

ORDER_FLOW = ["Placed", "Accepted", "Preparing", "Ready", "Out for delivery", "Delivered"]
ORDER_STATUSES = ORDER_FLOW + ["Cancelled"]

REQUEST_STATUSES = ["Open", "Assigned", "Completed", "Cancelled"]
BID_STATUSES = ["Offered", "Accepted", "Declined"]

PLAN_PERIODS = [7, 15, 26, 30]

PAYMENT_STATUSES = ["Unpaid", "Claimed", "Paid", "Refund due", "Refunded"]

DEFAULT_SETTINGS = {
    "platform_upi": "",          # the UPI ID customers pay into; set by admin before go-live
    "brand_name": "GharSe",
    "platform_pct": "8",         # onboarding, verification, the app itself
    "ops_pct": "2",              # payment processing, support, coordination
    "commission_pct": "8",       # legacy single rate; kept so old databases still read
    "delivery_fee": "15",        # home delivery, per order
    "pickup_point_fee": "5",     # batched drop at a PG / office / apartment gate
    "self_pickup_fee": "0",
    "min_order": "60",
}

# ----------------------------------------------------------------------------- DB LAYER
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def q(sql, params=()):
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows

def execute(sql, params=()):
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last

def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()

def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            linked_id INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT, owner_name TEXT, phone TEXT, email TEXT,
            area TEXT, pincode TEXT, radius_km REAL DEFAULT 3,
            kinds TEXT, categories TEXT, cuisines TEXT, languages TEXT,
            diet TEXT, bio TEXT,
            fssai_no TEXT, kyc_done INTEGER DEFAULT 0, hygiene_done INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0, verify_note TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT, phone TEXT, email TEXT, area TEXT,
            stay_type TEXT, pickup_point TEXT,
            diet TEXT, spice TEXT, avoid TEXT, budget_per_meal REAL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER, kind TEXT, category TEXT, title TEXT, description TEXT,
            cuisine TEXT, diet TEXT, price REAL, unit TEXT, market_price REAL,
            avail_date TEXT, slot TEXT, capacity INTEGER DEFAULT 0, sold INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER, provider_id INTEGER, customer_id INTEGER,
            qty INTEGER, item_total REAL, delivery_fee REAL, platform_fee REAL,
            provider_payout REAL, customer_total REAL,
            delivery_mode TEXT, note TEXT, status TEXT, for_date TEXT, slot TEXT,
            rating INTEGER, review TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER, title TEXT, description TEXT, slot TEXT,
            days INTEGER, price REAL, diet TEXT, active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER, provider_id INTEGER, customer_id INTEGER,
            start_date TEXT, days INTEGER, price REAL, delivery_mode TEXT,
            prefs TEXT, paused INTEGER DEFAULT 0, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER, kind TEXT, category TEXT, title TEXT, description TEXT,
            area TEXT, budget REAL, needed_by TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER, provider_id INTEGER, price REAL, eta TEXT,
            note TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS price_bands (
            category TEXT PRIMARY KEY, kind TEXT, min_price REAL, max_price REAL,
            note TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_phone TEXT, to_name TEXT, to_role TEXT, kind TEXT, body TEXT,
            order_id INTEGER, status TEXT, error TEXT, sent_at TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER, amount REAL, orders_count INTEGER,
            method TEXT, ref TEXT, note TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    # Seed the admin login and fee defaults only — NOT sample content.
    if not q("SELECT 1 FROM users WHERE role='admin'"):
        execute(
            "INSERT INTO users (username, pw_hash, role, created_at) VALUES (?,?,?,?)",
            ("admin", hash_pw("admin@123"), "admin", datetime.utcnow().isoformat()),
        )
    for k, v in DEFAULT_SETTINGS.items():
        if not q("SELECT 1 FROM settings WHERE key=?", (k,)):
            execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
    migrate()

# Additive column migrations, so a database created by an earlier version keeps working.
NEW_COLUMNS = {
    "providers": {"photo": "BLOB", "upi_id": "TEXT"},
    "listings": {"photo": "BLOB", "cost_price": "REAL"},
    "orders": {"payment_status": "TEXT", "payment_ref": "TEXT", "paid_at": "TEXT",
               "ops_fee": "REAL DEFAULT 0"},
}

def migrate():
    for table, cols in NEW_COLUMNS.items():
        have = {r["name"] for r in q(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

# ----------------------------------------------------------------------------- HELPERS
def idx(lst, val, default=0):
    return lst.index(val) if val in lst else default

def csv_join(items):
    return ", ".join(items) if items else ""

def csv_split(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]

def inr(n):
    n = int(round(n or 0))
    s = str(abs(n))
    if len(s) > 3:
        last3, rest = s[-3:], s[:-3]
        rest = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", rest)
        s = rest + "," + last3
    return ("-" if n < 0 else "") + "₹" + s

def mask_phone(p):
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    if len(d) < 4:
        return "•••• (shared after order)"
    return d[:2] + "•" * (len(d) - 4) + d[-2:]

def setting(key, cast=str):
    rows = q("SELECT value FROM settings WHERE key=?", (key,))
    val = rows[0]["value"] if rows else DEFAULT_SETTINGS.get(key, "0")
    try:
        return cast(val)
    except (TypeError, ValueError):
        return cast(DEFAULT_SETTINGS.get(key, "0"))

def set_setting(key, value):
    execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )

def delivery_fee_for(mode):
    return {
        "Home delivery": setting("delivery_fee", float),
        "Community pickup point": setting("pickup_point_fee", float),
        "Self pickup": setting("self_pickup_fee", float),
    }.get(mode, setting("delivery_fee", float))

def split_money(item_total, delivery_mode):
    """The one place fees are computed. Every screen shows this same breakdown.

    The commission is two named parts, not one blended number:
      platform_fee — onboarding, verification, the app itself
      ops_fee      — payment processing, support, the team's coordination
    """
    delivery = delivery_fee_for(delivery_mode)
    platform = round(item_total * setting("platform_pct", float) / 100.0, 2)
    ops = round(item_total * setting("ops_pct", float) / 100.0, 2)
    payout = round(item_total - platform - ops, 2)
    return {
        "item_total": round(item_total, 2),
        "delivery_fee": delivery,
        "platform_fee": platform,
        "ops_fee": ops,
        "commission": round(platform + ops, 2),
        "provider_payout": payout,
        "customer_total": round(item_total + delivery, 2),
    }

def upi_link(amount, note):
    """Deep link that opens GPay / PhonePe / Paytm on a phone with the amount filled in."""
    pa = (setting("platform_upi", str) or "").strip()
    if not pa:
        return None
    from urllib.parse import quote
    brand = setting("brand_name", str) or "GharSe"
    return (f"upi://pay?pa={quote(pa)}&pn={quote(brand)}&am={amount:.2f}"
            f"&cu=INR&tn={quote(note[:40])}")

def payout_due(provider_id):
    """Escrow rule: money is releasable only once the order is delivered *and* paid for."""
    earned = q(
        "SELECT COALESCE(SUM(provider_payout),0) s, COUNT(*) c FROM orders "
        "WHERE provider_id=? AND status='Delivered' AND payment_status='Paid'", (provider_id,)
    )[0]
    already = q("SELECT COALESCE(SUM(amount),0) s FROM payouts WHERE provider_id=?", (provider_id,))[0]["s"]
    return round((earned["s"] or 0) - (already or 0), 2), earned["c"]

def cancel_order(o):
    """Free the capacity back up, and flag money that now has to travel back."""
    execute("UPDATE orders SET status='Cancelled' WHERE id=?", (o["id"],))
    execute("UPDATE listings SET sold=MAX(0, sold-?) WHERE id=?", (o["qty"], o["listing_id"]))
    if (o["payment_status"] or "Unpaid") in ("Claimed", "Paid"):
        execute("UPDATE orders SET payment_status='Refund due' WHERE id=?", (o["id"],))

def price_band(category):
    rows = q("SELECT * FROM price_bands WHERE category=?", (category,))
    return dict(rows[0]) if rows else None

def price_verdict(category, price, cost=None):
    """The team's reasonable-price policy, applied by the app instead of by phone.
    Returns (level, message) where level is 'ok' | 'warn' | 'bad' | None."""
    if cost and price < cost:
        return "bad", f"This is below what it costs you to make ({inr(cost)}). You'd lose money on every order."
    if cost and price < cost * 1.15:
        return "warn", f"Only {inr(price - cost)} left after your cost of {inr(cost)} — price it a little higher."
    b = price_band(category)
    if not b:
        return None, None
    lo, hi = b["min_price"], b["max_price"]
    if lo and price < lo:
        return "warn", f"Below the usual {inr(lo)}–{inr(hi)} for {category} in this area. Fine if you meant it."
    if hi and price > hi:
        return "warn", (f"Above the usual {inr(lo)}–{inr(hi)} for {category}. Customers may skip it unless the "
                        f"description explains why it's worth more.")
    return "ok", f"In the fair range for {category} ({inr(lo)}–{inr(hi)})."

def pay_pill(status):
    status = status or "Unpaid"
    cls = {"Paid": "ok", "Claimed": "", "Unpaid": "warn", "Refund due": "warn", "Refunded": ""}.get(status, "")
    return f"<span class='pill {cls}'>{status}</span>"

# ----------------------------------------------------------------------------- WHATSAPP
# Two delivery paths. With Cloud API credentials in the environment, messages send
# themselves. Without them — which is where a pilot starts — every message is queued
# and sent by one click from the Outbox, which needs no Meta account at all.
def wa_number(phone):
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(d) == 10:
        d = "91" + d
    return d if len(d) >= 11 else ""

def wa_link(phone, body):
    from urllib.parse import quote
    n = wa_number(phone)
    return f"https://wa.me/{n}?text={quote(body)}" if n else None

def wa_credentials():
    import os
    tok, phone_id = os.environ.get("WHATSAPP_TOKEN"), os.environ.get("WHATSAPP_PHONE_ID")
    if not (tok and phone_id):
        try:
            tok = tok or st.secrets.get("WHATSAPP_TOKEN")
            phone_id = phone_id or st.secrets.get("WHATSAPP_PHONE_ID")
        except Exception:
            pass
    return tok, phone_id

def send_whatsapp(phone, body):
    """Returns (sent, error). Business-initiated messages outside the 24-hour customer
    service window need an approved template — the API error is stored, not hidden."""
    tok, phone_id = wa_credentials()
    n = wa_number(phone)
    if not (tok and phone_id):
        return False, "no API credentials — send from the Outbox"
    if not n:
        return False, "no usable phone number"
    try:
        import requests
        r = requests.post(
            f"https://graph.facebook.com/v20.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {tok}"},
            json={"messaging_product": "whatsapp", "to": n, "type": "text", "text": {"body": body}},
            timeout=15,
        )
        if r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)[:200]

def notify(phone, name, role, kind, body, order_id=None):
    now = datetime.utcnow().isoformat()
    sent, err = send_whatsapp(phone, body)
    execute(
        "INSERT INTO notifications (to_phone, to_name, to_role, kind, body, order_id, status, error, "
        "sent_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (phone, name, role, kind, body, order_id, "Sent" if sent else "Queued", err,
         now if sent else None, now),
    )

def notify_provider(provider_id, kind, body, order_id=None):
    rows = q("SELECT display_name, phone FROM providers WHERE id=?", (provider_id,))
    if rows:
        notify(rows[0]["phone"], rows[0]["display_name"], "provider", kind, body, order_id)

def notify_customer(customer_id, kind, body, order_id=None):
    rows = q("SELECT full_name, phone FROM customers WHERE id=?", (customer_id,))
    if rows:
        notify(rows[0]["phone"], rows[0]["full_name"], "customer", kind, body, order_id)

def authenticate(u, p):
    rows = q("SELECT * FROM users WHERE username=?", (u,))
    if rows and rows[0]["pw_hash"] == hash_pw(p):
        return dict(rows[0])
    return None

def provider_of(user):
    rows = q("SELECT * FROM providers WHERE id=?", (user["linked_id"],))
    return dict(rows[0]) if rows else {}

def customer_of(user):
    rows = q("SELECT * FROM customers WHERE id=?", (user["linked_id"],))
    return dict(rows[0]) if rows else {}

def can_sell_food(p):
    """Food is the one category we gate: verified provider + an FSSAI number on file."""
    return bool(p.get("verified")) and bool((p.get("fssai_no") or "").strip())

def provider_rating(pid):
    rows = q("SELECT AVG(rating) a, COUNT(rating) c FROM orders WHERE provider_id=? AND rating IS NOT NULL", (pid,))
    avg, cnt = rows[0]["a"], rows[0]["c"]
    return (round(avg, 1) if avg else None), (cnt or 0)

def repeat_customers(pid):
    rows = q(
        "SELECT COUNT(*) c FROM (SELECT customer_id FROM orders WHERE provider_id=? AND status='Delivered' "
        "GROUP BY customer_id HAVING COUNT(*) > 1)", (pid,)
    )
    return rows[0]["c"]

def df(rows):
    return pd.DataFrame([dict(r) for r in rows])

MAX_IMG_MB = 5

def process_image(uploaded, max_px=900):
    """Downscale an upload so the database stays small. Falls back to the raw bytes
    if Pillow is unavailable — a slightly heavy row beats a lost photo."""
    if uploaded is None:
        return None
    raw = uploaded.getvalue()
    if not raw:
        return None
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        return buf.getvalue()
    except Exception:
        return raw

def photo_of(row, fallback=None):
    """Listing photo, else the kitchen's own photo, else nothing."""
    for candidate in (row.get("photo") if isinstance(row, dict) else None, fallback):
        if candidate:
            return bytes(candidate)
    return None

def today_iso():
    return date.today().isoformat()

# ----------------------------------------------------------------------------- THEME / CSS
def inject_css():
    st.markdown(
        """
        <style>
        :root {
            --bg0:#0d0a07; --panel:rgba(255,255,255,0.04);
            --line:rgba(255,166,43,0.22); --amber:#ffa62b; --amber2:#ffc46b;
            --mint:#3ddc97; --txt:#f6efe6;
        }
        .stApp {
            background: radial-gradient(1100px 560px at 82% -12%, #2a1a08 0%, transparent 55%),
                        linear-gradient(160deg, #0d0a07 0%, #14100a 52%, #0d0a07 100%);
            color: var(--txt);
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #171008 0%, #0d0a07 100%);
            border-right: 1px solid var(--line);
        }
        section[data-testid="stSidebar"] * { color: var(--txt); }
        h1,h2,h3,h4,h5,h6, p, span, label, div { color: var(--txt); }
        .block-container { padding-top: 1.4rem; }

        .stTextInput input, .stNumberInput input, .stTextArea textarea, .stDateInput input,
        div[data-baseweb="select"] > div {
            background:#1b1409 !important; color:var(--txt) !important;
            border:1px solid var(--line) !important; border-radius:10px !important;
        }
        .stTextInput input:focus, .stTextArea textarea:focus { border-color:var(--amber) !important; }

        .stButton>button, .stDownloadButton>button, .stForm button[kind="primaryFormSubmit"] {
            background: linear-gradient(135deg, #f08b1d 0%, #ffa62b 100%);
            color:#20160a; border:0; border-radius:10px; font-weight:700;
            box-shadow:0 6px 18px rgba(255,166,43,0.22); transition:.15s;
        }
        .stButton>button:hover, .stDownloadButton>button:hover { filter:brightness(1.08); transform:translateY(-1px); }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--panel); border:1px solid var(--line) !important;
            border-radius:14px; box-shadow:0 8px 26px rgba(0,0,0,0.4);
        }
        .stTabs [data-baseweb="tab-list"] { gap:6px; }
        .stTabs [data-baseweb="tab"] { background:#1b1409; border-radius:8px 8px 0 0; padding:8px 16px; }
        .streamlit-expanderHeader, details summary { color:var(--txt) !important; }

        .brand { font-size:1.35rem; font-weight:800; letter-spacing:.3px; }
        .brand .accent { color:var(--amber2); }
        .brand-sub { font-size:.7rem; letter-spacing:3px; color:#c2a883; margin-top:-4px; }
        .page-title { font-size:1.7rem; font-weight:800; margin-bottom:.2rem; }
        .page-sub { color:#c2a883; margin-bottom:1rem; }
        .kpi { background:linear-gradient(160deg, rgba(255,166,43,.14), rgba(255,166,43,.03));
               border:1px solid var(--line); border-radius:16px; padding:16px 18px;
               box-shadow:0 8px 26px rgba(0,0,0,.35); height:100%; }
        .kpi .k-label { font-size:.76rem; color:#c2a883; text-transform:uppercase; letter-spacing:1px; }
        .kpi .k-val { font-size:1.85rem; font-weight:800; margin-top:6px; color:#fff; }
        .pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:.72rem;
                border:1px solid var(--line); background:rgba(255,166,43,.12); color:var(--amber2);
                margin:2px 4px 2px 0; }
        .pill.ok { border-color:rgba(61,220,151,.4); background:rgba(61,220,151,.12); color:var(--mint); }
        .pill.warn { border-color:rgba(255,99,99,.4); background:rgba(255,99,99,.12); color:#ff8b8b; }
        .price { font-size:1.5rem; font-weight:800; color:#fff; }
        .save { color:var(--mint); font-weight:700; font-size:.85rem; }
        .muted { color:#c2a883; font-size:.85rem; }
        .split { font-size:.8rem; color:#c2a883; border-top:1px dashed var(--line); margin-top:8px; padding-top:6px; }
        #MainMenu, footer {visibility:hidden;}
        </style>
        """,
        unsafe_allow_html=True,
    )

def kpi(col, label, value):
    col.markdown(
        f"<div class='kpi'><div class='k-label'>{label}</div><div class='k-val'>{value}</div></div>",
        unsafe_allow_html=True,
    )

def money_split_caption(s):
    return (
        f"<div class='split'>Customer pays <b>{inr(s['customer_total'])}</b> &nbsp;·&nbsp; "
        f"Home entrepreneur gets <b>{inr(s['provider_payout'])}</b> &nbsp;·&nbsp; "
        f"Delivery {inr(s['delivery_fee'])} &nbsp;·&nbsp; "
        f"Platform {inr(s['platform_fee'])} &nbsp;·&nbsp; "
        f"Processing &amp; support {inr(s.get('ops_fee', 0))}</div>"
    )

# ----------------------------------------------------------------------------- AUTH VIEWS
def login_register_view():
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown(
            "<div class='brand' style='text-align:center'>Ghar<span class='accent'>Se</span></div>"
            "<div class='brand-sub' style='text-align:center'>EARN FROM HOME · BUY FROM HOME</div><br>",
            unsafe_allow_html=True,
        )
        tab_login, tab_reg = st.tabs(["Sign in", "Register"])
        with tab_login:
            with st.form("login"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                if st.form_submit_button("Sign in", use_container_width=True):
                    user = authenticate(u, p)
                    if user:
                        st.session_state["user"] = user
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")
            st.caption("Admin demo login → username **admin** · password **admin@123** (change it after first sign-in).")
        with tab_reg:
            with st.form("register"):
                acct = st.radio("I want to", ["Sell from home (Home entrepreneur)", "Buy home-made (Customer)"],
                                horizontal=False)
                name = st.text_input("Kitchen / business name" if acct.startswith("Sell") else "Full name")
                area = st.selectbox("Your area", AREAS)
                ru = st.text_input("Choose a username")
                rp = st.text_input("Choose a password", type="password")
                rp2 = st.text_input("Confirm password", type="password")
                if st.form_submit_button("Create account", use_container_width=True):
                    if not (name and ru and rp):
                        st.error("Please fill all fields.")
                    elif rp != rp2:
                        st.error("Passwords do not match.")
                    elif q("SELECT 1 FROM users WHERE username=?", (ru,)):
                        st.error("That username is taken.")
                    else:
                        now = datetime.utcnow().isoformat()
                        if acct.startswith("Sell"):
                            lid = execute(
                                "INSERT INTO providers (display_name, area, created_at) VALUES (?,?,?)",
                                (name, area, now),
                            )
                            role = "provider"
                        else:
                            lid = execute(
                                "INSERT INTO customers (full_name, area, created_at) VALUES (?,?,?)",
                                (name, area, now),
                            )
                            role = "customer"
                        execute(
                            "INSERT INTO users (username, pw_hash, role, linked_id, created_at) VALUES (?,?,?,?,?)",
                            (ru, hash_pw(rp), role, lid, now),
                        )
                        st.session_state["user"] = dict(q("SELECT * FROM users WHERE username=?", (ru,))[0])
                        st.success("Account created. Loading your portal…")
                        st.rerun()

# ----------------------------------------------------------------------------- SIDEBAR NAV
def sidebar_nav(user):
    with st.sidebar:
        st.markdown(
            "<div class='brand'>Ghar<span class='accent'>Se</span></div>"
            "<div class='brand-sub'>HOME ECONOMY</div>",
            unsafe_allow_html=True,
        )
        st.write("")
        role = user["role"]
        if role == "admin":
            pages = ["Dashboard", "Home Entrepreneurs", "Listings", "Price Guidance", "Orders", "Payouts",
                     "Subscriptions", "Custom Requests", "WhatsApp Outbox", "Fees & Settings"]
        elif role == "provider":
            pages = ["My Profile & Verification", "My Listings", "Orders Received",
                     "Meal Plans & Subscribers", "Open Requests", "My Earnings"]
        else:
            pages = ["Discover Near Me", "My Orders", "My Subscriptions",
                     "Post a Request", "My Preferences"]
        choice = st.radio("Navigate", pages, label_visibility="collapsed")
        st.divider()
        st.markdown(
            f"<span class='muted'>Signed in as</span><br><b>{user['username']}</b> · {role}",
            unsafe_allow_html=True,
        )
        if st.button("Log out", use_container_width=True):
            st.session_state.pop("user", None)
            st.rerun()
    return choice

# ----------------------------------------------------------------------------- ADMIN PAGES
def admin_dashboard():
    st.markdown("<div class='page-title'>Platform Dashboard</div>"
                "<div class='page-sub'>Every figure below is computed from real portal activity — nothing is seeded.</div>",
                unsafe_allow_html=True)

    n_prov = q("SELECT COUNT(*) c FROM providers")[0]["c"]
    n_verif = q("SELECT COUNT(*) c FROM providers WHERE verified=1")[0]["c"]
    n_cust = q("SELECT COUNT(*) c FROM customers")[0]["c"]
    n_orders = q("SELECT COUNT(*) c FROM orders WHERE status!='Cancelled'")[0]["c"]
    gmv = q("SELECT COALESCE(SUM(customer_total),0) s FROM orders WHERE status='Delivered'")[0]["s"]
    payout = q("SELECT COALESCE(SUM(provider_payout),0) s FROM orders WHERE status='Delivered'")[0]["s"]
    take = q("SELECT COALESCE(SUM(platform_fee),0) + COALESCE(SUM(ops_fee),0) s FROM orders "
             "WHERE status='Delivered'")[0]["s"]
    n_subs = q("SELECT COUNT(*) c FROM subscriptions WHERE status='Active'")[0]["c"]

    c = st.columns(4)
    kpi(c[0], "Home entrepreneurs", f"{n_prov}")
    kpi(c[1], "Verified", f"{n_verif}")
    kpi(c[2], "Customers", f"{n_cust}")
    kpi(c[3], "Orders", f"{n_orders}")
    st.write("")
    c = st.columns(4)
    kpi(c[0], "Delivered GMV", inr(gmv))
    kpi(c[1], "Paid to women", inr(payout))
    kpi(c[2], "Platform revenue", inr(take))
    kpi(c[3], "Active subscriptions", f"{n_subs}")
    st.write("")

    st.markdown("#### Where the money went")
    if gmv:
        st.caption(
            f"Of {inr(gmv)} collected, {inr(payout)} ({payout / gmv * 100:.0f}%) reached the home entrepreneurs. "
            "Keeping that share high is the brand promise — the dashboard exists to hold us to it."
        )
    else:
        st.caption("No delivered orders yet. This split appears the moment the first order is delivered.")

    left, right = st.columns(2)
    with left:
        st.markdown("**Orders by area**")
        rows = q(
            "SELECT p.area area, COUNT(*) orders FROM orders o JOIN providers p ON p.id=o.provider_id "
            "WHERE o.status!='Cancelled' GROUP BY p.area ORDER BY orders DESC"
        )
        if rows:
            st.bar_chart(df(rows).set_index("area"))
        else:
            st.caption("No orders yet.")
    with right:
        st.markdown("**Listings by kind**")
        rows = q("SELECT kind, COUNT(*) listings FROM listings GROUP BY kind")
        if rows:
            st.bar_chart(df(rows).set_index("kind"))
        else:
            st.caption("No listings yet.")

    st.markdown("#### Repeat rate — the metric that matters")
    rows = q("SELECT COUNT(DISTINCT customer_id) c FROM orders WHERE status='Delivered'")
    buyers = rows[0]["c"]
    rows = q(
        "SELECT COUNT(*) c FROM (SELECT customer_id FROM orders WHERE status='Delivered' "
        "GROUP BY customer_id HAVING COUNT(*) > 1)"
    )
    repeats = rows[0]["c"]
    if buyers:
        st.caption(f"{repeats} of {buyers} customers have ordered more than once "
                   f"({repeats / buyers * 100:.0f}%). Downloads are vanity; reorders are product-market fit.")
    else:
        st.caption("No delivered orders yet.")

def admin_providers():
    st.markdown("<div class='page-title'>Home Entrepreneurs</div>"
                "<div class='page-sub'>Verify KYC, hygiene training and FSSAI before a kitchen can publish food.</div>",
                unsafe_allow_html=True)
    rows = q("SELECT * FROM providers ORDER BY created_at DESC")
    if not rows:
        st.info("No one has registered yet.")
        return

    f1, f2 = st.columns(2)
    area_f = f1.selectbox("Area", ["All"] + AREAS)
    stat_f = f2.selectbox("Status", ["All", "Pending verification", "Verified"])

    for r in rows:
        p = dict(r)
        if area_f != "All" and p["area"] != area_f:
            continue
        if stat_f == "Verified" and not p["verified"]:
            continue
        if stat_f == "Pending verification" and p["verified"]:
            continue
        badge = "<span class='pill ok'>Verified ✓</span>" if p["verified"] else "<span class='pill warn'>Pending</span>"
        with st.container(border=True):
            st.markdown(
                f"**{p['display_name'] or 'Unnamed'}** · {p['area'] or '—'} {badge}<br>"
                f"<span class='muted'>{p['owner_name'] or '—'} · {p['phone'] or '—'} · "
                f"FSSAI: {p['fssai_no'] or 'not provided'}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "".join(f"<span class='pill'>{c}</span>" for c in csv_split(p["categories"])[:8]),
                unsafe_allow_html=True,
            )
            a, b, c_, d = st.columns(4)
            kyc = a.checkbox("KYC done", value=bool(p["kyc_done"]), key=f"kyc{p['id']}")
            hyg = b.checkbox("Hygiene training", value=bool(p["hygiene_done"]), key=f"hyg{p['id']}")
            ver = c_.checkbox("Verified", value=bool(p["verified"]), key=f"ver{p['id']}")
            if d.button("Save", key=f"savep{p['id']}", use_container_width=True):
                execute("UPDATE providers SET kyc_done=?, hygiene_done=?, verified=? WHERE id=?",
                        (int(kyc), int(hyg), int(ver), p["id"]))
                st.success("Updated.")
                st.rerun()
            if not p["fssai_no"] and ver:
                st.caption("⚠️ No FSSAI number on file — food listings stay blocked until one is added.")

def admin_listings():
    st.markdown("<div class='page-title'>Listings</div>"
                "<div class='page-sub'>Everything on sale across the network.</div>", unsafe_allow_html=True)
    rows = q(
        "SELECT l.*, p.display_name, p.area, p.verified FROM listings l "
        "JOIN providers p ON p.id=l.provider_id ORDER BY l.created_at DESC"
    )
    if not rows:
        st.info("No listings yet.")
        return
    d = df(rows)[["id", "display_name", "area", "kind", "category", "title", "price", "unit",
                  "avail_date", "capacity", "sold", "active"]]
    st.dataframe(d, use_container_width=True, hide_index=True)

def admin_orders():
    st.markdown("<div class='page-title'>Orders</div>"
                "<div class='page-sub'>Full ledger with the fee split on every line.</div>", unsafe_allow_html=True)
    todo = q(
        "SELECT o.*, p.display_name provider, c.full_name customer, l.title item FROM orders o "
        "JOIN providers p ON p.id=o.provider_id JOIN customers c ON c.id=o.customer_id "
        "JOIN listings l ON l.id=o.listing_id WHERE o.payment_status IN ('Claimed','Refund due') ORDER BY o.id"
    )
    if todo:
        st.markdown("#### Needs your confirmation")
        for r in todo:
            o = dict(r)
            with st.container(border=True):
                c = st.columns([3, 1, 1])
                c[0].markdown(
                    f"**#{o['id']} · {o['item']} × {o['qty']}**<br>"
                    f"<span class='muted'>{o['customer']} → {o['provider']} · ref {o['payment_ref'] or '—'}</span>",
                    unsafe_allow_html=True,
                )
                c[1].markdown(f"<div class='price'>{inr(o['customer_total'])}</div>", unsafe_allow_html=True)
                c[2].markdown(pay_pill(o["payment_status"]), unsafe_allow_html=True)
                if o["payment_status"] == "Claimed":
                    b = st.columns([1, 1, 3])
                    if b[0].button("Confirm received", key=f"conf{o['id']}", use_container_width=True):
                        execute("UPDATE orders SET payment_status='Paid' WHERE id=?", (o["id"],))
                        notify_provider(o["provider_id"], "paid",
                                        f"Order #{o['id']} ({o['qty']} × {o['item']}) is paid for. "
                                        f"You can start cooking — {inr(o['provider_payout'])} is yours "
                                        f"once it's delivered.", o["id"])
                        st.rerun()
                    if b[1].button("Not received", key=f"nrec{o['id']}", use_container_width=True):
                        execute("UPDATE orders SET payment_status='Unpaid', payment_ref=NULL WHERE id=?", (o["id"],))
                        st.rerun()
                else:
                    if st.button("Mark refunded", key=f"refd{o['id']}"):
                        execute("UPDATE orders SET payment_status='Refunded' WHERE id=?", (o["id"],))
                        st.rerun()
        st.divider()

    rows = q(
        "SELECT o.id, o.created_at, p.display_name provider, c.full_name customer, l.title item, "
        "o.qty, o.customer_total, o.provider_payout, o.platform_fee, o.delivery_fee, o.delivery_mode, "
        "o.status, o.payment_status, o.payment_ref "
        "FROM orders o JOIN providers p ON p.id=o.provider_id JOIN customers c ON c.id=o.customer_id "
        "JOIN listings l ON l.id=o.listing_id ORDER BY o.id DESC"
    )
    if not rows:
        st.info("No orders yet.")
        return
    st.dataframe(df(rows), use_container_width=True, hide_index=True)

def admin_subscriptions():
    st.markdown("<div class='page-title'>Subscriptions</div>"
                "<div class='page-sub'>Recurring revenue — the part of this business that compounds.</div>",
                unsafe_allow_html=True)
    rows = q(
        "SELECT s.id, p.display_name provider, c.full_name customer, pl.title plan, "
        "s.start_date, s.days, s.price, s.delivery_mode, s.paused, s.status "
        "FROM subscriptions s JOIN providers p ON p.id=s.provider_id "
        "JOIN customers c ON c.id=s.customer_id JOIN plans pl ON pl.id=s.plan_id ORDER BY s.id DESC"
    )
    if not rows:
        st.info("No subscriptions yet.")
        return
    st.dataframe(df(rows), use_container_width=True, hide_index=True)

def admin_requests():
    st.markdown("<div class='page-title'>Custom Requests</div>"
                "<div class='page-sub'>The reverse marketplace: customers post a need, women bid.</div>",
                unsafe_allow_html=True)
    rows = q(
        "SELECT r.*, c.full_name customer, (SELECT COUNT(*) FROM bids b WHERE b.request_id=r.id) bids "
        "FROM requests r JOIN customers c ON c.id=r.customer_id ORDER BY r.id DESC"
    )
    if not rows:
        st.info("No requests posted yet.")
        return
    st.dataframe(
        df(rows)[["id", "customer", "kind", "category", "title", "area", "budget", "needed_by", "bids", "status"]],
        use_container_width=True, hide_index=True,
    )

def admin_payouts():
    st.markdown("<div class='page-title'>Payouts</div>"
                "<div class='page-sub'>Money is held until an order is delivered and paid for — then it goes out.</div>",
                unsafe_allow_html=True)
    provs = q("SELECT * FROM providers ORDER BY display_name")
    if not provs:
        st.info("No home entrepreneurs yet.")
        return

    total_due = 0.0
    pending = []
    for r in provs:
        p = dict(r)
        due, cnt = payout_due(p["id"])
        if due > 0.005:
            pending.append((p, due, cnt))
            total_due += due
    c = st.columns(3)
    kpi(c[0], "Owed right now", inr(total_due))
    kpi(c[1], "Women awaiting payout", f"{len(pending)}")
    kpi(c[2], "Paid out to date",
        inr(q("SELECT COALESCE(SUM(amount),0) s FROM payouts")[0]["s"]))
    st.write("")

    if not pending:
        st.success("Nothing outstanding — every delivered, paid order has been settled.")
    for p, due, cnt in pending:
        with st.container(border=True):
            c = st.columns([3, 1])
            c[0].markdown(
                f"**{p['display_name']}** · {p['area'] or '—'}<br>"
                f"<span class='muted'>{cnt} delivered & paid order(s) · UPI: "
                f"{p['upi_id'] or 'NOT PROVIDED'}</span>",
                unsafe_allow_html=True,
            )
            c[1].markdown(f"<div class='price'>{inr(due)}</div><span class='muted'>owed</span>",
                          unsafe_allow_html=True)
            if not (p["upi_id"] or "").strip():
                st.caption("⚠️ No UPI ID on her profile — ask her to add one before paying.")
            with st.form(f"po{p['id']}"):
                fc = st.columns([1, 1, 2])
                amount = fc[0].number_input("Amount (₹)", 0.0, float(due), float(due), step=1.0,
                                            key=f"amt{p['id']}")
                method = fc[1].selectbox("Method", ["UPI", "Bank transfer", "Cash"], key=f"m{p['id']}")
                ref = fc[2].text_input("Transaction reference", key=f"r{p['id']}")
                if st.form_submit_button("Record payout"):
                    if amount <= 0:
                        st.error("Enter an amount.")
                    elif len(ref.strip()) < 4:
                        st.error("Enter the transaction reference — this is the audit trail.")
                    else:
                        execute(
                            "INSERT INTO payouts (provider_id, amount, orders_count, method, ref, created_at) "
                            "VALUES (?,?,?,?,?,?)",
                            (p["id"], round(amount, 2), cnt, method, ref.strip(), datetime.utcnow().isoformat()),
                        )
                        notify_provider(p["id"], "payout",
                                        f"{inr(amount)} has been sent to you for {cnt} delivered order(s). "
                                        f"{method} reference {ref.strip()}.")
                        st.success(f"Recorded {inr(amount)} to {p['display_name']}.")
                        st.rerun()

    st.markdown("#### Payout history")
    hist = q("SELECT po.created_at, p.display_name provider, po.amount, po.orders_count, po.method, po.ref "
             "FROM payouts po JOIN providers p ON p.id=po.provider_id ORDER BY po.id DESC")
    if hist:
        st.dataframe(df(hist), use_container_width=True, hide_index=True)
    else:
        st.caption("No payouts recorded yet.")

def admin_price_policy():
    st.markdown("<div class='page-title'>Price Guidance</div>"
                "<div class='page-sub'>The team's reasonable-price policy, written once here instead of "
                "explained on every call.</div>", unsafe_allow_html=True)
    st.caption(
        "Set the range a category should normally sit in. Sellers see it while pricing, and get a warning "
        "when they go outside it — or below their own cost. Nothing is blocked; this is guidance, not a cage."
    )
    kind = st.selectbox("Category group", KINDS)
    with st.form(f"bands{kind}"):
        rows = {}
        for cat in CATEGORIES[kind]:
            b = price_band(cat) or {}
            c = st.columns([2, 1, 1, 2])
            c[0].markdown(f"**{cat}**")
            lo = c[1].number_input("Min ₹", 0.0, 100000.0, float(b.get("min_price") or 0), step=5.0,
                                   key=f"lo{cat}", label_visibility="collapsed")
            hi = c[2].number_input("Max ₹", 0.0, 100000.0, float(b.get("max_price") or 0), step=5.0,
                                   key=f"hi{cat}", label_visibility="collapsed")
            note = c[3].text_input("Note", b.get("note") or "", key=f"nt{cat}", label_visibility="collapsed",
                                   placeholder="optional note to the seller")
            rows[cat] = (lo, hi, note)
        if st.form_submit_button("Save guidance", use_container_width=True):
            bad = [c for c, (lo, hi, _) in rows.items() if lo and hi and lo > hi]
            if bad:
                st.error(f"Minimum is above maximum for: {', '.join(bad)}")
            else:
                for cat, (lo, hi, note) in rows.items():
                    if lo or hi or note:
                        execute(
                            "INSERT INTO price_bands (category, kind, min_price, max_price, note, updated_at) "
                            "VALUES (?,?,?,?,?,?) ON CONFLICT(category) DO UPDATE SET "
                            "kind=excluded.kind, min_price=excluded.min_price, max_price=excluded.max_price, "
                            "note=excluded.note, updated_at=excluded.updated_at",
                            (cat, kind, lo or None, hi or None, note.strip() or None,
                             datetime.utcnow().isoformat()),
                        )
                    else:
                        execute("DELETE FROM price_bands WHERE category=?", (cat,))
                st.success("Guidance saved.")
                st.rerun()

    st.markdown("#### Listings priced outside guidance")
    rows = q(
        "SELECT l.title, l.category, l.price, l.cost_price, p.display_name, b.min_price, b.max_price "
        "FROM listings l JOIN providers p ON p.id=l.provider_id "
        "JOIN price_bands b ON b.category=l.category "
        "WHERE l.active=1 AND ((b.min_price IS NOT NULL AND l.price < b.min_price) "
        "OR (b.max_price IS NOT NULL AND l.price > b.max_price)) ORDER BY p.display_name"
    )
    if rows:
        st.dataframe(df(rows), use_container_width=True, hide_index=True)
        st.caption("Worth a call — either the price is wrong, or the guidance is.")
    else:
        st.caption("Every live listing sits inside its range.")

    below = q("SELECT l.title, l.price, l.cost_price, p.display_name FROM listings l "
              "JOIN providers p ON p.id=l.provider_id WHERE l.active=1 AND l.cost_price IS NOT NULL "
              "AND l.price < l.cost_price")
    if below:
        st.markdown("#### ⚠️ Selling below cost")
        st.dataframe(df(below), use_container_width=True, hide_index=True)
        st.caption("Call these women today. A seller losing money on every order quits within weeks.")

def admin_outbox():
    st.markdown("<div class='page-title'>WhatsApp Outbox</div>"
                "<div class='page-sub'>Every message the app wants to send. One tap each — no Meta account needed.</div>",
                unsafe_allow_html=True)
    tok, phone_id = wa_credentials()
    if tok and phone_id:
        st.success("Cloud API credentials found — messages send automatically. Anything that failed stays here.")
    else:
        st.info("No Cloud API credentials set (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`). Messages queue here and "
                "you send them with one tap, which is exactly how a 25-kitchen pilot should run.")

    queued = q("SELECT * FROM notifications WHERE status='Queued' ORDER BY id")
    n_sent = q("SELECT COUNT(*) c FROM notifications WHERE status='Sent'")[0]["c"]
    n_all = q("SELECT COUNT(*) c FROM notifications")[0]["c"]
    c = st.columns(3)
    kpi(c[0], "Waiting to send", f"{len(queued)}")
    kpi(c[1], "Sent", f"{n_sent}")
    kpi(c[2], "Total messages", f"{n_all}")
    st.write("")

    if not queued:
        st.success("Nothing waiting.")
    for r in queued:
        n = dict(r)
        with st.container(border=True):
            st.markdown(
                f"**{n['to_name'] or 'Unknown'}** · {n['to_role']} · {n['to_phone'] or 'no phone'} "
                f"<span class='pill'>{n['kind']}</span><br><span class='muted'>{n['body']}</span>"
                + (f"<br><span class='muted'>Last error: {n['error']}</span>" if n["error"] else ""),
                unsafe_allow_html=True,
            )
            b = st.columns([1, 1, 2])
            link = wa_link(n["to_phone"], n["body"])
            if link:
                b[0].link_button("Open WhatsApp", link, use_container_width=True)
            else:
                b[0].caption("No valid phone number on file.")
            if b[1].button("Mark sent", key=f"ms{n['id']}", use_container_width=True):
                execute("UPDATE notifications SET status='Sent', sent_at=? WHERE id=?",
                        (datetime.utcnow().isoformat(), n["id"]))
                st.rerun()
            if tok and phone_id and b[2].button("Retry via API", key=f"rt{n['id']}"):
                ok, err = send_whatsapp(n["to_phone"], n["body"])
                execute("UPDATE notifications SET status=?, error=?, sent_at=? WHERE id=?",
                        ("Sent" if ok else "Queued", err, datetime.utcnow().isoformat() if ok else None, n["id"]))
                st.rerun()

    st.markdown("#### Message history")
    hist = q("SELECT created_at, to_name, to_role, kind, status, body FROM notifications ORDER BY id DESC LIMIT 200")
    if hist:
        st.dataframe(df(hist), use_container_width=True, hide_index=True)
    else:
        st.caption("Nothing yet.")

def admin_settings():
    st.markdown("<div class='page-title'>Fees & Settings</div>"
                "<div class='page-sub'>Change these and every price breakdown in the app updates.</div>",
                unsafe_allow_html=True)
    with st.form("settings"):
        upi = st.text_input("Platform UPI ID — customers pay into this", setting("platform_upi", str),
                            placeholder="gharse@okaxis")
        c = st.columns(3)
        plat = c[0].number_input("Platform fee (%)", 0.0, 30.0, setting("platform_pct", float), step=0.5,
                                 help="Onboarding, verification, the app itself.")
        ops = c[1].number_input("Processing & support (%)", 0.0, 30.0, setting("ops_pct", float), step=0.5,
                                help="Payment gateway charges, support, the team's coordination.")
        mino = c[2].number_input("Minimum order value (₹)", 0.0, 500.0, setting("min_order", float), step=10.0)
        st.caption(f"Total commission **{plat + ops:.1f}%** — on a ₹100 item the cook keeps "
                   f"{inr(100 - plat - ops)}, you keep {inr(plat + ops)} before delivery costs.")
        c = st.columns(3)
        dfee = c[0].number_input("Home delivery fee (₹)", 0.0, 100.0, setting("delivery_fee", float), step=1.0)
        pfee = c[1].number_input("Community pickup point fee (₹)", 0.0, 100.0,
                                 setting("pickup_point_fee", float), step=1.0)
        sfee = c[2].number_input("Self pickup fee (₹)", 0.0, 100.0, setting("self_pickup_fee", float), step=1.0)
        if st.form_submit_button("Save settings", use_container_width=True):
            set_setting("platform_upi", upi.strip())
            set_setting("platform_pct", plat)
            set_setting("ops_pct", ops)
            set_setting("min_order", mino)
            set_setting("delivery_fee", dfee)
            set_setting("pickup_point_fee", pfee)
            set_setting("self_pickup_fee", sfee)
            st.success("Saved.")
            st.rerun()

    st.markdown("#### How money moves today")
    st.caption(
        "Customers pay into the platform UPI ID and enter the reference; you confirm it under **Orders**; "
        "the cook's share is released under **Payouts** once the order is delivered. That is a real, "
        "auditable escrow with no gateway account needed — which is how a 25-kitchen pilot should start. "
        "Swap in a payment gateway (Razorpay/Cashfree) for auto-reconciliation once volume makes manual "
        "confirmation the bottleneck; the split, escrow rule and payout ledger stay exactly as they are."
    )
    st.markdown("#### Why commission is capped at 10%")
    st.caption(
        "A home cook prices at roughly 1.6× her cost, so a ₹85 meal carries about ₹33 of margin. "
        "A 30% commission would take ₹25.50 of that — around 77% of her profit — and she would quit, "
        "correctly. At 10% she keeps ₹24.50 a meal, and the platform still earns on every order. "
        "Growth comes from subscriptions, corporate contracts and seller plans, not from a fatter cut "
        "of a ₹90 lunch."
    )
    st.markdown("#### Change admin password")
    with st.form("pw"):
        p1 = st.text_input("New password", type="password")
        p2 = st.text_input("Confirm", type="password")
        if st.form_submit_button("Update password"):
            if not p1 or p1 != p2:
                st.error("Passwords empty or do not match.")
            else:
                execute("UPDATE users SET pw_hash=? WHERE role='admin'", (hash_pw(p1),))
                st.success("Password updated.")

# ----------------------------------------------------------------------------- PROVIDER PAGES
def provider_profile(user):
    p = provider_of(user)
    st.markdown("<div class='page-title'>My Profile & Verification</div>"
                "<div class='page-sub'>Customers buy from people they trust — this page is that trust.</div>",
                unsafe_allow_html=True)

    a, b, c_ = st.columns(3)
    kpi(a, "Verification", "Verified ✓" if p.get("verified") else "Pending")
    rating, cnt = provider_rating(p["id"])
    kpi(b, "Rating", f"{rating} ★ ({cnt})" if rating else "—")
    kpi(c_, "Repeat customers", f"{repeat_customers(p['id'])}")
    st.write("")

    pc = st.columns([1, 2])
    with pc[0]:
        if p.get("photo"):
            st.image(bytes(p["photo"]), use_container_width=True)
        else:
            st.caption("No photo yet — customers scroll past listings without one.")
    with pc[1]:
        shot = st.file_uploader("Your kitchen / work photo", type=["jpg", "jpeg", "png", "webp"],
                                key="provphoto")
        if shot is not None and st.button("Save photo"):
            if shot.size > MAX_IMG_MB * 1024 * 1024:
                st.error(f"Please keep the photo under {MAX_IMG_MB} MB.")
            else:
                execute("UPDATE providers SET photo=? WHERE id=?", (process_image(shot), p["id"]))
                st.success("Photo saved.")
                st.rerun()

    with st.form("prof"):
        c = st.columns(2)
        display_name = c[0].text_input("Kitchen / business name", p.get("display_name") or "")
        owner_name = c[1].text_input("Your name", p.get("owner_name") or "")
        c = st.columns(3)
        phone = c[0].text_input("Phone", p.get("phone") or "")
        email = c[1].text_input("Email", p.get("email") or "")
        pincode = c[2].text_input("Pincode", p.get("pincode") or "")
        c = st.columns(2)
        area = c[0].selectbox("Area", AREAS, index=idx(AREAS, p.get("area")))
        radius = c[1].slider("How far will you serve? (km)", 1.0, 8.0, float(p.get("radius_km") or 3), 0.5)
        kinds = st.multiselect("What do you offer?", KINDS, default=csv_split(p.get("kinds")) or ["Food"])
        cat_pool = [c2 for k in kinds for c2 in CATEGORIES[k]]
        cats = st.multiselect("Categories", cat_pool,
                              default=[c2 for c2 in csv_split(p.get("categories")) if c2 in cat_pool])
        c = st.columns(2)
        cuis = c[0].multiselect("Cuisines (food only)", CUISINES, default=csv_split(p.get("cuisines")))
        langs = c[1].multiselect("Languages you speak", LANGUAGES, default=csv_split(p.get("languages")))
        diet = st.selectbox("Kitchen type", DIETS, index=idx(DIETS, p.get("diet")))
        bio = st.text_area("Tell customers about your cooking / craft", p.get("bio") or "", height=90)
        c = st.columns(2)
        fssai = c[0].text_input("FSSAI registration number (required to sell food)", p.get("fssai_no") or "")
        upi = c[1].text_input("Your UPI ID — this is where your earnings are sent", p.get("upi_id") or "",
                              placeholder="name@okhdfcbank")
        if st.form_submit_button("Save profile", use_container_width=True):
            execute(
                "UPDATE providers SET display_name=?, owner_name=?, phone=?, email=?, pincode=?, area=?, "
                "radius_km=?, kinds=?, categories=?, cuisines=?, languages=?, diet=?, bio=?, fssai_no=?, "
                "upi_id=? WHERE id=?",
                (display_name, owner_name, phone, email, pincode, area, radius, csv_join(kinds), csv_join(cats),
                 csv_join(cuis), csv_join(langs), diet, bio, fssai.strip(), upi.strip(), p["id"]),
            )
            st.success("Profile saved.")
            st.rerun()

    st.markdown("#### Verification checklist")
    st.markdown(
        f"- KYC: {'✅ done' if p.get('kyc_done') else '⬜ pending with admin'}\n"
        f"- Hygiene training: {'✅ done' if p.get('hygiene_done') else '⬜ pending with admin'}\n"
        f"- FSSAI number: {'✅ on file' if (p.get('fssai_no') or '').strip() else '⬜ not provided'}\n"
        f"- Admin verification: {'✅ verified' if p.get('verified') else '⬜ pending'}"
    )
    st.caption(
        "Food businesses in India need FSSAI registration or a licence. If you do not have one yet, "
        "add your phone number above and the team will walk you through the application — that help is part of the service."
    )

def provider_listings(user):
    p = provider_of(user)
    st.markdown("<div class='page-title'>My Listings</div>"
                "<div class='page-sub'>Put up what you can make today. Set the quantity you can actually handle.</div>",
                unsafe_allow_html=True)

    food_ok = can_sell_food(p)
    if not food_ok:
        st.warning("Food listings stay unpublished until admin verifies you **and** an FSSAI number is on file. "
                   "Products and services can be listed right away.")

    with st.expander("➕ Add a listing", expanded=False):
        # Outside a form, so the price check below reacts as she types.
        kind = st.selectbox("Type", KINDS, key="nl_kind")
        c = st.columns(2)
        category = c[0].selectbox("Category", CATEGORIES[kind], key="nl_cat")
        band = price_band(category)
        if band and (band["min_price"] or band["max_price"]):
            c[1].caption(f"💡 Usual range here: **{inr(band['min_price'])}–{inr(band['max_price'])}**"
                         + (f" · {band['note']}" if band["note"] else ""))
        c = st.columns(3)
        price = c[0].number_input("Your price (₹)", 0.0, 100000.0, 0.0, step=5.0, key="nl_price")
        cost = c[1].number_input("What it costs you to make (₹)", 0.0, 100000.0, 0.0, step=5.0, key="nl_cost",
                                 help="Ingredients + gas + packaging. Only you and the team see this.")
        market = c[2].number_input("Typical shop/restaurant price (₹)", 0.0, 100000.0, 0.0, step=5.0, key="nl_mkt")
        if price > 0:
            level, msg = price_verdict(category, price, cost or None)
            if level == "bad":
                st.error(msg)
            elif level == "warn":
                st.warning(msg)
            elif level == "ok":
                st.success(msg)
            if cost and price > cost:
                st.caption(f"You keep {inr(split_money(price, 'Self pickup')['provider_payout'] - cost)} per unit "
                           f"after our 10% and your cost.")
        with st.form("newlisting", clear_on_submit=True):
            title = st.text_input("Title (e.g. Chicken Dum Biryani)")
            desc = st.text_area("What's in it?", height=80)
            unit = st.selectbox("Unit", UNITS)
            c = st.columns(3)
            avail = c[0].date_input("Available on", date.today())
            slot = c[1].selectbox("Slot", SLOTS)
            capacity = c[2].number_input("How many can you make?", 1, 500, 10)
            c = st.columns(2)
            cuisine = c[0].selectbox("Cuisine", ["—"] + CUISINES)
            diet = c[1].selectbox("Veg / Non-veg", DIETS)
            shot = st.file_uploader("Photo of the dish / item", type=["jpg", "jpeg", "png", "webp"])
            if st.form_submit_button("Publish", use_container_width=True):
                if not title or price <= 0:
                    st.error("Title and a price above zero are required.")
                elif shot is not None and shot.size > MAX_IMG_MB * 1024 * 1024:
                    st.error(f"Please keep the photo under {MAX_IMG_MB} MB.")
                else:
                    active = 1 if (kind != "Food" or food_ok) else 0
                    execute(
                        "INSERT INTO listings (provider_id, kind, category, title, description, cuisine, diet, "
                        "price, unit, cost_price, market_price, avail_date, slot, capacity, sold, active, photo, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                        (p["id"], kind, category, title, desc, None if cuisine == "—" else cuisine, diet,
                         price, unit, cost or None, market or None, avail.isoformat(), slot, int(capacity),
                         active, process_image(shot), datetime.utcnow().isoformat()),
                    )
                    st.success("Listing added." if active else "Saved, but held unpublished until verification.")
                    st.rerun()

    rows = q("SELECT * FROM listings WHERE provider_id=? ORDER BY avail_date DESC, id DESC", (p["id"],))
    if not rows:
        st.info("Nothing listed yet.")
        return
    for r in rows:
        l = dict(r)
        left = max(0, (l["capacity"] or 0) - (l["sold"] or 0))
        with st.container(border=True):
            c = st.columns([1, 3, 1, 1])
            if l["photo"]:
                c[0].image(bytes(l["photo"]), use_container_width=True)
            else:
                c[0].caption("No photo")
            c = [c[1], c[2], c[3]]
            state = "<span class='pill ok'>Live</span>" if l["active"] else "<span class='pill warn'>Unpublished</span>"
            c[0].markdown(
                f"**{l['title']}** {state}<br><span class='muted'>{l['kind']} · {l['category']} · "
                f"{l['avail_date']} · {l['slot']}</span>",
                unsafe_allow_html=True,
            )
            c[1].markdown(f"<div class='price'>{inr(l['price'])}</div><span class='muted'>{l['unit']}</span>",
                          unsafe_allow_html=True)
            c[2].markdown(f"**{left}** left<br><span class='muted'>of {l['capacity']}</span>", unsafe_allow_html=True)
            b = st.columns(3)
            if b[0].button("Toggle live", key=f"tg{l['id']}", use_container_width=True):
                if l["kind"] == "Food" and not food_ok and not l["active"]:
                    st.error("Food cannot go live before verification + FSSAI.")
                else:
                    execute("UPDATE listings SET active=? WHERE id=?", (0 if l["active"] else 1, l["id"]))
                    st.rerun()
            newcap = b[1].number_input("Capacity", 1, 500, int(l["capacity"] or 1), key=f"cap{l['id']}")
            if b[2].button("Update capacity", key=f"uc{l['id']}", use_container_width=True):
                execute("UPDATE listings SET capacity=? WHERE id=?", (int(newcap), l["id"]))
                st.rerun()
            with st.expander("Change photo"):
                shot = st.file_uploader("New photo", type=["jpg", "jpeg", "png", "webp"], key=f"ph{l['id']}")
                if shot is not None and st.button("Save photo", key=f"sp{l['id']}"):
                    if shot.size > MAX_IMG_MB * 1024 * 1024:
                        st.error(f"Please keep the photo under {MAX_IMG_MB} MB.")
                    else:
                        execute("UPDATE listings SET photo=? WHERE id=?", (process_image(shot), l["id"]))
                        st.rerun()

def provider_orders(user):
    p = provider_of(user)
    st.markdown("<div class='page-title'>Orders Received</div>"
                "<div class='page-sub'>Move each order along as you cook, pack and hand over.</div>",
                unsafe_allow_html=True)
    rows = q(
        "SELECT o.*, l.title, c.full_name, c.phone, c.pickup_point FROM orders o "
        "JOIN listings l ON l.id=o.listing_id JOIN customers c ON c.id=o.customer_id "
        "WHERE o.provider_id=? ORDER BY o.id DESC", (p["id"],)
    )
    if not rows:
        st.info("No orders yet.")
        return
    for r in rows:
        o = dict(r)
        with st.container(border=True):
            c = st.columns([3, 1, 1])
            c[0].markdown(
                f"**#{o['id']} · {o['title']} × {o['qty']}**<br>"
                f"<span class='muted'>{o['for_date']} · {o['slot']} · {o['delivery_mode']}"
                f"{' · ' + o['pickup_point'] if o['delivery_mode'] == 'Community pickup point' and o['pickup_point'] else ''}</span><br>"
                f"<span class='muted'>{o['full_name']} · {o['phone'] if o['status'] not in ('Placed', 'Cancelled') else mask_phone(o['phone'])}</span>"
                + (f"<br><span class='muted'>Note: {o['note']}</span>" if o["note"] else ""),
                unsafe_allow_html=True,
            )
            c[1].markdown(f"<div class='price'>{inr(o['provider_payout'])}</div><span class='muted'>you get</span>",
                          unsafe_allow_html=True)
            c[2].markdown(f"<span class='pill'>{o['status']}</span> {pay_pill(o['payment_status'])}",
                          unsafe_allow_html=True)
            if (o["payment_status"] or "Unpaid") == "Unpaid" and o["status"] == "Placed":
                st.caption("Not paid for yet — wait for payment before you start cooking.")
            if o["status"] not in ("Delivered", "Cancelled"):
                nxt = ORDER_FLOW[min(ORDER_FLOW.index(o["status"]) + 1, len(ORDER_FLOW) - 1)]
                b = st.columns([1, 1, 3])
                if b[0].button(f"Mark {nxt}", key=f"adv{o['id']}", use_container_width=True):
                    execute("UPDATE orders SET status=? WHERE id=?", (nxt, o["id"]))
                    line = {
                        "Accepted": "has been accepted by the kitchen",
                        "Preparing": "is being cooked now",
                        "Ready": "is ready",
                        "Out for delivery": "is on its way",
                        "Delivered": "has been delivered — enjoy, and do rate it in the app",
                    }.get(nxt, f"is now {nxt}")
                    notify_customer(o["customer_id"], "status",
                                    f"Your GharSe order #{o['id']} ({o['title']}) {line}.", o["id"])
                    st.rerun()
                if b[1].button("Cancel", key=f"can{o['id']}", use_container_width=True):
                    cancel_order(o)
                    notify_customer(o["customer_id"], "cancelled",
                                    f"Sorry — order #{o['id']} ({o['title']}) was cancelled by the kitchen."
                                    + (" Your refund is being processed."
                                       if (o["payment_status"] or "") in ("Claimed", "Paid") else ""), o["id"])
                    st.rerun()
            if o["rating"]:
                st.caption(f"⭐ {o['rating']}/5 — {o['review'] or 'no comment'}")

def provider_plans(user):
    p = provider_of(user)
    st.markdown("<div class='page-title'>Meal Plans & Subscribers</div>"
                "<div class='page-sub'>Predictable demand beats one-off orders — this is where steady income comes from.</div>",
                unsafe_allow_html=True)

    with st.expander("➕ Create a plan", expanded=False):
        with st.form("newplan", clear_on_submit=True):
            title = st.text_input("Plan name (e.g. PG Lunch Plan)")
            desc = st.text_area("What's included?", height=70)
            c = st.columns(3)
            slot = c[0].selectbox("Slot", SLOTS)
            days = c[1].selectbox("Days", PLAN_PERIODS, index=2)
            price = c[2].number_input("Total price (₹)", 0.0, 100000.0, 0.0, step=50.0)
            diet = st.selectbox("Diet", DIETS)
            if price and days:
                st.caption(f"Works out to {inr(price / days)} per meal for the customer.")
            if st.form_submit_button("Publish plan", use_container_width=True):
                if not title or price <= 0:
                    st.error("Name and price are required.")
                elif not can_sell_food(p):
                    st.error("Meal plans need verification + FSSAI first.")
                else:
                    execute(
                        "INSERT INTO plans (provider_id, title, description, slot, days, price, diet, active, created_at) "
                        "VALUES (?,?,?,?,?,?,?,1,?)",
                        (p["id"], title, desc, slot, int(days), price, diet, datetime.utcnow().isoformat()),
                    )
                    st.success("Plan published.")
                    st.rerun()

    plans = q("SELECT * FROM plans WHERE provider_id=? ORDER BY id DESC", (p["id"],))
    for r in plans:
        pl = dict(r)
        subs = q("SELECT COUNT(*) c FROM subscriptions WHERE plan_id=? AND status='Active'", (pl["id"],))[0]["c"]
        with st.container(border=True):
            c = st.columns([3, 1, 1])
            c[0].markdown(f"**{pl['title']}** · {pl['slot']}<br><span class='muted'>{pl['description'] or ''}</span>",
                          unsafe_allow_html=True)
            c[1].markdown(f"<div class='price'>{inr(pl['price'])}</div>"
                          f"<span class='muted'>{pl['days']} days · {inr(pl['price'] / pl['days'])}/meal</span>",
                          unsafe_allow_html=True)
            c[2].markdown(f"**{subs}** subscribers", unsafe_allow_html=True)
            if st.button("Toggle active", key=f"pl{pl['id']}"):
                execute("UPDATE plans SET active=? WHERE id=?", (0 if pl["active"] else 1, pl["id"]))
                st.rerun()

    st.markdown("#### Today's subscriber count")
    rows = q(
        "SELECT pl.slot slot, COUNT(*) c FROM subscriptions s JOIN plans pl ON pl.id=s.plan_id "
        "WHERE s.provider_id=? AND s.status='Active' AND s.paused=0 GROUP BY pl.slot",
        (p["id"],),
    )
    if rows:
        for r in rows:
            st.markdown(f"- **{r['c']}** meals to cook for *{r['slot']}*")
    else:
        st.caption("No active subscribers yet.")

def provider_requests(user):
    p = provider_of(user)
    st.markdown("<div class='page-title'>Open Requests</div>"
                "<div class='page-sub'>Customers posting what they need. Send your price — they pick.</div>",
                unsafe_allow_html=True)
    rows = q("SELECT r.*, c.full_name FROM requests r JOIN customers c ON c.id=r.customer_id "
             "WHERE r.status='Open' ORDER BY r.id DESC")
    if not rows:
        st.info("No open requests right now.")
        return
    for r in rows:
        req = dict(r)
        mine = q("SELECT * FROM bids WHERE request_id=? AND provider_id=?", (req["id"], p["id"]))
        with st.container(border=True):
            st.markdown(
                f"**{req['title']}** <span class='pill'>{req['category']}</span><br>"
                f"<span class='muted'>{req['area']} · needed by {req['needed_by']} · "
                f"budget {inr(req['budget']) if req['budget'] else 'open'}</span><br>{req['description'] or ''}",
                unsafe_allow_html=True,
            )
            if mine:
                b = dict(mine[0])
                st.markdown(f"<span class='pill ok'>Your offer: {inr(b['price'])} · {b['status']}</span>",
                            unsafe_allow_html=True)
            else:
                with st.form(f"bid{req['id']}"):
                    c = st.columns([1, 1, 2])
                    price = c[0].number_input("Your price (₹)", 0.0, 100000.0, float(req["budget"] or 0), step=25.0)
                    eta = c[1].text_input("Ready by", req["needed_by"] or "")
                    note = c[2].text_input("Message to the customer")
                    if st.form_submit_button("Send offer"):
                        if price <= 0:
                            st.error("Enter a price.")
                        else:
                            execute(
                                "INSERT INTO bids (request_id, provider_id, price, eta, note, status, created_at) "
                                "VALUES (?,?,?,?,?, 'Offered', ?)",
                                (req["id"], p["id"], price, eta, note, datetime.utcnow().isoformat()),
                            )
                            notify_customer(req["customer_id"], "new_bid",
                                            f"{p['display_name']} has offered {inr(price)} for your request "
                                            f"\"{req['title']}\". Open GharSe to accept or compare offers.")
                            st.success("Offer sent.")
                            st.rerun()

def provider_earnings(user):
    p = provider_of(user)
    st.markdown("<div class='page-title'>My Earnings</div>"
                "<div class='page-sub'>What you earned, and exactly what the platform kept.</div>",
                unsafe_allow_html=True)
    delivered = q("SELECT COALESCE(SUM(provider_payout),0) s, COUNT(*) c FROM orders "
                  "WHERE provider_id=? AND status='Delivered'", (p["id"],))[0]
    pending = q("SELECT COALESCE(SUM(provider_payout),0) s FROM orders "
                "WHERE provider_id=? AND status NOT IN ('Delivered','Cancelled')", (p["id"],))[0]["s"]
    fees = q("SELECT COALESCE(SUM(platform_fee),0) + COALESCE(SUM(ops_fee),0) s FROM orders "
             "WHERE provider_id=? AND status='Delivered'", (p["id"],))[0]["s"]
    paid_out = q("SELECT COALESCE(SUM(amount),0) s FROM payouts WHERE provider_id=?", (p["id"],))[0]["s"]
    due, due_orders = payout_due(p["id"])
    c = st.columns(4)
    kpi(c[0], "Earned (delivered)", inr(delivered["s"]))
    kpi(c[1], "Paid out to you", inr(paid_out))
    kpi(c[2], "Awaiting payout", inr(due))
    kpi(c[3], "In progress", inr(pending))
    st.write("")
    st.caption(
        f"Payout covers {due_orders} delivered order(s) that the customer has actually paid for — "
        f"money is held until delivery. Platform fee so far: {inr(fees)}."
    )
    if not (p.get("upi_id") or "").strip():
        st.warning("Add your UPI ID under **My Profile & Verification** — payouts can't be sent without it.")

    payouts = q("SELECT * FROM payouts WHERE provider_id=? ORDER BY id DESC", (p["id"],))
    if payouts:
        st.markdown("#### Payouts received")
        st.dataframe(df(payouts)[["created_at", "amount", "orders_count", "method", "ref"]],
                     use_container_width=True, hide_index=True)

    st.markdown("#### Every order")
    rows = q(
        "SELECT o.created_at, l.title, o.qty, o.customer_total, o.platform_fee, o.provider_payout, "
        "o.status, o.payment_status FROM orders o JOIN listings l ON l.id=o.listing_id "
        "WHERE o.provider_id=? ORDER BY o.id DESC", (p["id"],)
    )
    if rows:
        st.dataframe(df(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No orders yet.")

# ----------------------------------------------------------------------------- CUSTOMER PAGES
def customer_discover(user):
    cust = customer_of(user)
    st.markdown("<div class='page-title'>Discover Near Me</div>"
                "<div class='page-sub'>Home-made, from women in your neighbourhood.</div>", unsafe_allow_html=True)

    c = st.columns(4)
    kind = c[0].selectbox("What are you looking for?", ["Everything"] + KINDS)
    cat_pool = ["All"] + (CATEGORIES[kind] if kind in CATEGORIES else
                          [x for k in KINDS for x in CATEGORIES[k]])
    category = c[1].selectbox("Category", cat_pool)
    area = c[2].selectbox("Area", ["All"] + AREAS, index=idx(["All"] + AREAS, cust.get("area")))
    diet = c[3].selectbox("Diet", ["Any"] + DIETS, index=idx(["Any"] + DIETS, cust.get("diet")))
    only_today = st.checkbox("Only what's available today", value=True)

    sql = (
        "SELECT l.*, p.display_name, p.area, p.verified, p.cuisines, p.owner_name, p.photo pphoto "
        "FROM listings l JOIN providers p ON p.id=l.provider_id WHERE l.active=1 AND l.sold < l.capacity"
    )
    params = []
    if kind != "Everything":
        sql += " AND l.kind=?"
        params.append(kind)
    if category != "All":
        sql += " AND l.category=?"
        params.append(category)
    if area != "All":
        sql += " AND p.area=?"
        params.append(area)
    if diet != "Any":
        sql += " AND l.diet=?"
        params.append(diet)
    if only_today:
        sql += " AND l.avail_date>=?"
        params.append(today_iso())
    sql += " ORDER BY l.avail_date ASC, l.id DESC"
    rows = q(sql, tuple(params))

    if not rows:
        st.info("Nothing matches yet. Try widening the filters — or post a custom request and let cooks come to you.")
        return

    min_order = setting("min_order", float)
    for r in rows:
        l = dict(r)
        left = (l["capacity"] or 0) - (l["sold"] or 0)
        rating, cnt = provider_rating(l["provider_id"])
        with st.container(border=True):
            shot = photo_of(l, l.get("pphoto"))
            cols = st.columns([1.1, 3, 1.2]) if shot else st.columns([3, 1.2])
            if shot:
                cols[0].image(shot, use_container_width=True)
                cols = cols[1:]
            c = cols
            badges = "<span class='pill ok'>Verified ✓</span>" if l["verified"] else ""
            if rating:
                badges += f"<span class='pill'>⭐ {rating} ({cnt})</span>"
            c[0].markdown(
                f"**{l['title']}** {badges}<br>"
                f"<span class='muted'>{l['display_name']} · {l['area']} · {l['slot']} · {l['avail_date']}</span><br>"
                f"{l['description'] or ''}<br>"
                f"<span class='pill'>{l['category']}</span>"
                + (f"<span class='pill'>{l['cuisine']}</span>" if l["cuisine"] else "")
                + (f"<span class='pill'>{l['diet']}</span>" if l["diet"] else "")
                + f"<span class='pill'>{left} left</span>",
                unsafe_allow_html=True,
            )
            save_html = ""
            if l["market_price"] and l["market_price"] > l["price"]:
                pct = (l["market_price"] - l["price"]) / l["market_price"] * 100
                save_html = (f"<div class='save'>Saves {inr(l['market_price'] - l['price'])} ({pct:.0f}%)<br>"
                             f"<span class='muted'>typical outside: {inr(l['market_price'])}</span></div>")
            c[1].markdown(
                f"<div class='price'>{inr(l['price'])}</div><span class='muted'>{l['unit']}</span>{save_html}",
                unsafe_allow_html=True,
            )
            # Deliberately not a st.form — widgets outside a form rerun on change, so the
            # money split below updates live as quantity or delivery mode changes.
            oc = st.columns([1, 1.6, 2])
            qty = oc[0].number_input("Qty", 1, int(max(1, left)), 1, key=f"qty{l['id']}")
            mode = oc[1].selectbox("Delivery", DELIVERY_MODES, key=f"dm{l['id']}")
            note = oc[2].text_input("Any instruction (less oil, no onion…)", key=f"nt{l['id']}")
            s = split_money(l["price"] * qty, mode)
            st.markdown(money_split_caption(s), unsafe_allow_html=True)
            if st.button(f"Order · pay {inr(s['customer_total'])}", key=f"ob{l['id']}"):
                if s["item_total"] < min_order:
                    st.error(f"Minimum order is {inr(min_order)}.")
                elif qty > left:
                    st.error("Not that many left.")
                else:
                    execute(
                        "INSERT INTO orders (listing_id, provider_id, customer_id, qty, item_total, delivery_fee, "
                        "platform_fee, ops_fee, provider_payout, customer_total, delivery_mode, note, status, "
                        "for_date, slot, payment_status, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'Placed', ?,?, 'Unpaid', ?)",
                        (l["id"], l["provider_id"], cust["id"], int(qty), s["item_total"], s["delivery_fee"],
                         s["platform_fee"], s["ops_fee"], s["provider_payout"], s["customer_total"], mode, note,
                         l["avail_date"], l["slot"], datetime.utcnow().isoformat()),
                    )
                    execute("UPDATE listings SET sold=sold+? WHERE id=?", (int(qty), l["id"]))
                    oid = q("SELECT MAX(id) m FROM orders")[0]["m"]
                    notify_provider(
                        l["provider_id"], "new_order",
                        f"New order #{oid} on GharSe: {qty} × {l['title']} for {l['avail_date']}, "
                        f"{l['slot']}. {mode}."
                        + (f" Note: {note}." if note else "")
                        + f" You'll get {inr(s['provider_payout'])}. We'll confirm once the customer has paid.",
                        oid,
                    )
                    notify_customer(
                        cust["id"], "order_placed",
                        f"Order #{oid} placed with {l['display_name']}: {qty} × {l['title']}. "
                        f"Pay {inr(s['customer_total'])} in the app to confirm it.", oid,
                    )
                    st.success("Order placed. Pay for it under My Orders — the kitchen starts cooking once it's paid.")
                    st.rerun()

    st.markdown("### Meal plans near you")
    plans = q(
        "SELECT pl.*, p.display_name, p.area, p.verified FROM plans pl JOIN providers p ON p.id=pl.provider_id "
        "WHERE pl.active=1" + (" AND p.area=?" if area != "All" else "") + " ORDER BY pl.id DESC",
        ((area,) if area != "All" else ()),
    )
    if not plans:
        st.caption("No plans published in this area yet.")
        return
    for r in plans:
        pl = dict(r)
        with st.container(border=True):
            c = st.columns([3, 1.2])
            c[0].markdown(
                f"**{pl['title']}** · {pl['display_name']} · {pl['area']}<br>"
                f"<span class='muted'>{pl['slot']} · {pl['days']} days · {pl['diet'] or ''}</span><br>{pl['description'] or ''}",
                unsafe_allow_html=True,
            )
            c[1].markdown(
                f"<div class='price'>{inr(pl['price'])}</div>"
                f"<span class='muted'>{inr(pl['price'] / pl['days'])} per meal</span>", unsafe_allow_html=True)
            with st.form(f"sub{pl['id']}"):
                sc = st.columns([1.4, 1.6])
                start = sc[0].date_input("Start from", date.today() + timedelta(days=1), key=f"sd{pl['id']}")
                mode = sc[1].selectbox("Delivery", DELIVERY_MODES, key=f"sm{pl['id']}")
                if st.form_submit_button("Subscribe"):
                    prefs = (f"Diet: {cust.get('diet') or '—'} · Spice: {cust.get('spice') or '—'} · "
                             f"Avoid: {cust.get('avoid') or '—'} · Budget: "
                             f"{inr(cust.get('budget_per_meal')) if cust.get('budget_per_meal') else '—'}/meal")
                    execute(
                        "INSERT INTO subscriptions (plan_id, provider_id, customer_id, start_date, days, price, "
                        "delivery_mode, prefs, paused, status, created_at) VALUES (?,?,?,?,?,?,?,?,0,'Active',?)",
                        (pl["id"], pl["provider_id"], cust["id"], start.isoformat(), pl["days"], pl["price"],
                         mode, prefs, datetime.utcnow().isoformat()),
                    )
                    notify_provider(pl["provider_id"], "new_subscription",
                                    f"New subscriber on GharSe: {cust.get('full_name') or 'a customer'} took "
                                    f"\"{pl['title']}\" ({pl['days']} days, {pl['slot']}) from {start.isoformat()}. "
                                    f"{mode}. Preferences — {prefs}.")
                    st.success("Subscribed. Your preferences were sent to the kitchen.")
                    st.rerun()

def customer_orders(user):
    cust = customer_of(user)
    st.markdown("<div class='page-title'>My Orders</div>"
                "<div class='page-sub'>Track, and rate once delivered.</div>", unsafe_allow_html=True)
    rows = q(
        "SELECT o.*, l.title, p.display_name, p.phone FROM orders o JOIN listings l ON l.id=o.listing_id "
        "JOIN providers p ON p.id=o.provider_id WHERE o.customer_id=? ORDER BY o.id DESC", (cust["id"],)
    )
    if not rows:
        st.info("No orders yet — head to Discover Near Me.")
        return
    spent = sum(dict(r)["customer_total"] for r in rows if dict(r)["status"] == "Delivered")
    to_women = sum(dict(r)["provider_payout"] for r in rows if dict(r)["status"] == "Delivered")
    c = st.columns(3)
    kpi(c[0], "Orders", f"{len(rows)}")
    kpi(c[1], "Spent", inr(spent))
    kpi(c[2], "Reached the cook", inr(to_women))
    st.write("")
    for r in rows:
        o = dict(r)
        with st.container(border=True):
            c = st.columns([3, 1, 1])
            c[0].markdown(
                f"**#{o['id']} · {o['title']} × {o['qty']}**<br>"
                f"<span class='muted'>{o['display_name']} · {o['for_date']} · {o['slot']} · {o['delivery_mode']}</span>",
                unsafe_allow_html=True,
            )
            c[1].markdown(f"<div class='price'>{inr(o['customer_total'])}</div>", unsafe_allow_html=True)
            c[2].markdown(f"<span class='pill'>{o['status']}</span> {pay_pill(o['payment_status'])}",
                          unsafe_allow_html=True)
            st.markdown(money_split_caption({
                "customer_total": o["customer_total"], "provider_payout": o["provider_payout"],
                "delivery_fee": o["delivery_fee"], "platform_fee": o["platform_fee"],
                "ops_fee": o["ops_fee"] or 0,
            }), unsafe_allow_html=True)

            if (o["payment_status"] or "Unpaid") == "Unpaid" and o["status"] != "Cancelled":
                link = upi_link(o["customer_total"], f"GharSe order {o['id']}")
                pa = (setting("platform_upi", str) or "").strip()
                if link:
                    st.link_button(f"Pay {inr(o['customer_total'])} by UPI", link, use_container_width=True)
                    st.caption(f"Or send it to **{pa}** from any UPI app, then enter the reference below.")
                else:
                    st.caption("Payment isn't switched on yet — admin needs to add the platform UPI ID "
                               "under Fees & Settings.")
                with st.form(f"pay{o['id']}"):
                    pc = st.columns([3, 1])
                    ref = pc[0].text_input("UPI reference / UTR number", key=f"utr{o['id']}")
                    if pc[1].form_submit_button("I've paid"):
                        if len(ref.strip()) < 6:
                            st.error("Enter the reference number your UPI app showed after payment.")
                        else:
                            execute("UPDATE orders SET payment_status='Claimed', payment_ref=?, paid_at=? "
                                    "WHERE id=?", (ref.strip(), datetime.utcnow().isoformat(), o["id"]))
                            st.success("Thanks — the team will confirm it shortly.")
                            st.rerun()
            elif o["payment_status"] == "Claimed":
                st.caption(f"Payment reference {o['payment_ref']} — awaiting confirmation.")
            elif o["payment_status"] == "Refund due":
                st.caption("Cancelled after payment — your refund is being processed.")
            if o["status"] == "Delivered" and not o["rating"]:
                with st.form(f"rate{o['id']}"):
                    rc = st.columns([1, 3, 1])
                    stars = rc[0].selectbox("Rating", [5, 4, 3, 2, 1], key=f"st{o['id']}")
                    review = rc[1].text_input("How was it?", key=f"rv{o['id']}")
                    if rc[2].form_submit_button("Submit"):
                        execute("UPDATE orders SET rating=?, review=? WHERE id=?", (int(stars), review, o["id"]))
                        st.rerun()
            if o["status"] == "Placed":
                if st.button("Cancel order", key=f"cc{o['id']}"):
                    cancel_order(o)
                    st.rerun()

def customer_subscriptions(user):
    cust = customer_of(user)
    st.markdown("<div class='page-title'>My Subscriptions</div>"
                "<div class='page-sub'>Pause when you travel. Resume when you're back.</div>", unsafe_allow_html=True)
    rows = q(
        "SELECT s.*, pl.title, pl.slot, p.display_name FROM subscriptions s JOIN plans pl ON pl.id=s.plan_id "
        "JOIN providers p ON p.id=s.provider_id WHERE s.customer_id=? ORDER BY s.id DESC", (cust["id"],)
    )
    if not rows:
        st.info("No subscriptions yet.")
        return
    for r in rows:
        s = dict(r)
        with st.container(border=True):
            c = st.columns([3, 1, 1])
            c[0].markdown(
                f"**{s['title']}** · {s['display_name']}<br>"
                f"<span class='muted'>{s['slot']} · from {s['start_date']} · {s['days']} days · {s['delivery_mode']}</span><br>"
                f"<span class='muted'>{s['prefs'] or ''}</span>",
                unsafe_allow_html=True,
            )
            c[1].markdown(f"<div class='price'>{inr(s['price'])}</div>"
                          f"<span class='muted'>{inr(s['price'] / s['days'])}/meal</span>", unsafe_allow_html=True)
            state = "Paused" if s["paused"] else s["status"]
            c[2].markdown(f"<span class='pill'>{state}</span>", unsafe_allow_html=True)
            b = st.columns(2)
            if s["status"] == "Active":
                if b[0].button("Resume" if s["paused"] else "Pause", key=f"pz{s['id']}", use_container_width=True):
                    execute("UPDATE subscriptions SET paused=? WHERE id=?", (0 if s["paused"] else 1, s["id"]))
                    st.rerun()
                if b[1].button("Cancel", key=f"sc{s['id']}", use_container_width=True):
                    execute("UPDATE subscriptions SET status='Cancelled' WHERE id=?", (s["id"],))
                    st.rerun()

def customer_requests(user):
    cust = customer_of(user)
    st.markdown("<div class='page-title'>Post a Request</div>"
                "<div class='page-sub'>Tell the neighbourhood what you need — cooks and craftswomen send offers.</div>",
                unsafe_allow_html=True)
    with st.expander("➕ New request", expanded=True):
        with st.form("newreq", clear_on_submit=True):
            kind = st.selectbox("Type", KINDS)
            c = st.columns(2)
            category = c[0].selectbox("Category", CATEGORIES[kind])
            title = c[1].text_input("What do you need? (e.g. 50 jasmine garlands)")
            desc = st.text_area("Details", height=80)
            c = st.columns(3)
            budget = c[0].number_input("Budget (₹, optional)", 0.0, 500000.0, 0.0, step=100.0)
            needed = c[1].date_input("Needed by", date.today() + timedelta(days=3))
            area = c[2].selectbox("Area", AREAS, index=idx(AREAS, cust.get("area")))
            if st.form_submit_button("Post request", use_container_width=True):
                if not title:
                    st.error("Give the request a title.")
                else:
                    execute(
                        "INSERT INTO requests (customer_id, kind, category, title, description, area, budget, "
                        "needed_by, status, created_at) VALUES (?,?,?,?,?,?,?,?, 'Open', ?)",
                        (cust["id"], kind, category, title, desc, area, budget or None, needed.isoformat(),
                         datetime.utcnow().isoformat()),
                    )
                    st.success("Posted. Offers will appear below.")
                    st.rerun()

    rows = q("SELECT * FROM requests WHERE customer_id=? ORDER BY id DESC", (cust["id"],))
    for r in rows:
        req = dict(r)
        with st.container(border=True):
            st.markdown(
                f"**{req['title']}** <span class='pill'>{req['status']}</span><br>"
                f"<span class='muted'>{req['category']} · {req['area']} · by {req['needed_by']} · "
                f"budget {inr(req['budget']) if req['budget'] else 'open'}</span>",
                unsafe_allow_html=True,
            )
            bids = q(
                "SELECT b.*, p.display_name, p.area, p.verified FROM bids b JOIN providers p ON p.id=b.provider_id "
                "WHERE b.request_id=? ORDER BY b.price ASC", (req["id"],)
            )
            if not bids:
                st.caption("No offers yet.")
            for br in bids:
                b = dict(br)
                bc = st.columns([3, 1, 1])
                badge = "<span class='pill ok'>Verified ✓</span>" if b["verified"] else ""
                bc[0].markdown(
                    f"{b['display_name']} · {b['area']} {badge}<br><span class='muted'>Ready by {b['eta'] or '—'} · "
                    f"{b['note'] or ''}</span>", unsafe_allow_html=True)
                bc[1].markdown(f"**{inr(b['price'])}**")
                if req["status"] == "Open":
                    if bc[2].button("Accept", key=f"ab{b['id']}", use_container_width=True):
                        execute("UPDATE bids SET status='Accepted' WHERE id=?", (b["id"],))
                        execute("UPDATE bids SET status='Declined' WHERE request_id=? AND id!=?", (req["id"], b["id"]))
                        execute("UPDATE requests SET status='Assigned' WHERE id=?", (req["id"],))
                        notify_provider(b["provider_id"], "bid_accepted",
                                        f"Your offer of {inr(b['price'])} for \"{req['title']}\" was accepted by "
                                        f"{cust.get('full_name') or 'a customer'} in {req['area']}. "
                                        f"Needed by {req['needed_by']}.")
                        st.rerun()
                else:
                    bc[2].markdown(f"<span class='pill'>{b['status']}</span>", unsafe_allow_html=True)
            if req["status"] == "Assigned":
                if st.button("Mark completed", key=f"rc{req['id']}"):
                    execute("UPDATE requests SET status='Completed' WHERE id=?", (req["id"],))
                    st.rerun()

def customer_prefs(user):
    cust = customer_of(user)
    st.markdown("<div class='page-title'>My Preferences</div>"
                "<div class='page-sub'>Set this once — every kitchen you subscribe to receives it.</div>",
                unsafe_allow_html=True)
    with st.form("prefs"):
        c = st.columns(2)
        name = c[0].text_input("Full name", cust.get("full_name") or "")
        phone = c[1].text_input("Phone", cust.get("phone") or "")
        c = st.columns(3)
        email = c[0].text_input("Email", cust.get("email") or "")
        area = c[1].selectbox("Area", AREAS, index=idx(AREAS, cust.get("area")))
        stay = c[2].selectbox("I stay in", ["PG / Hostel", "Shared flat", "Own home", "Office (day)", "Other"],
                              index=idx(["PG / Hostel", "Shared flat", "Own home", "Office (day)", "Other"],
                                        cust.get("stay_type")))
        pickup = st.text_input("Community pickup point (PG reception, office lobby, apartment gate…)",
                               cust.get("pickup_point") or "")
        c = st.columns(3)
        diet = c[0].selectbox("Diet", DIETS, index=idx(DIETS, cust.get("diet")))
        spice = c[1].selectbox("Spice", SPICE, index=idx(SPICE, cust.get("spice")))
        budget = c[2].number_input("Budget per meal (₹)", 0.0, 2000.0, float(cust.get("budget_per_meal") or 0),
                                   step=10.0)
        avoid = st.text_input("Avoid (allergies, ingredients)", cust.get("avoid") or "")
        if st.form_submit_button("Save preferences", use_container_width=True):
            execute(
                "UPDATE customers SET full_name=?, phone=?, email=?, area=?, stay_type=?, pickup_point=?, "
                "diet=?, spice=?, avoid=?, budget_per_meal=? WHERE id=?",
                (name, phone, email, area, stay, pickup, diet, spice, avoid, budget, cust["id"]),
            )
            st.success("Saved.")
            st.rerun()
    st.caption("Picking a community pickup point cuts your delivery fee — several orders to one PG or office "
               "travel together instead of one rider per meal.")

# ----------------------------------------------------------------------------- ROUTER
def dispatch(user, page):
    role = user["role"]
    if role == "admin":
        {
            "Dashboard": admin_dashboard, "Home Entrepreneurs": admin_providers, "Listings": admin_listings,
            "Price Guidance": admin_price_policy,
            "Orders": admin_orders, "Payouts": admin_payouts, "Subscriptions": admin_subscriptions,
            "Custom Requests": admin_requests, "WhatsApp Outbox": admin_outbox,
            "Fees & Settings": admin_settings,
        }[page]()
    elif role == "provider":
        {
            "My Profile & Verification": provider_profile, "My Listings": provider_listings,
            "Orders Received": provider_orders, "Meal Plans & Subscribers": provider_plans,
            "Open Requests": provider_requests, "My Earnings": provider_earnings,
        }[page](user)
    else:
        {
            "Discover Near Me": customer_discover, "My Orders": customer_orders,
            "My Subscriptions": customer_subscriptions, "Post a Request": customer_requests,
            "My Preferences": customer_prefs,
        }[page](user)

def main():
    init_db()
    inject_css()
    user = st.session_state.get("user")
    if not user:
        login_register_view()
        return
    page = sidebar_nav(user)
    dispatch(user, page)

if __name__ == "__main__":
    main()
