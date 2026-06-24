# app.py
# Mallu Articles Bengaluru — CA/CMA Articleship Connecting Portal
# Single-file Streamlit app. Roles: Admin (broker) | Firm (recruiter) | Candidate (student)
# Run locally:  pip install -r requirements.txt  &&  streamlit run app.py

import streamlit as st
import sqlite3
import hashlib
import re
from datetime import datetime
import pandas as pd

# ----------------------------------------------------------------------------- CONFIG
st.set_page_config(
    page_title="Mallu Articles Bengaluru | Articleship Portal",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = "mallu_articles.db"

ROLES = [
    "CA Article", "CMA Article", "Audit Assistant", "Paid Assistant",
    "Accounts Executive", "Articleship Trainee", "Semi-Qualified (CA/CMA)", "Other",
]
AREAS = [
    "Statutory Audit", "Internal Audit", "Direct Tax", "GST / Indirect Tax",
    "Accounting & Bookkeeping", "Company Law / ROC", "FP&A / Finance",
    "Audit & Assurance", "Bank Audit", "Other",
]
LOCATIONS = [
    "Jayanagar", "JP Nagar", "HSR Layout", "Koramangala", "BTM Layout",
    "Indiranagar", "Whitefield", "Marathahalli", "Electronic City", "Rajajinagar",
    "Malleshwaram", "Yelahanka", "Banashankari", "Bommanahalli", "CBD / MG Road",
    "Vijayanagar", "Other",
]
FIRM_TYPES = ["CA Firm", "CMA Firm", "CA & CMA Firm", "Consulting / Other"]
COURSES = ["CA", "CMA", "CA + CMA"]
LEVELS = ["Foundation", "Intermediate", "Final", "Qualified"]
APP_STATUSES = ["Applied", "Shortlisted", "Interview", "Selected", "Joined", "Rejected"]
BONUS_STATUSES = ["Pending", "Received"]

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
        CREATE TABLE IF NOT EXISTS firms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_name TEXT, firm_type TEXT, location_area TEXT, about TEXT,
            contact_name TEXT, contact_phone TEXT, contact_email TEXT, website TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT, course TEXT, level TEXT, role_seeking TEXT,
            areas_of_interest TEXT, location_pref TEXT, expected_stipend INTEGER,
            about TEXT, resume_link TEXT, resume_blob BLOB, resume_name TEXT,
            contact_phone TEXT, contact_email TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vacancies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_id INTEGER, role TEXT, num_vacancies INTEGER, area_of_work TEXT,
            jd TEXT, stipend_min INTEGER, stipend_max INTEGER, location_area TEXT,
            status TEXT DEFAULT 'Open', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER, vacancy_id INTEGER, status TEXT DEFAULT 'Applied',
            bonus_amount INTEGER DEFAULT 0, bonus_status TEXT DEFAULT 'Pending',
            note TEXT, created_at TEXT,
            UNIQUE(candidate_id, vacancy_id)
        );
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_blob BLOB, link TEXT, caption TEXT, active INTEGER DEFAULT 1,
            created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    # Seed the admin login only (required to bootstrap — NOT sample content).
    if not q("SELECT 1 FROM users WHERE role='admin'"):
        execute(
            "INSERT INTO users (username, pw_hash, role, created_at) VALUES (?,?,?,?)",
            ("admin", hash_pw("admin@123"), "admin", datetime.utcnow().isoformat()),
        )

# ----------------------------------------------------------------------------- HELPERS
def idx(lst, val, default=0):
    return lst.index(val) if val in lst else default

def mask_phone(p):
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    if len(d) < 4:
        return "•••• (via admin)"
    return d[:2] + "•" * (len(d) - 4) + d[-2:]

def mask_email(e):
    if not e or "@" not in e:
        return "•••• (via admin)"
    name, dom = e.split("@", 1)
    return (name[0] if name else "•") + "•••@" + dom

def inr(n):
    n = int(round(n or 0))
    s = str(abs(n))
    if len(s) > 3:
        last3, rest = s[-3:], s[:-3]
        rest = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", rest)
        s = rest + "," + last3
    return ("-" if n < 0 else "") + "₹" + s

def authenticate(u, p):
    rows = q("SELECT * FROM users WHERE username=?", (u,))
    if rows and rows[0]["pw_hash"] == hash_pw(p):
        return dict(rows[0])
    return None

def csv_join(items):
    return ", ".join(items) if items else ""

# ----------------------------------------------------------------------------- THEME / CSS
def inject_css():
    st.markdown(
        """
        <style>
        :root {
            --bg0:#070b18; --bg1:#0b1224; --panel:rgba(255,255,255,0.035);
            --line:rgba(45,140,255,0.22); --blue:#2b8fff; --blue2:#5fb0ff; --txt:#eaf1ff;
        }
        .stApp {
            background: radial-gradient(1200px 600px at 80% -10%, #122042 0%, transparent 55%),
                        linear-gradient(160deg, #070b18 0%, #0a1020 50%, #070b18 100%);
            color: var(--txt);
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0a1124 0%, #070b18 100%);
            border-right: 1px solid var(--line);
        }
        section[data-testid="stSidebar"] * { color: var(--txt); }
        h1,h2,h3,h4,h5,h6, p, span, label, div { color: var(--txt); }
        .block-container { padding-top: 1.4rem; }

        /* Inputs */
        .stTextInput input, .stNumberInput input, .stTextArea textarea,
        div[data-baseweb="select"] > div {
            background:#0c1430 !important; color:var(--txt) !important;
            border:1px solid var(--line) !important; border-radius:10px !important;
        }
        .stTextInput input:focus, .stTextArea textarea:focus { border-color:var(--blue) !important; }

        /* Buttons */
        .stButton>button, .stDownloadButton>button, .stForm button[kind="primaryFormSubmit"] {
            background: linear-gradient(135deg, #1f6fff 0%, #2b8fff 100%);
            color:#fff; border:0; border-radius:10px; font-weight:600;
            box-shadow:0 6px 18px rgba(31,111,255,0.28); transition:.15s;
        }
        .stButton>button:hover, .stDownloadButton>button:hover { filter:brightness(1.08); transform:translateY(-1px); }

        /* Bordered containers -> glassy cards */
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--panel); border:1px solid var(--line) !important;
            border-radius:14px; box-shadow:0 8px 26px rgba(0,0,0,0.35);
        }
        /* Tabs / expanders */
        .stTabs [data-baseweb="tab-list"] { gap:6px; }
        .stTabs [data-baseweb="tab"] { background:#0c1430; border-radius:8px 8px 0 0; padding:8px 16px; }
        .streamlit-expanderHeader, details summary { color:var(--txt) !important; }

        /* Brand + KPI + Ad bits */
        .brand { font-size:1.35rem; font-weight:800; letter-spacing:.3px; }
        .brand .accent { color:var(--blue2); }
        .brand-sub { font-size:.72rem; letter-spacing:3px; color:#8aa6d6; margin-top:-4px; }
        .page-title { font-size:1.7rem; font-weight:800; margin-bottom:.2rem; }
        .page-sub { color:#9fb4dd; margin-bottom:1rem; }
        .kpi { background:linear-gradient(160deg, rgba(43,143,255,.14), rgba(43,143,255,.03));
               border:1px solid var(--line); border-radius:16px; padding:16px 18px;
               box-shadow:0 8px 26px rgba(0,0,0,.35); height:100%; }
        .kpi .k-label { font-size:.78rem; color:#9fb4dd; text-transform:uppercase; letter-spacing:1px; }
        .kpi .k-val { font-size:1.9rem; font-weight:800; margin-top:6px; color:#fff; }
        .pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:.72rem;
                border:1px solid var(--line); background:rgba(43,143,255,.12); color:var(--blue2);
                margin:2px 4px 2px 0; }
        .ad-label { font-size:.66rem; letter-spacing:2px; color:#7e93bd; text-align:center; margin-bottom:6px; }
        .ad-placeholder { border:1px dashed var(--line); border-radius:14px; min-height:420px;
            display:flex; flex-direction:column; align-items:center; justify-content:center;
            background:rgba(43,143,255,.05); color:#8aa6d6; font-weight:700; text-align:center; }
        .ad-placeholder span { display:block; font-weight:400; font-size:.78rem; margin-top:8px; color:#6f86b3; }
        .muted { color:#9fb4dd; font-size:.85rem; }
        #MainMenu, footer {visibility:hidden;}
        </style>
        """,
        unsafe_allow_html=True,
    )

# ----------------------------------------------------------------------------- AD SLOT (top-right, tall & slim)
def render_ad():
    st.markdown("<div class='ad-label'>ADVERTISEMENT</div>", unsafe_allow_html=True)
    rows = q("SELECT * FROM ads WHERE active=1 ORDER BY created_at DESC")
    if rows:
        ad = rows[0]
        if ad["image_blob"]:
            st.image(bytes(ad["image_blob"]), use_container_width=True)
        if ad["caption"]:
            st.caption(ad["caption"])
        if ad["link"]:
            st.markdown(f"[Learn more →]({ad['link']})")
    else:
        st.markdown(
            "<div class='ad-placeholder'>Your Ad Here<span>Admin → Advertisements<br>to feature your poster</span></div>",
            unsafe_allow_html=True,
        )

# ----------------------------------------------------------------------------- AUTH VIEWS
def login_register_view():
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown(
            "<div class='brand' style='text-align:center'>Mallu Articles <span class='accent'>Bengaluru</span></div>"
            "<div class='brand-sub' style='text-align:center'>CA / CMA ARTICLESHIP CONNECT</div><br>",
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
            with st.form("register", clear_on_submit=False):
                acct = st.radio("I am a", ["CA / CMA Firm (Recruiter)", "Candidate (Student)"], horizontal=True)
                name = st.text_input("Firm name" if acct.startswith("CA") else "Full name")
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
                        if acct.startswith("CA"):
                            lid = execute("INSERT INTO firms (firm_name, created_at) VALUES (?,?)", (name, now))
                            role = "firm"
                        else:
                            lid = execute("INSERT INTO candidates (full_name, created_at) VALUES (?,?)", (name, now))
                            role = "candidate"
                        execute(
                            "INSERT INTO users (username, pw_hash, role, linked_id, created_at) VALUES (?,?,?,?,?)",
                            (ru, hash_pw(rp), role, lid, now),
                        )
                        st.session_state["user"] = dict(
                            q("SELECT * FROM users WHERE username=?", (ru,))[0]
                        )
                        st.success("Account created. Loading your portal…")
                        st.rerun()

# ----------------------------------------------------------------------------- SIDEBAR NAV
def sidebar_nav(user):
    with st.sidebar:
        st.markdown(
            "<div class='brand'>Mallu Articles <span class='accent'>Bengaluru</span></div>"
            "<div class='brand-sub'>ARTICLESHIP CONNECT</div>",
            unsafe_allow_html=True,
        )
        st.write("")
        role = user["role"]
        if role == "admin":
            pages = ["Dashboard", "CA Firms", "Candidates", "Vacancies",
                     "Referrals & Placements", "Advertisements"]
        elif role == "firm":
            pages = ["My Firm Profile", "My Vacancies", "Search Candidates"]
        else:
            pages = ["My Profile & Resume", "Search Firms & Openings"]
        choice = st.radio("Navigate", pages, label_visibility="collapsed")
        st.divider()
        st.markdown(f"<span class='muted'>Signed in as</span><br><b>{user['username']}</b> · {role}", unsafe_allow_html=True)
        if st.button("Log out", use_container_width=True):
            st.session_state.pop("user", None)
            st.rerun()
    return choice

# ----------------------------------------------------------------------------- ADMIN PAGES
def kpi(col, label, value):
    col.markdown(f"<div class='kpi'><div class='k-label'>{label}</div><div class='k-val'>{value}</div></div>",
                 unsafe_allow_html=True)

def admin_dashboard():
    st.markdown("<div class='page-title'>Admin Dashboard</div>"
                "<div class='page-sub'>Live overview — every figure is computed from real portal data.</div>",
                unsafe_allow_html=True)
    n_firms = q("SELECT COUNT(*) c FROM firms")[0]["c"]
    n_cands = q("SELECT COUNT(*) c FROM candidates")[0]["c"]
    n_openvac = q("SELECT COALESCE(SUM(num_vacancies),0) c FROM vacancies WHERE status='Open'")[0]["c"]
    n_apps = q("SELECT COUNT(*) c FROM applications")[0]["c"]
    n_placed = q("SELECT COUNT(*) c FROM applications WHERE status='Joined'")[0]["c"]
    b_recv = q("SELECT COALESCE(SUM(bonus_amount),0) s FROM applications WHERE bonus_status='Received'")[0]["s"]
    b_pend = q("SELECT COALESCE(SUM(bonus_amount),0) s FROM applications WHERE bonus_status='Pending' AND status='Joined'")[0]["s"]

    r1 = st.columns(4)
    kpi(r1[0], "CA / CMA Firms", n_firms)
    kpi(r1[1], "Candidates", n_cands)
    kpi(r1[2], "Open Vacancies", n_openvac)
    kpi(r1[3], "Applications", n_apps)
    st.write("")
    r2 = st.columns(3)
    kpi(r2[0], "Placements (Joined)", n_placed)
    kpi(r2[1], "Bonus Received", inr(b_recv))
    kpi(r2[2], "Bonus Pending", inr(b_pend))
    st.write("")

    cA, cB = st.columns(2)
    with cA:
        with st.container(border=True):
            st.markdown("**Application funnel**")
            df = pd.DataFrame(
                [(s, q("SELECT COUNT(*) c FROM applications WHERE status=?", (s,))[0]["c"]) for s in APP_STATUSES],
                columns=["Status", "Count"],
            ).set_index("Status")
            if df["Count"].sum():
                st.bar_chart(df, height=240)
            else:
                st.caption("No applications yet.")
    with cB:
        with st.container(border=True):
            st.markdown("**Candidates by role sought**")
            rows = q("SELECT role_seeking r, COUNT(*) c FROM candidates WHERE role_seeking IS NOT NULL AND role_seeking<>'' GROUP BY role_seeking")
            df = pd.DataFrame([(r["r"], r["c"]) for r in rows], columns=["Role", "Count"]).set_index("Role")
            if len(df):
                st.bar_chart(df, height=240)
            else:
                st.caption("No candidate profiles yet.")

    with st.container(border=True):
        st.markdown("**Placements over time**")
        rows = q("SELECT substr(created_at,1,7) m, COUNT(*) c FROM applications WHERE status='Joined' GROUP BY m ORDER BY m")
        df = pd.DataFrame([(r["m"], r["c"]) for r in rows], columns=["Month", "Placements"]).set_index("Month")
        if len(df):
            st.line_chart(df, height=220)
        else:
            st.caption("No placements recorded yet.")

def admin_firms():
    st.markdown("<div class='page-title'>CA / CMA Firms</div>"
                "<div class='page-sub'>Full contact details visible to admin only.</div>", unsafe_allow_html=True)
    with st.expander("➕ Add a firm on behalf of a recruiter"):
        with st.form("admin_add_firm", clear_on_submit=True):
            cols = st.columns(2)
            name = cols[0].text_input("Firm name")
            ftype = cols[1].selectbox("Firm type", FIRM_TYPES)
            loc = cols[0].selectbox("Location / area", LOCATIONS)
            phone = cols[1].text_input("Contact phone")
            email = cols[0].text_input("Contact email")
            person = cols[1].text_input("Contact person")
            about = st.text_area("About the firm")
            if st.form_submit_button("Add firm"):
                if name:
                    execute("""INSERT INTO firms (firm_name,firm_type,location_area,about,contact_name,
                               contact_phone,contact_email,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                            (name, ftype, loc, about, person, phone, email, datetime.utcnow().isoformat()))
                    st.success("Firm added."); st.rerun()
                else:
                    st.error("Firm name is required.")
    firms = q("SELECT * FROM firms ORDER BY created_at DESC")
    st.caption(f"{len(firms)} firm(s) registered.")
    for f in firms:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"### {f['firm_name'] or 'Unnamed firm'}")
                st.markdown(f"<span class='pill'>{f['firm_type'] or '—'}</span>"
                            f"<span class='pill'>{f['location_area'] or '—'}</span>", unsafe_allow_html=True)
                if f["about"]:
                    st.write(f["about"])
                st.markdown(f"<span class='muted'>👤 {f['contact_name'] or '—'} · 📞 {f['contact_phone'] or '—'} · "
                            f"✉️ {f['contact_email'] or '—'}</span>", unsafe_allow_html=True)
                vc = q("SELECT COUNT(*) c FROM vacancies WHERE firm_id=?", (f["id"],))[0]["c"]
                st.caption(f"{vc} vacancy posting(s)")
            with c2:
                if st.button("Delete", key=f"delf{f['id']}"):
                    execute("DELETE FROM vacancies WHERE firm_id=?", (f["id"],))
                    execute("DELETE FROM firms WHERE id=?", (f["id"],))
                    st.rerun()

def admin_candidates():
    st.markdown("<div class='page-title'>Candidates</div>"
                "<div class='page-sub'>Full contact details and resumes visible to admin only.</div>",
                unsafe_allow_html=True)
    cands = q("SELECT * FROM candidates ORDER BY created_at DESC")
    st.caption(f"{len(cands)} candidate(s) registered.")
    for c in cands:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"### {c['full_name'] or 'Unnamed candidate'}")
                st.markdown(f"<span class='pill'>{c['course'] or '—'} · {c['level'] or '—'}</span>"
                            f"<span class='pill'>{c['role_seeking'] or '—'}</span>"
                            f"<span class='pill'>Exp: {inr(c['expected_stipend']) if c['expected_stipend'] else '—'}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"**Interest:** {c['areas_of_interest'] or '—'}  \n**Location:** {c['location_pref'] or '—'}")
                if c["about"]:
                    st.write(c["about"])
                st.markdown(f"<span class='muted'>📞 {c['contact_phone'] or '—'} · ✉️ {c['contact_email'] or '—'}</span>",
                            unsafe_allow_html=True)
                if c["resume_link"]:
                    st.markdown(f"[📄 Resume link]({c['resume_link']})")
                if c["resume_blob"]:
                    st.download_button("📄 Download resume", bytes(c["resume_blob"]),
                                       file_name=c["resume_name"] or "resume.pdf", key=f"dl{c['id']}")
            with c2:
                if st.button("Delete", key=f"delc{c['id']}"):
                    execute("DELETE FROM applications WHERE candidate_id=?", (c["id"],))
                    execute("DELETE FROM candidates WHERE id=?", (c["id"],))
                    st.rerun()

def admin_vacancies():
    st.markdown("<div class='page-title'>Vacancies</div>"
                "<div class='page-sub'>All openings posted across firms.</div>", unsafe_allow_html=True)
    rows = q("""SELECT v.*, f.firm_name FROM vacancies v JOIN firms f ON v.firm_id=f.id
                ORDER BY v.created_at DESC""")
    st.caption(f"{len(rows)} vacancy posting(s).")
    for v in rows:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"### {v['role']} · {v['firm_name']}")
                st.markdown(f"<span class='pill'>{v['area_of_work'] or '—'}</span>"
                            f"<span class='pill'>{v['location_area'] or '—'}</span>"
                            f"<span class='pill'>{v['num_vacancies'] or 1} opening(s)</span>"
                            f"<span class='pill'>{inr(v['stipend_min'])}–{inr(v['stipend_max'])}</span>"
                            f"<span class='pill'>{v['status']}</span>", unsafe_allow_html=True)
                if v["jd"]:
                    st.write(v["jd"])
            with c2:
                new = "Closed" if v["status"] == "Open" else "Open"
                if st.button(f"Mark {new}", key=f"tg{v['id']}"):
                    execute("UPDATE vacancies SET status=? WHERE id=?", (new, v["id"])); st.rerun()
                if st.button("Delete", key=f"delv{v['id']}"):
                    execute("DELETE FROM applications WHERE vacancy_id=?", (v["id"],))
                    execute("DELETE FROM vacancies WHERE id=?", (v["id"],)); st.rerun()

def admin_referrals():
    st.markdown("<div class='page-title'>Referrals & Placements</div>"
                "<div class='page-sub'>Track each introduction from application to joining — and the referral bonus.</div>",
                unsafe_allow_html=True)
    rows = q("""SELECT a.*, c.full_name, c.contact_phone cphone, v.role, f.firm_name
                FROM applications a
                JOIN candidates c ON a.candidate_id=c.id
                JOIN vacancies v ON a.vacancy_id=v.id
                JOIN firms f ON v.firm_id=f.id
                ORDER BY a.created_at DESC""")
    if not rows:
        st.info("No referrals yet. They appear here when a candidate requests a referral or a firm shortlists someone.")
        return
    for a in rows:
        with st.container(border=True):
            st.markdown(f"**{a['full_name']}**  →  *{a['role']}* @ **{a['firm_name']}**")
            c = st.columns([2, 2, 2, 1])
            new_status = c[0].selectbox("Status", APP_STATUSES, index=idx(APP_STATUSES, a["status"]), key=f"s{a['id']}")
            bonus = c[1].number_input("Bonus (₹)", min_value=0, value=int(a["bonus_amount"] or 0), step=1000, key=f"b{a['id']}")
            bstat = c[2].selectbox("Bonus status", BONUS_STATUSES, index=idx(BONUS_STATUSES, a["bonus_status"]), key=f"bs{a['id']}")
            if c[3].button("Save", key=f"sa{a['id']}"):
                execute("UPDATE applications SET status=?, bonus_amount=?, bonus_status=? WHERE id=?",
                        (new_status, bonus, bstat, a["id"]))
                st.success("Updated."); st.rerun()

def admin_ads():
    st.markdown("<div class='page-title'>Advertisements</div>"
                "<div class='page-sub'>Images here appear in the tall slot at the top-right of every page.</div>",
                unsafe_allow_html=True)
    with st.form("add_ad", clear_on_submit=True):
        img = st.file_uploader("Ad image (your poster works great — portrait fits best)", type=["png", "jpg", "jpeg"])
        cap = st.text_input("Caption (optional)")
        link = st.text_input("Link URL (optional)")
        if st.form_submit_button("Publish ad"):
            if img:
                execute("INSERT INTO ads (image_blob, link, caption, active, created_at) VALUES (?,?,?,1,?)",
                        (img.read(), link, cap, datetime.utcnow().isoformat()))
                st.success("Ad published."); st.rerun()
            else:
                st.error("Please upload an image.")
    st.divider()
    ads = q("SELECT * FROM ads ORDER BY created_at DESC")
    for ad in ads:
        with st.container(border=True):
            c1, c2 = st.columns([1, 3])
            if ad["image_blob"]:
                c1.image(bytes(ad["image_blob"]), use_container_width=True)
            c2.markdown(f"**{ad['caption'] or '(no caption)'}**  \n{ad['link'] or ''}")
            c2.caption("Active" if ad["active"] else "Inactive")
            b1, b2 = c2.columns(2)
            if b1.button("Activate" if not ad["active"] else "Deactivate", key=f"ad{ad['id']}"):
                execute("UPDATE ads SET active=? WHERE id=?", (0 if ad["active"] else 1, ad["id"])); st.rerun()
            if b2.button("Delete", key=f"adel{ad['id']}"):
                execute("DELETE FROM ads WHERE id=?", (ad["id"],)); st.rerun()

# ----------------------------------------------------------------------------- FIRM PAGES
def firm_profile(user):
    st.markdown("<div class='page-title'>My Firm Profile</div>"
                "<div class='page-sub'>This is your recruiter page. Contact details stay masked from candidates.</div>",
                unsafe_allow_html=True)
    f = q("SELECT * FROM firms WHERE id=?", (user["linked_id"],))[0]
    with st.form("firm_profile"):
        c = st.columns(2)
        name = c[0].text_input("Firm name", f["firm_name"] or "")
        ftype = c[1].selectbox("Firm type", FIRM_TYPES, index=idx(FIRM_TYPES, f["firm_type"]))
        loc = c[0].selectbox("Location / area", LOCATIONS, index=idx(LOCATIONS, f["location_area"]))
        web = c[1].text_input("Website (optional)", f["website"] or "")
        about = st.text_area("About the firm", f["about"] or "", height=120)
        st.markdown("**Contact (masked from candidates — admin coordinates introductions)**")
        c2 = st.columns(3)
        person = c2[0].text_input("Contact person", f["contact_name"] or "")
        phone = c2[1].text_input("Contact phone", f["contact_phone"] or "")
        email = c2[2].text_input("Contact email", f["contact_email"] or "")
        if st.form_submit_button("Save profile"):
            execute("""UPDATE firms SET firm_name=?,firm_type=?,location_area=?,website=?,about=?,
                       contact_name=?,contact_phone=?,contact_email=? WHERE id=?""",
                    (name, ftype, loc, web, about, person, phone, email, user["linked_id"]))
            st.success("Profile saved.")

def firm_vacancies(user):
    st.markdown("<div class='page-title'>My Vacancies</div>"
                "<div class='page-sub'>Post and manage your openings.</div>", unsafe_allow_html=True)
    with st.expander("➕ Post a new vacancy", expanded=False):
        with st.form("new_vac", clear_on_submit=True):
            c = st.columns(2)
            role = c[0].selectbox("Role", ROLES)
            num = c[1].number_input("No. of vacancies", 1, 50, 1)
            area = c[0].selectbox("Area of work", AREAS)
            loc = c[1].selectbox("Location / area", LOCATIONS)
            smin = c[0].number_input("Stipend min (₹/month)", 0, 200000, 0, step=1000)
            smax = c[1].number_input("Stipend max (₹/month)", 0, 200000, 0, step=1000)
            jd = st.text_area("Job description")
            if st.form_submit_button("Post vacancy"):
                execute("""INSERT INTO vacancies (firm_id,role,num_vacancies,area_of_work,jd,
                           stipend_min,stipend_max,location_area,status,created_at)
                           VALUES (?,?,?,?,?,?,?,?, 'Open', ?)""",
                        (user["linked_id"], role, num, area, jd, smin, smax, loc, datetime.utcnow().isoformat()))
                st.success("Vacancy posted."); st.rerun()
    rows = q("SELECT * FROM vacancies WHERE firm_id=? ORDER BY created_at DESC", (user["linked_id"],))
    st.caption(f"{len(rows)} posting(s).")
    for v in rows:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"### {v['role']}")
            c1.markdown(f"<span class='pill'>{v['area_of_work']}</span><span class='pill'>{v['location_area']}</span>"
                        f"<span class='pill'>{v['num_vacancies']} opening(s)</span>"
                        f"<span class='pill'>{inr(v['stipend_min'])}–{inr(v['stipend_max'])}</span>"
                        f"<span class='pill'>{v['status']}</span>", unsafe_allow_html=True)
            if v["jd"]:
                c1.write(v["jd"])
            new = "Closed" if v["status"] == "Open" else "Open"
            if c2.button(f"Mark {new}", key=f"fv{v['id']}"):
                execute("UPDATE vacancies SET status=? WHERE id=?", (new, v["id"])); st.rerun()
            if c2.button("Delete", key=f"fvd{v['id']}"):
                execute("DELETE FROM applications WHERE vacancy_id=?", (v["id"],))
                execute("DELETE FROM vacancies WHERE id=?", (v["id"],)); st.rerun()

def firm_search_candidates(user):
    st.markdown("<div class='page-title'>Search Candidates</div>"
                "<div class='page-sub'>Filter the talent pool. Contacts are masked — shortlist to route via admin.</div>",
                unsafe_allow_html=True)
    my_vacs = q("SELECT * FROM vacancies WHERE firm_id=? AND status='Open' ORDER BY created_at DESC", (user["linked_id"],))
    vac_label = {f"{v['role']} · {v['area_of_work']} (#{v['id']})": v["id"] for v in my_vacs}
    if vac_label:
        sel_vac = st.selectbox("Sourcing for which open vacancy?", list(vac_label.keys()))
        active_vac_id = vac_label[sel_vac]
    else:
        active_vac_id = None
        st.warning("Post an open vacancy first to shortlist candidates against it.")

    with st.container(border=True):
        c = st.columns(4)
        f_roles = c[0].multiselect("Role", ROLES)
        f_locs = c[1].multiselect("Location / area", LOCATIONS)
        f_areas = c[2].multiselect("Area of interest", AREAS)
        f_stip = c[3].number_input("Max expected stipend (₹)", 0, 200000, 0, step=1000)

    sql = "SELECT * FROM candidates WHERE 1=1"
    params = []
    if f_roles:
        sql += f" AND role_seeking IN ({','.join('?'*len(f_roles))})"; params += f_roles
    if f_locs:
        sub = " OR ".join(["location_pref LIKE ?"] * len(f_locs))
        sql += f" AND ({sub})"; params += [f"%{x}%" for x in f_locs]
    if f_areas:
        sub = " OR ".join(["areas_of_interest LIKE ?"] * len(f_areas))
        sql += f" AND ({sub})"; params += [f"%{x}%" for x in f_areas]
    if f_stip:
        sql += " AND expected_stipend>0 AND expected_stipend<=?"; params.append(f_stip)
    sql += " ORDER BY created_at DESC"
    rows = q(sql, tuple(params))
    st.caption(f"{len(rows)} candidate(s) match.")

    for c in rows:
        with st.container(border=True):
            cc = st.columns([4, 1])
            with cc[0]:
                st.markdown(f"### {c['full_name'] or 'Candidate'}")
                st.markdown(f"<span class='pill'>{c['course'] or '—'} · {c['level'] or '—'}</span>"
                            f"<span class='pill'>{c['role_seeking'] or '—'}</span>"
                            f"<span class='pill'>Exp: {inr(c['expected_stipend']) if c['expected_stipend'] else '—'}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"**Interest:** {c['areas_of_interest'] or '—'}  \n**Location:** {c['location_pref'] or '—'}")
                if c["about"]:
                    st.write(c["about"])
                st.markdown(f"<span class='muted'>📞 {mask_phone(c['contact_phone'])} · ✉️ {mask_email(c['contact_email'])}</span>",
                            unsafe_allow_html=True)
            with cc[1]:
                disabled = active_vac_id is None
                if st.button("Shortlist", key=f"sl{c['id']}", disabled=disabled, use_container_width=True):
                    try:
                        execute("""INSERT INTO applications (candidate_id,vacancy_id,status,created_at)
                                   VALUES (?,?, 'Shortlisted', ?)""",
                                (c["id"], active_vac_id, datetime.utcnow().isoformat()))
                        st.success("Shortlisted — admin will coordinate.")
                    except sqlite3.IntegrityError:
                        st.info("Already in your pipeline for this vacancy.")

# ----------------------------------------------------------------------------- CANDIDATE PAGES
def candidate_profile(user):
    st.markdown("<div class='page-title'>My Profile & Resume</div>"
                "<div class='page-sub'>This is your student page. Contact details stay masked from firms.</div>",
                unsafe_allow_html=True)
    c = q("SELECT * FROM candidates WHERE id=?", (user["linked_id"],))[0]
    cur_areas = [a for a in (c["areas_of_interest"] or "").split(", ") if a in AREAS]
    cur_locs = [l for l in (c["location_pref"] or "").split(", ") if l in LOCATIONS]
    with st.form("cand_profile"):
        cc = st.columns(2)
        name = cc[0].text_input("Full name", c["full_name"] or "")
        course = cc[1].selectbox("Course", COURSES, index=idx(COURSES, c["course"]))
        level = cc[0].selectbox("Level", LEVELS, index=idx(LEVELS, c["level"]))
        role = cc[1].selectbox("Role seeking", ROLES, index=idx(ROLES, c["role_seeking"]))
        areas = st.multiselect("Areas of interest", AREAS, default=cur_areas)
        locs = st.multiselect("Preferred location(s)", LOCATIONS, default=cur_locs)
        stip = st.number_input("Expected stipend (₹/month)", 0, 200000, int(c["expected_stipend"] or 0), step=1000)
        about = st.text_area("About you (strengths, availability, etc.)", c["about"] or "", height=110)
        link = st.text_input("Resume link (Google Drive / URL)", c["resume_link"] or "")
        up = st.file_uploader("Or upload resume (PDF)", type=["pdf"])
        st.markdown("**Contact (masked from firms — admin coordinates introductions)**")
        cc2 = st.columns(2)
        phone = cc2[0].text_input("Contact phone", c["contact_phone"] or "")
        email = cc2[1].text_input("Contact email", c["contact_email"] or "")
        if st.form_submit_button("Save profile"):
            blob = up.read() if up else c["resume_blob"]
            rname = up.name if up else c["resume_name"]
            execute("""UPDATE candidates SET full_name=?,course=?,level=?,role_seeking=?,areas_of_interest=?,
                       location_pref=?,expected_stipend=?,about=?,resume_link=?,resume_blob=?,resume_name=?,
                       contact_phone=?,contact_email=? WHERE id=?""",
                    (name, course, level, role, csv_join(areas), csv_join(locs), stip, about, link, blob, rname,
                     phone, email, user["linked_id"]))
            st.success("Profile saved.")

def candidate_search_firms(user):
    st.markdown("<div class='page-title'>Search Firms & Openings</div>"
                "<div class='page-sub'>Filter live openings. Request a referral and the admin team takes it forward.</div>",
                unsafe_allow_html=True)
    with st.container(border=True):
        c = st.columns(4)
        f_roles = c[0].multiselect("Role", ROLES)
        f_locs = c[1].multiselect("Location / area", LOCATIONS)
        f_areas = c[2].multiselect("Area of work", AREAS)
        f_stip = c[3].number_input("Min stipend (₹)", 0, 200000, 0, step=1000)

    sql = """SELECT v.*, f.firm_name, f.firm_type, f.location_area firm_loc, f.about firm_about,
             f.contact_phone, f.contact_email, f.website
             FROM vacancies v JOIN firms f ON v.firm_id=f.id WHERE v.status='Open'"""
    params = []
    if f_roles:
        sql += f" AND v.role IN ({','.join('?'*len(f_roles))})"; params += f_roles
    if f_locs:
        sql += f" AND v.location_area IN ({','.join('?'*len(f_locs))})"; params += f_locs
    if f_areas:
        sql += f" AND v.area_of_work IN ({','.join('?'*len(f_areas))})"; params += f_areas
    if f_stip:
        sql += " AND v.stipend_max>=?"; params.append(f_stip)
    sql += " ORDER BY v.created_at DESC"
    rows = q(sql, tuple(params))
    st.caption(f"{len(rows)} opening(s) match.")

    for v in rows:
        with st.container(border=True):
            cc = st.columns([4, 1])
            with cc[0]:
                st.markdown(f"### {v['firm_name']} — {v['role']}")
                st.markdown(f"<span class='pill'>{v['firm_type'] or '—'}</span>"
                            f"<span class='pill'>{v['area_of_work']}</span>"
                            f"<span class='pill'>{v['location_area']}</span>"
                            f"<span class='pill'>{v['num_vacancies']} opening(s)</span>"
                            f"<span class='pill'>{inr(v['stipend_min'])}–{inr(v['stipend_max'])}</span>",
                            unsafe_allow_html=True)
                if v["firm_about"]:
                    st.write(v["firm_about"])
                if v["jd"]:
                    st.markdown(f"**JD:** {v['jd']}")
                st.markdown(f"<span class='muted'>📞 {mask_phone(v['contact_phone'])} · "
                            f"✉️ {mask_email(v['contact_email'])}</span>", unsafe_allow_html=True)
            with cc[1]:
                if st.button("Request referral", key=f"rr{v['id']}", use_container_width=True):
                    try:
                        execute("""INSERT INTO applications (candidate_id,vacancy_id,status,created_at)
                                   VALUES (?,?, 'Applied', ?)""",
                                (user["linked_id"], v["id"], datetime.utcnow().isoformat()))
                        st.success("Referral requested — admin will coordinate.")
                    except sqlite3.IntegrityError:
                        st.info("You have already requested this opening.")

# ----------------------------------------------------------------------------- ROUTER
def dispatch(user, page):
    role = user["role"]
    if role == "admin":
        {"Dashboard": admin_dashboard, "CA Firms": admin_firms, "Candidates": admin_candidates,
         "Vacancies": admin_vacancies, "Referrals & Placements": admin_referrals,
         "Advertisements": admin_ads}[page]()
    elif role == "firm":
        {"My Firm Profile": firm_profile, "My Vacancies": firm_vacancies,
         "Search Candidates": firm_search_candidates}[page](user)
    else:
        {"My Profile & Resume": candidate_profile,
         "Search Firms & Openings": candidate_search_firms}[page](user)

def main():
    init_db()
    inject_css()
    if not st.session_state.get("user"):
        login_register_view()
        return
    user = st.session_state["user"]
    page = sidebar_nav(user)
    body, ad = st.columns([5, 1.25], gap="large")
    with ad:
        render_ad()
    with body:
        dispatch(user, page)

if __name__ == "__main__":
    main()
