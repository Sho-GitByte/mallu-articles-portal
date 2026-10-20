# learn.py — Learn & Earn.
#
# Two things neighbours can do for each other besides selling food:
#
#   Earn  — post a job ("cook for my mother, 7–10 AM, ₹6,000 a month") or apply for
#           one nearby. Anyone signed in can post; anyone can apply, once per job.
#   Learn — every skill in catalog.SKILLS has a YouTube search that always resolves,
#           any videos the GharSe team has watched and vouches for, and the women in
#           your own area who have offered to teach it. A teacher in your neighbourhood.
#
# Tables: jobs, job_applications, skill_offers, learn_requests, skill_links.
#
# Contact details stay private. A phone number is shown only to the other party, and
# only once the application or learn request has been accepted.

import re
from datetime import date, timedelta
from urllib.parse import parse_qs, quote_plus, urlsplit

import streamlit as st

from catalog import AREAS, ASK_GROUPS, JOB_KINDS, LANGUAGES, PAY_UNITS, SKILL_LEVELS, SKILLS, TEACH_MODES
from store import execute, now_iso, q, today_ist
from ui import csv_join, csv_split, customer_of, h, idx, inr, kpi, notify, provider_of, short_dt, table, wa_link

PAGE_TITLE = "Learn & Earn"

# How a teacher prices her time. Not in catalog.py because nothing else uses it yet.
FEE_UNITS = ["per class", "per hour", "per month", "for the whole course"]

JOB_OPEN, JOB_FILLED, JOB_CLOSED, JOB_REMOVED = "Open", "Filled", "Closed", "Removed"
_JOB_LABEL = {JOB_OPEN: "Open", JOB_FILLED: "Filled", JOB_CLOSED: "Closed", JOB_REMOVED: "Closed by GharSe"}

APPLIED, ACCEPTED, DECLINED = "Applied", "Accepted", "Declined"
REQUESTED = "Requested"

YOUTUBE_SEARCH = "https://www.youtube.com/results?search_query="
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
# A video ID is always 11 characters of this alphabet; playlist IDs vary in length.
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_LIST_ID = re.compile(r"[A-Za-z0-9_-]{10,64}")
_SHORT_PATH = re.compile(r"/([A-Za-z0-9_-]{11})/?")                       # youtu.be/ID
_VIDEO_PATH = re.compile(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?")  # youtube.com/shorts/ID …

_SAFETY_LINE = ("For a first meeting, meet at your own home with someone else around, or somewhere public. "
                "Never pay a full course fee in advance — pay class by class until you trust each other.")

# Which areas are next to which, so "nearby" means something. Anything not listed is
# simply "elsewhere in Bengaluru". Made symmetric below.
_NEXT_TO = {
    "HSR Layout": ["Koramangala", "BTM Layout", "Bellandur", "Electronic City"],
    "Koramangala": ["BTM Layout", "Indiranagar", "Jayanagar", "CBD / MG Road", "Bellandur"],
    "BTM Layout": ["Jayanagar", "JP Nagar", "Electronic City"],
    "Bellandur": ["Marathahalli"],
    "Marathahalli": ["Whitefield", "Indiranagar"],
    "Indiranagar": ["CBD / MG Road"],
    "Jayanagar": ["JP Nagar", "Banashankari"],
    "JP Nagar": ["Banashankari"],
    "Rajajinagar": ["Malleshwaram"],
    "Malleshwaram": ["Hebbal", "CBD / MG Road"],
    "Yelahanka": ["Hebbal"],
}
_NEAR = {}
for _a, _bs in _NEXT_TO.items():
    for _b in _bs:
        _NEAR.setdefault(_a, set()).add(_b)
        _NEAR.setdefault(_b, set()).add(_a)


def has_home(area):
    """True when an area is a real neighbourhood we can rank by. "Other" can mean
    anywhere in the city, so two people in "Other" are not neighbours."""
    return bool(area) and area in AREAS and area != "Other"


def nearness(my_area, area):
    """0 = your area, 1 = next door, 2 = elsewhere in the city (or we can't tell)."""
    if not has_home(my_area) or not has_home(area):
        return 2
    if area == my_area:
        return 0
    if area in _NEAR.get(my_area, ()):
        return 1
    return 2


# ----------------------------------------------------------------------------- links
def youtube_search_url(query):
    """A YouTube search for a skill. Searches never 404, unlike individual videos."""
    return YOUTUBE_SEARCH + quote_plus(query or "")


def _watch(video_id):
    return "https://www.youtube.com/watch?v=" + video_id


def check_youtube_url(url):
    """(ok, canonical url or reason). Only https links to a single video or playlist on
    youtube.com or youtu.be get through — this link is shown to every learner as
    vouched for by GharSe. The link is rebuilt from the video or playlist ID alone, so
    nothing else the person pasted (extra paths, '..' segments, tracking parameters)
    ever reaches a learner, and the same video pasted twice is recognised."""
    u = (url or "").strip()
    if not u:
        return False, "Paste the video's link."
    if len(u) > 500 or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in u):
        return False, "That doesn't look like a single link. Copy it again from the YouTube share button."
    if not u.isascii():
        return False, ("That link has look-alike letters in it, so it may not be YouTube. "
                       "Copy it straight from YouTube's share button.")
    hiding = "That link hides where it really goes. Copy it straight from YouTube's share button."
    if "\\" in u:
        return False, hiding
    try:
        p = urlsplit(u)
        port = p.port
    except ValueError:
        return False, "That link isn't valid. Copy it again from the YouTube share button."
    if p.scheme.lower() != "https":
        return False, "Only https:// YouTube links can be added (youtube.com or youtu.be)."
    netloc = p.netloc.lower()
    host = (p.hostname or "").lower()
    if "@" in netloc or p.username is not None or p.password is not None or port not in (None, 443):
        return False, hiding
    if host not in _YT_HOSTS or netloc not in (host, host + ":443"):
        return False, "That link doesn't go to YouTube. Only youtube.com or youtu.be links can be added."
    path = p.path or ""
    low = path.lower()
    if any(seg in (".", "..") for seg in path.split("/")) or any(e in low for e in ("%2e", "%2f", "%5c")):
        return False, hiding
    not_video = "That's a YouTube page, but not a video or playlist. Open the video itself and copy its link."
    if host == "youtu.be":
        m = _SHORT_PATH.fullmatch(path)
        if not m:
            return False, "That youtu.be link has no video in it. Open the video and copy its link."
        return True, _watch(m.group(1))
    query = parse_qs(p.query)
    if path in ("/watch", "/watch/"):
        v = query.get("v", [])
        if len(v) == 1 and _VIDEO_ID.fullmatch(v[0]):
            return True, _watch(v[0])
        return False, not_video
    if path in ("/playlist", "/playlist/"):
        lst = query.get("list", [])
        if len(lst) == 1 and _LIST_ID.fullmatch(lst[0]):
            return True, "https://www.youtube.com/playlist?list=" + lst[0]
        return False, not_video
    m = _VIDEO_PATH.fullmatch(path)
    if m:
        return True, _watch(m.group(1))
    return False, not_video


def safe_video_url(url):
    """The canonical link for a stored video, or None if it no longer passes the check
    (a row saved before the check was tightened). Never link to a URL that fails."""
    ok, result = check_youtube_url(url)
    return result if ok else None


# ----------------------------------------------------------------------------- people
def _who_sql(col, p):
    """Extra SELECT columns and JOINs that turn a users.id column into a name, phone,
    area and verified flag, whatever the person's role. Prefix p keeps them apart."""
    sel = (f", {p}u.full_name AS {p}_uname, {p}u.phone AS {p}_uphone, {p}u.role AS {p}_role, "
           f"{p}pr.owner_name AS {p}_owner, {p}pr.display_name AS {p}_kitchen, {p}pr.phone AS {p}_pphone, "
           f"{p}pr.verified AS {p}_verified, {p}pr.area AS {p}_parea, "
           f"{p}cu.full_name AS {p}_cname, {p}cu.phone AS {p}_cphone, {p}cu.area AS {p}_carea")
    joins = (f" LEFT JOIN users {p}u ON {p}u.id={col}"
             f" LEFT JOIN providers {p}pr ON {p}u.role='provider' AND {p}pr.id={p}u.linked_id"
             f" LEFT JOIN customers {p}cu ON {p}u.role='customer' AND {p}cu.id={p}u.linked_id")
    return sel, joins


def _who(row, p):
    def g(k):
        v = row.get(f"{p}_{k}")
        return v.strip() if isinstance(v, str) else v
    role = g("role")
    name = g("uname") or g("owner") or g("kitchen") or g("cname")
    if not name:
        name = "GharSe team" if role == "admin" else "A neighbour"
    return {
        "name": name,
        "phone": g("uphone") or g("pphone") or g("cphone") or "",
        "area": g("parea") or g("carea") or "",
        "verified": bool(g("verified")),
        "role": role,
    }


def _person(user_id):
    sel, joins = _who_sql("x.id", "x")
    rows = q(f"SELECT x.id AS uid{sel} FROM users x{joins} WHERE x.id=?", (user_id,))
    if not rows:
        return {"name": "A neighbour", "phone": "", "area": "", "verified": False, "role": None}
    return _who(dict(rows[0]), "x")


_FALLBACK_NAMES = ("A neighbour", "GharSe team")


def _first(name):
    """First name for a friendly line. The fallbacks stay whole ("posted by A" is no name)."""
    name = (name or "").strip()
    if not name:
        return "A neighbour"
    return name if name in _FALLBACK_NAMES else name.split()[0]


def user_area(user):
    rec = {}
    if user.get("role") == "provider":
        rec = provider_of(user)
    elif user.get("role") == "customer":
        rec = customer_of(user)
    area = (rec or {}).get("area")
    # Unknown (no area on file, or an admin) stays unknown: guessing AREAS[0] told a
    # stranger's jobs that they were "In HSR Layout".
    return area if area in AREAS else None


def _tell(user_id, kind, body):
    """WhatsApp someone by user id. Skipped when they have no number — a message with
    nowhere to go would only clutter the team's outbox."""
    who = _person(user_id)
    if who["phone"]:
        notify(who["phone"], who["name"], who["role"] or "user", kind, body)


# ----------------------------------------------------------------------------- formatting
def _pay(job, esc=True):
    """Pay as markup (esc=True, escaped) or as plain text for table(), which escapes itself."""
    pay = job.get("pay") or 0
    if pay > 0:
        unit = job.get("pay_unit") or ""
        return f"{inr(pay)} {h(unit) if esc else unit}".strip()
    return "Volunteer" if job.get("kind") == "Volunteer" else "Pay to discuss"


def _fee(offer, esc=True):
    fee = offer.get("fee") or 0
    if fee > 0:
        unit = offer.get("fee_unit") or ""
        return f"{inr(fee)} {h(unit) if esc else unit}".strip()
    return "Free"


def _cell(v):
    """A DB value for ui.table(), which HTML-escapes but expects one line: collapse
    whitespace so a stored blank line can't end the table's HTML block early."""
    return " ".join(v.split()) if isinstance(v, str) else v


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10]).strftime("%a %d %b")
    except (TypeError, ValueError):
        return str(v) if v else "—"


def _plural(n, word, plural=None):
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _pill(status):
    s = status or ""
    if s in (ACCEPTED, JOB_FILLED):
        cls = "pill ok"
    elif s in (DECLINED, JOB_CLOSED, JOB_REMOVED):
        cls = "pill warn"
    else:
        cls = "pill"
    label = _JOB_LABEL.get(s, s)
    if s == REQUESTED:
        label = "Waiting for her reply"
    elif s == APPLIED:
        label = "Applied"
    return f"<span class='{cls}'>{h(label)}</span>"


def _near_label(n, area):
    return {0: f"In {h(area)}", 1: "Nearby"}.get(n, "Elsewhere in Bengaluru")


def _no_home_note(area):
    """Shown instead of "in <area>" when we can't tell what's near this person."""
    if area == "Other":
        return ("Your area is set to “Other”, so we can't tell what's nearest to you. If your "
                "neighbourhood is in the list, pick it in your profile.")
    return "Set your area in your profile to see what's nearest to you first."


def _flash(key):
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


def _contact_line(other_name, phone, my_phone, wa_text, key):
    st.markdown(f"Reach <b>{h(other_name)}</b> on <b>{h(phone or 'no number on file yet')}</b>.",
                unsafe_allow_html=True)
    st.caption(f"Your number {h(my_phone)} has been shared with them." if my_phone else
               "Add your phone number in your profile so they can reach you too.")
    link = wa_link(phone, wa_text) if phone else None
    if link:
        st.link_button("WhatsApp", link, key=key)


# ----------------------------------------------------------------------------- page
def learn_and_earn(user):
    st.markdown(
        f"<div class='page-title'>{PAGE_TITLE}</div>"
        "<div class='page-sub'>Work and learning, from the people on your own street. Find a part-time job "
        "nearby or post one, pick up a skill from a free video — or from an aunty two lanes away who has "
        "been stitching blouses for twenty years — and teach what you know.</div>",
        unsafe_allow_html=True,
    )
    me = _person(user["id"])
    area = user_area(user)
    _flash("learn_flash")
    tabs = st.tabs(["Jobs near me", "Post a job", "Learn a skill", "Teach a skill", "My activity"],
                   key="learn_tab", on_change="rerun")
    with tabs[0]:
        _jobs_near(user, me, area)
    with tabs[1]:
        _post_job(user, me, area)
        st.write("")
        _my_jobs(user, me)
    with tabs[2]:
        _learn(user, me, area)
    with tabs[3]:
        _teach(user, me, area)
    with tabs[4]:
        _activity(user, me)


# ----------------------------------------------------------------------------- jobs near me
def _jobs_near(user, me, area):
    c = st.columns([2, 1])
    kind = c[0].selectbox("Kind of work", ["All kinds"] + JOB_KINDS, key="lj_kind")
    home = has_home(area)
    only_mine = c[1].checkbox(f"Only {area}", key="lj_only") if home else False

    sel, joins = _who_sql("j.poster_user_id", "po")
    rows = q(f"SELECT j.*{sel}, "
             "(SELECT COUNT(*) FROM job_applications a WHERE a.job_id=j.id) AS applicants "
             f"FROM jobs j{joins} WHERE j.status=? AND j.poster_user_id<>? ORDER BY j.id DESC",
             (JOB_OPEN, user["id"]))
    jobs = [dict(r) for r in rows]
    if kind != "All kinds":
        jobs = [j for j in jobs if j.get("kind") == kind]
    if only_mine:
        jobs = [j for j in jobs if j.get("area") == area]
    jobs.sort(key=lambda j: nearness(area, j.get("area")))   # stable: newest first within each band

    if home:
        here = sum(1 for j in jobs if j.get("area") == area)
        st.markdown(f"<span class='muted'>{_plural(here, 'open job')} in {h(area)}"
                    f"{' · ' + str(len(jobs) - here) + ' more nearby and across the city' if len(jobs) > here else ''}"
                    "</span>", unsafe_allow_html=True)
    else:
        st.markdown(f"<span class='muted'>{_plural(len(jobs), 'open job')} across Bengaluru</span>",
                    unsafe_allow_html=True)
        st.caption(_no_home_note(area))
    if not jobs:
        st.info("No open jobs here yet. When a neighbour posts one, it shows up here first for your area. "
                "Need help yourself? Post a job in the next tab.")
        return

    mine = {r["job_id"]: r["status"] for r in
            q("SELECT job_id, status FROM job_applications WHERE applicant_user_id=?", (user["id"],))}
    band = None
    for j in jobs:
        n = nearness(area, j.get("area"))
        if home and n != band:
            band = n
            st.markdown(f"<span class='muted'><b>{_near_label(n, area)}</b></span>", unsafe_allow_html=True)
        poster = _who(j, "po")
        with st.container(border=True):
            c = st.columns([4, 1])
            desc = f"<br>{h(j['description'])}" if (j.get("description") or "").strip() else ""
            badge = " <span class='pill ok'>Verified</span>" if poster["verified"] else ""
            c[0].markdown(
                f"<b>{h(j.get('title'))}</b> <span class='pill'>{h(j.get('kind'))}</span>"
                f"<span class='pill'>{h(j.get('category') or '')}</span><br>"
                f"<span class='muted'>{h(j.get('area'))} · {h(j.get('hours') or 'hours to discuss')} · "
                f"starts {h(_date(j.get('starts')))} · posted by {h(_first(poster['name']))}{badge} "
                f"· {h(short_dt(j.get('created_at')))}</span>{desc}",
                unsafe_allow_html=True)
            c[1].markdown(f"<div class='price'>{_pay(j)}</div>"
                          f"<span class='muted'>{_plural(j.get('applicants') or 0, 'applicant')}</span>",
                          unsafe_allow_html=True)
            status = mine.get(j["id"])
            if status:
                st.markdown(_pill(status), unsafe_allow_html=True)
                if status == ACCEPTED:
                    _contact_line(poster["name"], poster["phone"], me["phone"],
                                  f"Hi, I'm {me['name']} from GharSe, about your job \"{j.get('title')}\".",
                                  f"ljwa{j['id']}")
                continue
            with st.expander("Apply for this job"):
                with st.form(f"lj_apply{j['id']}"):
                    note = st.text_input("A short note to them", key=f"lj_note{j['id']}",
                                         placeholder="What you've done before, when you can start")
                    if st.form_submit_button("Apply", type="primary"):
                        _apply(user, me, j, note)
                if not me["phone"]:
                    st.caption("You have no phone number on file. Add one in your profile, or they "
                               "won't be able to reach you if they say yes.")


def _apply(user, me, job, note):
    if not note.strip():
        st.error("Write a line about yourself — it's what they'll decide on.")
        return
    fresh = q("SELECT status FROM jobs WHERE id=?", (job["id"],))
    if not fresh or fresh[0]["status"] != JOB_OPEN:
        st.error("This job has just closed.")
        return
    # One statement, so two quick submits can't both pass a separate check. (A unique
    # index on (job_id, applicant_user_id) would make this airtight on Postgres too.)
    new_id = execute(
        "INSERT INTO job_applications (job_id, applicant_user_id, note, status, created_at) "
        "SELECT ?,?,?,?,? WHERE NOT EXISTS "
        "(SELECT 1 FROM job_applications WHERE job_id=? AND applicant_user_id=?) RETURNING id",
        (job["id"], user["id"], note.strip(), APPLIED, now_iso(), job["id"], user["id"]))
    if new_id is None:
        st.error("You've already applied for this job.")
        return
    _tell(job["poster_user_id"], "job_applied",
          f"{me['name']} has applied for your job \"{job.get('title')}\" on GharSe: \"{note.strip()}\". "
          "Open Learn & Earn to accept or decline.")
    st.session_state["learn_flash"] = (f"Applied for “{h(job.get('title'))}”. If they accept, you'll both see "
                                       "each other's phone number.")
    st.rerun()


# ----------------------------------------------------------------------------- post a job
def _post_job(user, me, area):
    n = st.session_state.get("lj_post_n", 0)
    k = lambda name: f"lj_{name}_{n}"   # noqa: E731
    with st.container(border=True):
        st.markdown("#### Post a job")
        st.caption("A part-time cook for your parents, a helper for your tiffin kitchen, someone to string "
                   "flowers before a wedding. Say the pay clearly — good people apply when they know it.")
        with st.form(f"lj_post_{n}"):
            title = st.text_input("Job title", key=k("title"),
                                  placeholder="e.g. Cook for an elderly couple, mornings")
            c = st.columns(2)
            kind = c[0].selectbox("Kind", JOB_KINDS, key=k("kind"))
            category = c[1].selectbox("Kind of work", list(ASK_GROUPS), key=k("cat"))
            desc = st.text_area("What the work involves", key=k("desc"), height=90,
                                placeholder="Tasks, who it's for, anything they should know before applying.")
            c = st.columns(3)
            pay = c[0].number_input("Pay (₹)", 0.0, 1000000.0, 0.0, step=100.0, key=k("pay"))
            unit = c[1].selectbox("Pay is", PAY_UNITS, key=k("unit"))
            hours = c[2].text_input("Hours", key=k("hours"), placeholder="e.g. 7–10 AM, Mon–Sat")
            c = st.columns(2)
            where = c[0].selectbox("Area", AREAS, index=idx(AREAS, area) if area else None,
                                   placeholder="Choose the area", key=k("area"))
            starts = c[1].date_input("Starts", today_ist() + timedelta(days=1), min_value=today_ist(),
                                     key=k("starts"))
            if st.form_submit_button("Post job", type="primary"):
                errors = []
                if not title.strip():
                    errors.append("Give the job a title.")
                if pay <= 0 and kind != "Volunteer":
                    errors.append("Enter the pay. If it's unpaid, choose “Volunteer” as the kind.")
                if not where:
                    errors.append("Choose the area where the work is.")
                if errors:
                    for e in errors:
                        st.error(e)
                    return
                execute("INSERT INTO jobs (poster_user_id, title, kind, category, description, pay, pay_unit, "
                        "hours, area, starts, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (user["id"], title.strip(), kind, category, desc.strip(), pay if pay > 0 else None,
                         unit, hours.strip(), where, starts.isoformat(), JOB_OPEN, now_iso()))
                st.session_state["lj_post_n"] = n + 1
                st.session_state["learn_flash"] = (f"Posted “{h(title.strip())}”. Neighbours in {h(where)} see it "
                                                   "first — applications will appear under Your job posts.")
                st.rerun()


def _my_jobs(user, me):
    st.markdown("#### Your job posts")
    jobs = [dict(r) for r in q("SELECT * FROM jobs WHERE poster_user_id=? ORDER BY "
                               "CASE WHEN status=? THEN 0 ELSE 1 END, id DESC", (user["id"], JOB_OPEN))]
    if not jobs:
        st.caption("You haven't posted a job yet. Your posts and everyone who applies will show here.")
        return
    sel, joins = _who_sql("a.applicant_user_id", "ap")
    for j in jobs:
        apps = [dict(r) for r in q(
            f"SELECT a.*{sel} FROM job_applications a{joins} WHERE a.job_id=? "
            "ORDER BY CASE a.status WHEN 'Accepted' THEN 0 WHEN 'Applied' THEN 1 ELSE 2 END, a.id",
            (j["id"],))]
        is_open = j.get("status") == JOB_OPEN
        with st.container(border=True):
            c = st.columns([4, 1])
            c[0].markdown(
                f"<b>{h(j.get('title'))}</b> <span class='pill'>{h(j.get('kind'))}</span><br>"
                f"<span class='muted'>{h(j.get('area'))} · {_pay(j)} · {h(j.get('hours') or '—')} · "
                f"starts {h(_date(j.get('starts')))}</span>", unsafe_allow_html=True)
            c[1].markdown(_pill(j.get("status")), unsafe_allow_html=True)
            if j.get("status") == JOB_REMOVED:
                st.caption("The GharSe team closed this post. We've sent you a message saying why.")
            if not apps:
                st.caption("No applications yet." if is_open else "Nobody applied.")
            for a in apps:
                who = _who(a, "ap")
                badge = " <span class='pill ok'>Verified</span>" if who["verified"] else ""
                ac = st.columns([3, 1, 1])
                ac[0].markdown(
                    f"<b>{h(who['name'])}</b>{badge} <span class='muted'>· {h(who['area'] or '—')} · "
                    f"{h(short_dt(a.get('created_at')))}</span><br>{h(a.get('note') or '')}",
                    unsafe_allow_html=True)
                if a["status"] == APPLIED and is_open:
                    if ac[1].button("Accept", key=f"lja{a['id']}", type="primary", width="stretch"):
                        _decide_application(user, me, j, a, ACCEPTED)
                    if ac[2].button("Decline", key=f"ljd{a['id']}", width="stretch"):
                        _decide_application(user, me, j, a, DECLINED)
                else:
                    ac[1].markdown(_pill(a["status"]), unsafe_allow_html=True)
                if a["status"] == ACCEPTED and j.get("status") == JOB_REMOVED:
                    st.caption("GharSe closed this post, so contact details are no longer shown here.")
                elif a["status"] == ACCEPTED:
                    _contact_line(who["name"], who["phone"], me["phone"],
                                  f"Hi {_first(who['name'])}, this is {me['name']} from GharSe, about the job "
                                  f"\"{j.get('title')}\".", f"ljwa_ap{a['id']}")
            if is_open:
                bc = st.columns(2)
                if bc[0].button("Mark filled", key=f"ljfill{j['id']}", width="stretch"):
                    _end_job(j, JOB_FILLED)
                if bc[1].button("Close job", key=f"ljclose{j['id']}", width="stretch"):
                    _end_job(j, JOB_CLOSED)


def _decide_application(user, me, job, app, status):
    # Check and change in one statement: only a still-waiting application on a job of
    # yours that is still open can be answered, however stale the page was.
    done = execute("UPDATE job_applications SET status=? WHERE id=? AND status=? AND EXISTS "
                   "(SELECT 1 FROM jobs WHERE jobs.id=? AND jobs.status=? AND jobs.poster_user_id=?) "
                   "RETURNING id", (status, app["id"], APPLIED, job["id"], JOB_OPEN, user["id"]))
    if done is None:
        st.session_state["learn_flash"] = "That application has already been answered."
        st.rerun()
    who = _who(app, "ap")
    if status == ACCEPTED:
        _tell(app["applicant_user_id"], "job_accepted",
              f"Good news — {me['name']} accepted your application for \"{job.get('title')}\" in {job.get('area')}, "
              f"starting {_date(job.get('starts'))}."
              + (f" Their number: {me['phone']}." if me["phone"] else " Open GharSe for the details."))
        st.session_state["learn_flash"] = (f"You accepted {h(who['name'])}. Their number is below, and yours has "
                                           "been shared with them.")
    else:
        _tell(app["applicant_user_id"], "job_declined",
              f"\"{job.get('title')}\" went to someone else this time. Thank you for applying — "
              "there are more jobs near you on GharSe.")
        st.session_state["learn_flash"] = f"Declined {h(who['name'])}'s application. They've been told kindly."
    st.rerun()


def _end_job(job, status):
    if execute("UPDATE jobs SET status=? WHERE id=? AND status=? RETURNING id",
               (status, job["id"], JOB_OPEN)) is None:
        st.session_state["learn_flash"] = "That job was already closed."
        st.rerun()
    pending = q("SELECT id, applicant_user_id FROM job_applications WHERE job_id=? AND status=?",
                (job["id"], APPLIED))
    execute("UPDATE job_applications SET status=? WHERE job_id=? AND status=?", (DECLINED, job["id"], APPLIED))
    for p in pending:
        _tell(p["applicant_user_id"], "job_closed",
              f"\"{job.get('title')}\" is no longer taking applications. Thank you for applying — "
              "there are more jobs near you on GharSe.")
    st.session_state["learn_flash"] = ("Marked as filled. Congratulations!" if status == JOB_FILLED
                                       else "Job closed. Anyone still waiting has been told.")
    st.rerun()


# ----------------------------------------------------------------------------- learn a skill
def _learn(user, me, area):
    sel, joins = _who_sql("o.teacher_user_id", "t")
    offers = [dict(r) for r in q(f"SELECT o.*{sel} FROM skill_offers o{joins} WHERE o.active=1 ORDER BY o.id")]
    links = [dict(r) for r in q("SELECT * FROM skill_links ORDER BY id")]
    asked = {r["offer_id"]: r["status"] for r in
             q("SELECT offer_id, status FROM learn_requests WHERE learner_user_id=?", (user["id"],))}

    by_skill = {}
    for o in offers:
        by_skill.setdefault(o.get("skill"), []).append(o)
    home = has_home(area)
    here_teachers = {o["teacher_user_id"] for o in offers if home and o.get("area") == area}
    here_skills = {o.get("skill") for o in offers if home and o.get("area") == area}
    if not home:
        st.markdown(f"<span class='muted'>{_plural(len({o['teacher_user_id'] for o in offers}), 'neighbour')} "
                    "on GharSe teach — the videos below are free, and if you know a skill yourself, offer it "
                    "under Teach a skill.</span>", unsafe_allow_html=True)
        st.caption(_no_home_note(area))
    elif here_teachers:
        st.markdown(f"<b>{_plural(len(here_teachers), 'neighbour')} in {h(area)} "
                    f"{'teaches' if len(here_teachers) == 1 else 'teach'} {_plural(len(here_skills), 'skill')}.</b> "
                    "<span class='muted'>Watch the videos to start, and ask a teacher nearby when you want "
                    "someone to show you properly.</span>", unsafe_allow_html=True)
    else:
        st.markdown(f"<span class='muted'>Nobody in {h(area)} has offered to teach yet — the videos below are "
                    "free, and if you know a skill yourself, offer it under Teach a skill.</span>",
                    unsafe_allow_html=True)

    c = st.columns([2, 1])
    pick = c[0].selectbox("Skill", ["All skills"] + list(SKILLS), key="ll_skill")
    near_only = c[1].checkbox(f"Only skills taught in {area}", key="ll_near") if home else False
    skills = list(SKILLS) if pick == "All skills" else [pick]
    if near_only:
        skills = [s for s in skills if s in here_skills]
        if not skills:
            st.caption(f"No one in {area} teaches {'that' if pick != 'All skills' else 'anything'} yet.")
    # Area-first at the skill level too: a skill with a teacher in your own area comes
    # before seventeen skills that have nobody near you. Catalog order breaks ties.
    order = {s: i for i, s in enumerate(SKILLS)}
    skills.sort(key=lambda s: (min((nearness(area, o.get("area")) for o in by_skill.get(s, [])), default=3),
                               order.get(s, len(order))))

    for skill in skills:
        teachers = sorted(by_skill.get(skill, []), key=lambda o: (nearness(area, o.get("area")), o["id"]))
        vids = [(v, safe_video_url(v.get("url"))) for v in links if v.get("skill") == skill]
        vids = [(v, u) for v, u in vids if u]   # a stored link that fails today's check is never shown
        n_here = sum(1 for o in teachers if nearness(area, o.get("area")) == 0)
        n_near = sum(1 for o in teachers if nearness(area, o.get("area")) == 1)
        with st.container(border=True):
            c = st.columns([3, 1])
            if home:
                count = (f"<span class='pill ok'>{_plural(n_here, 'teacher')} in {h(area)}</span>" if n_here else
                         f"<span class='pill'>No teacher in {h(area)} yet</span>")
                more = len(teachers) - n_here
                extra = (f" <span class='muted'>· {n_near} nearby"
                         + (f", {more - n_near} elsewhere" if more > n_near else "") + "</span>") if more else ""
            else:
                count = (f"<span class='pill'>{_plural(len(teachers), 'teacher')} on GharSe</span>" if teachers
                         else "<span class='pill'>No teacher yet</span>")
                extra = ""
            c[0].markdown(f"<b>{h(skill)}</b><br>{count}{extra}", unsafe_allow_html=True)
            c[1].link_button("Watch tutorials", youtube_search_url(SKILLS[skill]), key=f"llyt{idx(list(SKILLS), skill)}",
                             width="stretch")
            if vids:
                items = "".join(
                    f"<li><a href='{h(u)}' target='_blank' rel='noopener noreferrer'>{h(v.get('title'))}</a>"
                    f" <span class='muted'>· {h(v.get('language') or '')}</span></li>" for v, u in vids)
                st.markdown(f"<span class='muted'>Watched and recommended by the GharSe team</span><ul>{items}</ul>",
                            unsafe_allow_html=True)
            if teachers:
                st.markdown(f"<span class='muted'><b>{'Teachers near you' if home else 'Teachers'}</b></span>",
                            unsafe_allow_html=True)
            for o in teachers:
                _teacher_card(user, me, area, o, asked.get(o["id"]))


def _teacher_card(user, me, area, o, status):
    t = _who(o, "t")
    n = nearness(area, o.get("area"))
    badge = " <span class='pill ok'>Verified</span>" if t["verified"] else ""
    langs = csv_join(csv_split(o.get("languages"))) or "—"
    about = f"<br>{h(o['about'])}" if (o.get("about") or "").strip() else ""
    near = f" <span class='pill'>{_near_label(n, o.get('area') or '')}</span>" if has_home(area) else ""
    st.markdown(
        f"<b>{h(t['name'])}</b>{badge}{near}<br>"
        f"<span class='muted'>{h(o.get('area') or '—')} · {h(o.get('level') or '')} level · {h(o.get('mode') or '')} · "
        f"speaks {h(langs)}</span> · <b>{_fee(o)}</b>{about}",
        unsafe_allow_html=True)
    if o["teacher_user_id"] == user["id"]:
        st.markdown("<span class='pill'>Your offer</span>", unsafe_allow_html=True)
        return
    if status:
        st.markdown(_pill(status), unsafe_allow_html=True)
        if status == ACCEPTED:
            _contact_line(t["name"], t["phone"], me["phone"],
                          f"Hi {_first(t['name'])}, I'm {me['name']} from GharSe — you agreed to teach me "
                          f"{o.get('skill')}.", f"llwa{o['id']}")
        return
    with st.expander(f"Ask {h(_first(t['name']))} to teach you"):
        with st.form(f"ll_req{o['id']}"):
            note = st.text_input("A note to her", key=f"ll_note{o['id']}",
                                 placeholder="What you already know, what you'd like to learn, when you're free")
            if st.form_submit_button("Request to learn", type="primary"):
                _request_learn(user, me, o, t, note)


def _request_learn(user, me, offer, teacher, note):
    if not note.strip():
        st.error("Write a short note so she knows what you'd like to learn.")
        return
    fresh = q("SELECT active FROM skill_offers WHERE id=?", (offer["id"],))
    if not fresh or not fresh[0]["active"]:
        st.error("She has paused this offer for now.")
        return
    if offer["teacher_user_id"] == user["id"]:
        st.error("This is your own offer.")
        return
    new_id = execute(
        "INSERT INTO learn_requests (offer_id, learner_user_id, note, status, created_at) "
        "SELECT ?,?,?,?,? WHERE NOT EXISTS "
        "(SELECT 1 FROM learn_requests WHERE offer_id=? AND learner_user_id=?) RETURNING id",
        (offer["id"], user["id"], note.strip(), REQUESTED, now_iso(), offer["id"], user["id"]))
    if new_id is None:
        st.error("You've already asked her about this.")
        return
    _tell(offer["teacher_user_id"], "learn_request",
          f"{me['name']} would like to learn {offer.get('skill')} from you: \"{note.strip()}\". "
          "Open Learn & Earn on GharSe to accept or decline.")
    st.session_state["learn_flash"] = (f"Request sent to {h(teacher['name'])}. If she says yes, you'll both see "
                                       "each other's phone number.")
    st.rerun()


# ----------------------------------------------------------------------------- teach a skill
def _teach(user, me, area):
    n = st.session_state.get("lt_offer_n", 0)
    k = lambda name: f"lt_{name}_{n}"   # noqa: E731
    default_langs = [x for x in csv_split(provider_of(user).get("languages") if user.get("role") == "provider"
                                          else "") if x in LANGUAGES]
    with st.container(border=True):
        st.markdown("#### Offer to teach")
        st.caption("What you know is worth passing on. Teach for a fee, or for free — set the fee to 0 and "
                   "learners will see “Free”. Offering the same skill again updates your earlier offer.")
        with st.form(f"lt_offer_{n}"):
            c = st.columns(2)
            skill = c[0].selectbox("Skill", list(SKILLS), key=k("skill"))
            level = c[1].selectbox("You can teach up to", SKILL_LEVELS, key=k("level"))
            c = st.columns(3)
            mode = c[0].selectbox("Where", TEACH_MODES, key=k("mode"))
            fee = c[1].number_input("Fee (₹, 0 = free)", 0.0, 100000.0, 0.0, step=50.0, key=k("fee"))
            unit = c[2].selectbox("Fee is", FEE_UNITS, key=k("unit"))
            c = st.columns(2)
            where = c[0].selectbox("Area", AREAS, index=idx(AREAS, area) if area else None,
                                   placeholder="Choose the area", key=k("area"))
            langs = c[1].multiselect("Languages you teach in", LANGUAGES, default=default_langs, key=k("langs"))
            about = st.text_area("About you and your classes", key=k("about"), height=80,
                                 placeholder="How long you've done this, what a learner can make after a month.")
            if st.form_submit_button("Offer to teach", type="primary"):
                if not langs:
                    st.error("Choose at least one language you teach in.")
                    return
                if not where:
                    st.error("Choose the area you teach in.")
                    return
                existing = q("SELECT id FROM skill_offers WHERE teacher_user_id=? AND skill=?", (user["id"], skill))
                vals = (level, mode, fee, unit, where, csv_join(langs), about.strip())
                if existing:
                    execute("UPDATE skill_offers SET level=?, mode=?, fee=?, fee_unit=?, area=?, languages=?, "
                            "about=?, active=1 WHERE id=?", vals + (existing[0]["id"],))
                    msg = f"Updated your {h(skill)} offer. It's live for learners in {h(where)}."
                else:
                    execute("INSERT INTO skill_offers (teacher_user_id, skill, level, mode, fee, fee_unit, area, "
                            "languages, about, active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (user["id"], skill) + vals + (1, now_iso()))
                    msg = (f"You're now teaching {h(skill)}. Learners in {h(where)} see you first — their requests "
                           "will appear below.")
                st.session_state["lt_offer_n"] = n + 1
                st.session_state["learn_flash"] = msg
                st.rerun()
    st.caption(_SAFETY_LINE)
    st.write("")
    _my_offers(user)
    st.write("")
    _incoming(user, me)


def _my_offers(user):
    st.markdown("#### What you teach")
    offers = [dict(r) for r in q(
        "SELECT o.*, (SELECT COUNT(*) FROM learn_requests r WHERE r.offer_id=o.id) AS requests "
        "FROM skill_offers o WHERE o.teacher_user_id=? ORDER BY o.active DESC, o.id DESC", (user["id"],))]
    if not offers:
        st.caption("You haven't offered to teach anything yet.")
        return
    for o in offers:
        with st.container(border=True):
            c = st.columns([4, 1])
            state = "<span class='pill ok'>Live</span>" if o.get("active") else "<span class='pill warn'>Paused</span>"
            c[0].markdown(
                f"<b>{h(o.get('skill'))}</b> {state}<br><span class='muted'>{h(o.get('area'))} · "
                f"{h(o.get('level'))} · {h(o.get('mode'))} · {_fee(o)} · "
                f"{_plural(o.get('requests') or 0, 'request')}</span>", unsafe_allow_html=True)
            if o.get("active"):
                if c[1].button("Pause", key=f"ltp{o['id']}", width="stretch"):
                    execute("UPDATE skill_offers SET active=0 WHERE id=?", (o["id"],))
                    st.session_state["learn_flash"] = f"Paused {h(o.get('skill'))}. Learners won't see it until you resume."
                    st.rerun()
            elif c[1].button("Resume", key=f"ltr{o['id']}", width="stretch"):
                execute("UPDATE skill_offers SET active=1 WHERE id=?", (o["id"],))
                st.session_state["learn_flash"] = f"{h(o.get('skill'))} is live again."
                st.rerun()


def _incoming(user, me):
    st.markdown("#### People who want to learn from you")
    sel, joins = _who_sql("r.learner_user_id", "le")
    reqs = [dict(r) for r in q(
        f"SELECT r.*, o.skill, o.teacher_user_id{sel} FROM learn_requests r "
        f"JOIN skill_offers o ON o.id=r.offer_id{joins} WHERE o.teacher_user_id=? "
        "ORDER BY CASE r.status WHEN 'Requested' THEN 0 ELSE 1 END, r.id DESC", (user["id"],))]
    if not reqs:
        st.caption("No requests yet. When someone asks to learn from you, it shows here.")
        return
    for r in reqs:
        who = _who(r, "le")
        with st.container(border=True):
            c = st.columns([3, 1, 1])
            c[0].markdown(
                f"<b>{h(who['name'])}</b> <span class='muted'>· {h(who['area'] or '—')} · wants to learn "
                f"{h(r.get('skill'))} · {h(short_dt(r.get('created_at')))}</span><br>{h(r.get('note') or '')}",
                unsafe_allow_html=True)
            if r["status"] == REQUESTED:
                if c[1].button("Accept", key=f"lta{r['id']}", type="primary", width="stretch"):
                    _decide_learn(me, r, ACCEPTED)
                if c[2].button("Decline", key=f"ltd{r['id']}", width="stretch"):
                    _decide_learn(me, r, DECLINED)
            else:
                c[1].markdown(_pill(r["status"]), unsafe_allow_html=True)
            if r["status"] == ACCEPTED:
                _contact_line(who["name"], who["phone"], me["phone"],
                              f"Hi {_first(who['name'])}, I'm {me['name']} from GharSe — happy to teach you "
                              f"{r.get('skill')}.", f"ltwa{r['id']}")


def _decide_learn(me, req, status):
    if execute("UPDATE learn_requests SET status=? WHERE id=? AND status=? RETURNING id",
               (status, req["id"], REQUESTED)) is None:
        st.session_state["learn_flash"] = "That request has already been answered."
        st.rerun()
    who = _who(req, "le")
    if status == ACCEPTED:
        _tell(req["learner_user_id"], "learn_accepted",
              f"{me['name']} has agreed to teach you {req.get('skill')}."
              + (f" Her number: {me['phone']}." if me["phone"] else " Open GharSe for the details.")
              + " For the first class, meet at home with someone around, or somewhere public.")
        st.session_state["learn_flash"] = (f"You'll teach {h(who['name'])}. Their number is below, and yours has "
                                           "been shared with them.")
    else:
        _tell(req["learner_user_id"], "learn_declined",
              f"{me['name']} can't take on a new learner for {req.get('skill')} right now. "
              "Other teachers near you are on GharSe's Learn & Earn page.")
        st.session_state["learn_flash"] = f"Declined {h(who['name'])}'s request. They've been told kindly."
    st.rerun()


# ----------------------------------------------------------------------------- my activity
def _activity(user, me):
    uid = user["id"]
    st.markdown("#### Jobs you posted")
    table([{"job": _cell(r["title"]), "area": r["area"], "status": _JOB_LABEL.get(r["status"], r["status"]),
            "applicants": r["applicants"], "accepted": r["accepted"], "posted_at": r["created_at"]}
           for r in q("SELECT j.*, "
                      "(SELECT COUNT(*) FROM job_applications a WHERE a.job_id=j.id) AS applicants, "
                      "(SELECT COUNT(*) FROM job_applications a WHERE a.job_id=j.id AND a.status='Accepted') "
                      "AS accepted FROM jobs j WHERE j.poster_user_id=? ORDER BY j.id DESC", (uid,))],
          empty="You haven't posted a job.")

    st.markdown("#### Jobs you applied for")
    sel, joins = _who_sql("j.poster_user_id", "po")
    rows = []
    for r in q(f"SELECT a.status AS app_status, a.created_at AS applied_at, j.title, j.area, j.status AS job_status"
               f"{sel} FROM job_applications a JOIN jobs j ON j.id=a.job_id{joins} "
               "WHERE a.applicant_user_id=? ORDER BY a.id DESC", (uid,)):
        d = dict(r)
        poster = _who(d, "po")
        if d["app_status"] != ACCEPTED:
            contact = ""
        elif d["job_status"] == JOB_REMOVED:
            contact = "Hidden: GharSe closed this post"   # a takedown also ends the contact
        else:
            contact = f"{poster['name']} · {poster['phone'] or 'no number'}"
        rows.append({"job": _cell(d["title"]), "area": d["area"], "your application": d["app_status"],
                     "job is": _JOB_LABEL.get(d["job_status"], d["job_status"]),
                     "contact": _cell(contact),
                     "applied_at": d["applied_at"]})
    table(rows, empty="You haven't applied for a job.")

    st.markdown("#### Skills you teach")
    table([{"skill": r["skill"], "area": r["area"], "fee": _cell(_fee(dict(r), esc=False)),
            "status": "Live" if r["active"] else "Paused", "requests": r["requests"],
            "accepted": r["accepted"]}
           for r in q("SELECT o.*, "
                      "(SELECT COUNT(*) FROM learn_requests r WHERE r.offer_id=o.id) AS requests, "
                      "(SELECT COUNT(*) FROM learn_requests r WHERE r.offer_id=o.id AND r.status='Accepted') "
                      "AS accepted FROM skill_offers o WHERE o.teacher_user_id=? ORDER BY o.id DESC", (uid,))],
          empty="You haven't offered to teach anything.")

    st.markdown("#### Skills you asked to learn")
    sel, joins = _who_sql("o.teacher_user_id", "t")
    rows = []
    for r in q(f"SELECT r.status, r.created_at AS asked_at, o.skill, o.area{sel} FROM learn_requests r "
               f"JOIN skill_offers o ON o.id=r.offer_id{joins} WHERE r.learner_user_id=? ORDER BY r.id DESC", (uid,)):
        d = dict(r)
        t = _who(d, "t")
        rows.append({"skill": d["skill"], "teacher": _cell(t["name"]), "area": d["area"],
                     "status": "Waiting" if d["status"] == REQUESTED else d["status"],
                     "contact": _cell(t["phone"]) if d["status"] == ACCEPTED else "", "asked_at": d["asked_at"]})
    table(rows, empty="You haven't asked anyone to teach you yet.")


# ----------------------------------------------------------------------------- admin
def learn_admin():
    st.markdown(
        f"<div class='page-title'>{PAGE_TITLE}</div>"
        "<div class='page-sub'>Jobs, teachers and learners across the neighbourhoods, and the training videos "
        "the team vouches for. Only add a video you have watched from start to finish.</div>",
        unsafe_allow_html=True,
    )
    _flash("learn_admin_flash")
    count = lambda sql, p=(): q(sql, p)[0]["n"]   # noqa: E731
    c = st.columns(6)
    kpi(c[0], "Open jobs", f"{count('SELECT COUNT(*) AS n FROM jobs WHERE status=?', (JOB_OPEN,))}")
    kpi(c[1], "Jobs posted", f"{count('SELECT COUNT(*) AS n FROM jobs')}")
    kpi(c[2], "Applications", f"{count('SELECT COUNT(*) AS n FROM job_applications')}")
    kpi(c[3], "Live teachers", f"{count('SELECT COUNT(*) AS n FROM skill_offers WHERE active=1')}")
    kpi(c[4], "Learn requests", f"{count('SELECT COUNT(*) AS n FROM learn_requests')}")
    kpi(c[5], "Vouched videos", f"{count('SELECT COUNT(*) AS n FROM skill_links')}")
    st.write("")

    me = st.session_state.get("user") or {}
    n = st.session_state.get("la_link_n", 0)
    with st.container(border=True):
        st.markdown("#### Add a training video")
        st.caption("Paste a youtube.com or youtu.be link. Learners see it under the skill, marked as recommended "
                   "by GharSe.")
        with st.form(f"la_link_{n}"):
            c = st.columns(2)
            skill = c[0].selectbox("Skill", list(SKILLS), key=f"la_skill_{n}")
            lang = c[1].selectbox("Language", LANGUAGES, key=f"la_lang_{n}")
            title = st.text_input("Title learners will see", key=f"la_title_{n}",
                                  placeholder="e.g. Blouse cutting for beginners — part 1")
            url = st.text_input("YouTube link", key=f"la_url_{n}", placeholder="https://www.youtube.com/watch?v=…")
            if st.form_submit_button("Add video", type="primary"):
                ok, result = check_youtube_url(url)
                if not title.strip():
                    st.error("Give the video a title learners will understand.")
                elif not ok:
                    st.error(h(result))
                elif q("SELECT 1 FROM skill_links WHERE skill=? AND url=?", (skill, result)):
                    st.error("That video is already listed under this skill.")
                else:
                    execute("INSERT INTO skill_links (skill, title, url, source, language, added_by, created_at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (skill, title.strip(), result, "YouTube", lang, me.get("id"), now_iso()))
                    st.session_state["la_link_n"] = n + 1
                    st.session_state["learn_admin_flash"] = f"Added “{h(title.strip())}” under {h(skill)}."
                    st.rerun()

    st.markdown("#### Vouched videos")
    links = [dict(r) for r in q("SELECT * FROM skill_links ORDER BY skill, id")]
    if not links:
        st.caption("None yet. Learners still get a YouTube search for every skill.")
    for v in links:
        c = st.columns([4, 1])
        u = safe_video_url(v.get("url"))
        link = (f"<a href='{h(u)}' target='_blank' rel='noopener noreferrer'>{h(v.get('title'))}</a>" if u else
                f"{h(v.get('title'))} <span class='pill warn'>Link fails the YouTube check — hidden from "
                "learners. Remove it and add it again.</span>")
        c[0].markdown(f"<b>{h(v.get('skill'))}</b> · {link} "
                      f"<span class='muted'>· {h(v.get('language') or '')} · added {h(short_dt(v.get('created_at')))}"
                      "</span>", unsafe_allow_html=True)
        if c[1].button("Remove", key=f"larm{v['id']}", width="stretch"):
            execute("DELETE FROM skill_links WHERE id=?", (v["id"],))
            st.session_state["learn_admin_flash"] = f"Removed “{h(v.get('title'))}”."
            st.rerun()

    st.markdown("#### Open jobs")
    sel, joins = _who_sql("j.poster_user_id", "po")
    jobs = [dict(r) for r in q(f"SELECT j.*{sel}, "
                               "(SELECT COUNT(*) FROM job_applications a WHERE a.job_id=j.id) AS applicants "
                               f"FROM jobs j{joins} WHERE j.status=? ORDER BY j.id", (JOB_OPEN,))]
    if not jobs:
        st.caption("No open jobs right now.")
    for j in jobs:
        poster = _who(j, "po")
        with st.container(border=True):
            st.markdown(
                f"<b>{h(j.get('title'))}</b> <span class='pill'>{h(j.get('kind'))}</span> "
                f"<span class='muted'>#{h(j['id'])} · {h(j.get('area'))} · {_pay(j)} · posted by {h(poster['name'])} · "
                f"{_plural(j.get('applicants') or 0, 'applicant')}</span>"
                + (f"<br>{h(j['description'])}" if (j.get("description") or "").strip() else ""),
                unsafe_allow_html=True)
            with st.form(f"la_close{j['id']}"):
                fc = st.columns([3, 1])
                reason = fc[0].text_input("Reason (sent to the poster)", key=f"la_reason{j['id']}",
                                          placeholder="e.g. Pay below the legal minimum wage")
                if fc[1].form_submit_button("Close job"):
                    if not reason.strip():
                        st.error("Write the reason — the poster deserves to know why.")
                    else:
                        _admin_close(j, reason.strip())

    st.markdown("#### Teachers")
    sel, joins = _who_sql("o.teacher_user_id", "t")
    table([{"skill": d["skill"], "teacher": _cell(_who(d, "t")["name"]), "area": d["area"],
            "fee": _cell(_fee(d, esc=False)),
            "status": "Live" if d["active"] else "Paused", "requests": d["requests"], "offered_at": d["created_at"]}
           for d in (dict(r) for r in q(
               f"SELECT o.*{sel}, (SELECT COUNT(*) FROM learn_requests r WHERE r.offer_id=o.id) AS requests "
               f"FROM skill_offers o{joins} ORDER BY o.id DESC"))],
          empty="Nobody has offered to teach yet.")


def _admin_close(job, reason):
    if execute("UPDATE jobs SET status=? WHERE id=? AND status=? RETURNING id",
               (JOB_REMOVED, job["id"], JOB_OPEN)) is None:
        st.session_state["learn_admin_flash"] = f"Job #{job['id']} was already closed — nothing changed."
        st.rerun()
    pending = q("SELECT applicant_user_id FROM job_applications WHERE job_id=? AND status=?", (job["id"], APPLIED))
    execute("UPDATE job_applications SET status=? WHERE job_id=? AND status=?", (DECLINED, job["id"], APPLIED))
    _tell(job["poster_user_id"], "job_removed",
          f"The GharSe team closed your job post \"{job.get('title')}\": {reason}. "
          "Reply to this message if you think it was a mistake.")
    for p in pending:
        _tell(p["applicant_user_id"], "job_closed",
              f"\"{job.get('title')}\" has been taken down and is no longer taking applications.")
    st.session_state["learn_admin_flash"] = f"Closed job #{job['id']}. The poster has been told why."
    st.rerun()
