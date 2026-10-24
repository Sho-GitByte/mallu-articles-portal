# rentals.py — Rooms & PGs.
#
# People advertise a vacant room, a PG bed, a flat share or a whole flat in their own
# neighbourhood; others find one and book it. Same login, same KYC as the rest of GharSe.
#
# The flow, and who sees what:
#
#   1. Find — a card shows the room, the rent, the owner's FIRST name and verification
#      badge. Never the exact address, never a phone number.
#   2. Request a visit — free. A note to the owner. Still no contact details either way.
#   3. The owner ACCEPTS — only now do both sides see each other's phone, and the guest
#      sees the exact address. Declined, withdrawn or closed requests see nothing, and the
#      details are hidden again while either account is on hold, while the owner's identity
#      is no longer verified, or while the team has the listing on hold.
#   4. Optional booking amount — the owner sets a token or first month. GharSe adds its
#      platform and processing fees ON TOP (ui.split_on_top), the guest pays the platform
#      UPI and claims it with the reference, and the team confirms it on Rentals Admin.
#   5. The team pays the owner what she asked for and records it in social_payouts
#      (source "rental").
#   6. The stay ends. Relisting a let SINGLE place (a whole flat, a private room) closes its
#      tenancy and tells the guest. A shared place is relisted with its tenants left as they
#      are — a PG with one bed free is still home to the others — and the owner closes one
#      tenancy at a time with "Stay over" on that guest's request, which tells the guest.
#
# Tables: rentals, rental_requests, social_payouts.
#
#   rentals.status           Active | Paused (by the owner) | Let | On hold (by GharSe) | Removed
#   rentals.prehold_status   the status a listing had when the team put it On hold, written by
#                            the same UPDATE that holds it; restoring puts it back there
#   rental_requests.status   Requested | Accepted | Declined | Withdrawn | Cancelled | Closed
#   rental_requests.payment_status
#                            NULL (no booking amount) | Unpaid | Claimed | Paid | Paid out
#                            | Released (Paid, on a listing the team has on hold or removed,
#                              and cleared by the team for the owner's payout all the same)
#                            | Refund check (called off while only CLAIMED — the team checks
#                              whether money actually arrived) | Refund due (called off after
#                              the team CONFIRMED the money) | Refunded
#
# Every change that guards money or a booking is one conditional UPDATE … RETURNING id, so
# a double click, two tabs, or the owner and the team acting at once can never confirm a
# payment twice, pay an owner twice, or accept a request on a listing that was just taken
# down. "Paid out" is set per request by the same kind of statement before the payout is
# recorded, which is what makes an owner impossible to pay twice for the same booking.
#
# A whole flat or a private room takes one booking at a time. Two guests claiming it in the
# same instant can both pass that check on Postgres (READ COMMITTED), so a claim is checked
# again once it has committed: it backs off to a booking already confirmed, and otherwise to
# the request with the smaller id, so whatever order they commit in exactly one claim keeps
# the place. The one that backs off is closed as Refund check (its reference kept for the
# team) and that guest is told.
#
# The owner's standing is re-checked INSIDE each of those statements, not just on the page:
# an owner who is on hold or whose identity is no longer verified cannot accept a visit
# (which is what shares contact details), ask for or take a booking amount, or be paid out,
# and a listing the team has put on hold freezes its payouts until it is sorted out. A frozen
# booking is settled by the team on Rentals Admin when waiting won't fix it (a removed
# listing never comes back): refund the guest, or — only while the owner is in good standing
# — release it into the owner's payout. _PAYABLE_SQL / _freeze_reason are the one definition
# of what can be paid out.

import re
from datetime import date
from urllib.parse import quote

import streamlit as st

import kyc
import safety
from catalog import AMENITIES, AREAS, GENDER_PREF, RENTAL_KINDS, SHARING
from store import execute, now_iso, q, setting, today_ist
from ui import (
    MAX_IMG_MB,
    csv_join,
    csv_split,
    h,
    idx,
    inr,
    kpi,
    notify,
    process_image,
    safe_image,
    short_dt,
    split_on_top,
    table,
    wa_link,
)

RENTALS_PAGE_TITLE = "Rooms & PGs"
ADMIN_PAGE_TITLE = "Rentals Admin"

# Listing states.
ACTIVE, PAUSED, LET, HOLD, REMOVED = "Active", "Paused", "Let", "On hold", "Removed"
# Request states.
REQUESTED, ACCEPTED, DECLINED, WITHDRAWN, CANCELLED, CLOSED = (
    "Requested", "Accepted", "Declined", "Withdrawn", "Cancelled", "Closed")
OPEN_REQUEST = (REQUESTED, ACCEPTED)
# Payment states (NULL means the owner hasn't asked for a booking amount).
UNPAID, CLAIMED, PAID, PAID_OUT, REFUND_CHECK, REFUND_DUE, REFUNDED = (
    "Unpaid", "Claimed", "Paid", "Paid out", "Refund check", "Refund due", "Refunded")
# Paid, frozen by its listing (on hold or removed), and released by the team to the owner.
RELEASED = "Released"
# Confirmed money that belongs to the booking (not owed back to the guest).
_PAID_IN = (PAID, RELEASED, PAID_OUT)
# Money is (or may be) with GharSe and the request still needs someone's attention.
_MONEY_LIVE = (CLAIMED, PAID, RELEASED, REFUND_CHECK, REFUND_DUE)

# Notification kinds whose order_id (the table's generic reference column) is a rental id;
# the owner's My listings reads the latest one back to show why the team acted.
_MOD_KINDS = ("Room listing on hold", "Room listing removed")

MIN_BOOKING = 100
FLASH = "rentals_flash"
ADMIN_FLASH = "rentals_admin_flash"
# What a guest is told while the team is looking into a place or its owner — the same
# words whether it is the payment or the contact details that are held back.
_UNDER_REVIEW = "while the GharSe team looks into it. Don't pay anything for now."

# A phone number or an email inside a listing or a note would hand contact details to a
# stranger before anyone has accepted anything — exactly what this page is built to stop.
# This is best effort: the text is normalised first (spelled-out digits, "at" / "dot"),
# and only SINGLE separators are allowed between digits, so "98765/00001" and
# "080 2345 6789" are caught while "Rent 8000 - 9000 - 10000" is not mistaken for a number.
_SEP = r"[\s./-]?"
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91" + _SEP + r"|0" + _SEP + r")?[6-9](?:" + _SEP + r"\d){9}(?!\d)")
_LANDLINE_RE = re.compile(r"(?<!\d)0" + _SEP + r"\d{2,4}(?:" + _SEP + r"\d){6,8}(?!\d)")
_SPELLED_PHONE_RE = re.compile(r"(?<!\d)[6-9](?:[\s,.-]+\d){9}(?!\d)")   # one digit per word
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_UPI_RE = re.compile(r"\b[\w.-]{2,}@(?:ok)?[a-z]{2,}\b", re.IGNORECASE)
_UPI_HANDLE_RE = re.compile(r"\b[\w.-]{2,}@(?:ok\w+|ybl|ibl|axl|apl|paytm|upi|okicici|okhdfcbank)\b",
                            re.IGNORECASE)
_DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DIGIT_WORD_RE = re.compile("|".join(_DIGIT_WORDS), re.IGNORECASE)
_AT_RE = re.compile(r"\s*[\(\[\{]?\s*\bat\b\s*[\)\]\}]?\s*", re.IGNORECASE)
_DOT_RE = re.compile(r"\s*[\(\[\{]?\s*\bdot\b\s*[\)\]\}]?\s*", re.IGNORECASE)

# Columns for the person on a request or a listing. The profile names come along because
# app.py's sign-up writes a person's name to customers.full_name / providers.owner_name,
# not to users.full_name — without them the only name left would be the login handle.
# users.kyc_name (the name as on the ID) is deliberately NOT here: these rows feed what
# strangers see, and a KYC field never reaches a non-admin.
_USER_COLS = ("u.id uid, u.username, u.full_name, u.kyc_status, u.kyc_method, "
              "u.phone_verified, u.suspended, "
              "(SELECT p.owner_name FROM providers p WHERE p.id = u.provider_id) pname, "
              "(SELECT c.full_name FROM customers c WHERE c.id = u.customer_id) cname")

# SQL pieces re-checked inside the money / contact statements (see the header).
# Matches kyc.is_verified: identity verified AND phone proved; plus not on hold.
_OWNER_IN_GOOD_STANDING = ("SELECT id FROM users WHERE COALESCE(suspended,0)=0 "
                           "AND kyc_status='verified' AND COALESCE(phone_verified,0)<>0")
_NOT_HELD_USERS = "SELECT id FROM users WHERE COALESCE(suspended,0)=0"
# A whole flat, or any private (single-occupancy) room, can take one booking at a time.
_SINGLE_PLACES = "SELECT id FROM rentals WHERE kind='Whole flat' OR sharing='Private'"
# Another open request on the same single place already holds it — a claim, or confirmed money.
_HELD_ELSEWHERE = (
    f"rental_id IN ({_SINGLE_PLACES}) AND EXISTS (SELECT 1 FROM rental_requests o "
    "WHERE o.rental_id = rental_requests.rental_id AND o.id <> rental_requests.id "
    "AND o.status IN ('Requested','Accepted') "
    "AND o.payment_status IN ('Claimed','Paid','Released','Paid out'))")
_ONE_BOOKING_PER_SINGLE_PLACE = f"NOT ({_HELD_ELSEWHERE})"
# The same rule checked again AFTER a claim commits (see _single_place_backoff): this claim
# must give way if another open request on the place holds it with confirmed money, or with
# a claim of its own and the smaller id. That tie-break is what leaves exactly one holder
# whichever order two racing claims commit in — the earliest request never gives way to a
# later one, and a confirmed booking never gives way at all.
_GIVES_WAY = (
    f"rental_id IN ({_SINGLE_PLACES}) AND EXISTS (SELECT 1 FROM rental_requests o "
    "WHERE o.rental_id = rental_requests.rental_id AND o.id <> rental_requests.id "
    "AND o.status IN ('Requested','Accepted') "
    "AND (o.payment_status IN ('Paid','Released','Paid out') "
    "OR (o.payment_status='Claimed' AND o.id < rental_requests.id)))")


# ----------------------------------------------------------------------------- helpers
def _uid(user):
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _flash(key):
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


def _money(x):
    """₹438.90 — paise shown, because the itemised fees are not whole rupees."""
    paise = round(float(x or 0) * 100)
    rupees, rest = divmod(abs(paise), 100)
    out = inr(rupees) + (f".{rest:02d}" if rest else "")
    return ("-" + out) if paise < 0 else out


def _num(v):
    """A stored money figure as a float; 0 for NULL or anything that isn't a number (a row
    written outside the form must not take the page down)."""
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x and abs(x) != float("inf") else 0.0


def _clamped(v, hi):
    return min(max(int(_num(v)), 0), hi)


def _looks_like_contact(text):
    t = text or ""
    return bool(_PHONE_RE.search(t) or _EMAIL_RE.search(t) or _UPI_RE.search(t)
                or sum(ch.isdigit() for ch in t) >= 6)


# What a stranger sees when a person has no usable name: an owner (on a listing or a
# request card) vs a guest (on the owner's request list).
OWNER_FALLBACK, GUEST_FALLBACK = "A GharSe owner", "A neighbour"


def _first_name(row, fallback=GUEST_FALLBACK):
    """The only name a stranger sees: the first word of the person's display name — from
    the account, then the helper / customer profile, and the login handle only as a last
    resort and never when it looks like a phone number, an email or a UPI ID. Never the
    name as on the ID (users.kyc_name): that is a KYC field and stays with the team."""
    for key in ("full_name", "pname", "cname", "username"):
        try:
            v = (row[key] or "").strip()
        except (KeyError, IndexError, TypeError):
            v = ""
        if not v:
            continue
        first = v.split()[0][:30]
        if _looks_like_contact(v) or _looks_like_contact(first):
            continue
        return first
    return fallback


def _badge_row(row):
    """kyc.badge reads kyc_status / phone_verified / kyc_method from whatever it is given."""
    return kyc.badge({"kyc_status": row["kyc_status"], "phone_verified": row["phone_verified"],
                      "kyc_method": row["kyc_method"]})


def _contact(user_id, fallback=GUEST_FALLBACK):
    """Name, phone and side for a message or for the accepted party. The phone lives on
    the account, or on the helper / customer profile for older accounts. The name is the
    display name, then the login handle — never the name as on the ID (users.kyc_name)."""
    rows = q("SELECT u.id, u.username, u.full_name, u.phone, u.role, u.suspended, "
             "u.kyc_status, u.phone_verified, "
             "p.phone pphone, p.owner_name pname, p.upi_id, c.phone cphone, c.full_name cname "
             "FROM users u LEFT JOIN providers p ON p.id = u.provider_id "
             "LEFT JOIN customers c ON c.id = u.customer_id WHERE u.id=?", (user_id,))
    if not rows:
        return {"name": "A GharSe member", "first": fallback, "phone": "", "role": "user",
                "username": "", "upi": "", "row": {}}
    r = dict(rows[0])
    return {
        "row": r,
        "name": r["full_name"] or r["pname"] or r["cname"] or r["username"] or "A GharSe member",
        "first": _first_name(r, fallback),
        "phone": (r["phone"] or r["pphone"] or r["cphone"] or "").strip(),
        "role": r["role"] if r["role"] in ("provider", "customer") else "user",
        "username": r["username"] or "",
        "upi": (r["upi_id"] or "").strip(),
    }


def _tell(user_id, kind, body, ref=None):
    """WhatsApp the account (or queue it in the Outbox when there is no number)."""
    who = _contact(user_id)
    notify(who["phone"], who["name"], who["role"], kind, body, order_id=ref)


def _as_date(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _avail_label(v):
    d = _as_date(v)
    if d is None or d <= today_ist():
        return "Available now"
    return f"Available from {d.strftime('%d %b %Y')}"


def _contact_problem(*texts):
    """True when the text seems to carry a phone number, an email or a UPI ID. Best effort:
    someone set on getting round it can, but the common dodges are caught."""
    for t in texts:
        t = t or ""
        if (_PHONE_RE.search(t) or _LANDLINE_RE.search(t) or _EMAIL_RE.search(t)
                or _UPI_RE.search(t)):
            return True
        # "9876five00001", "nine eight seven six …"
        spelled = _DIGIT_WORD_RE.sub(lambda m: _DIGIT_WORDS[m.group(0).lower()], t)
        if spelled != t and (re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", spelled)
                             or _SPELLED_PHONE_RE.search(spelled)):
            return True
        # "priya at gmail dot com", "priya (at) okaxis"
        joined = _DOT_RE.sub(".", _AT_RE.sub("@", t))
        if joined != t and (_EMAIL_RE.search(joined) or _UPI_HANDLE_RE.search(joined)):
            return True
    return False


def _upi_link(amount, note):
    pa = (setting("platform_upi", str) or "").strip()
    if not pa:
        return None
    brand = setting("brand_name", str) or "GharSe"
    return (f"upi://pay?pa={quote(pa)}&pn={quote(brand)}&am={float(amount):.2f}"
            f"&cu=INR&tn={quote(note[:40])}")


def _pill(text, cls=""):
    return f"<span class='pill {cls}'>{h(text)}</span>"


_LISTING_PILL = {ACTIVE: "ok", PAUSED: "flat", LET: "", HOLD: "warn", REMOVED: "warn"}
_REQUEST_PILL = {REQUESTED: "", ACCEPTED: "ok", DECLINED: "flat", WITHDRAWN: "flat",
                 CANCELLED: "warn", CLOSED: "flat"}
_PAY_PILL = {UNPAID: "warn", CLAIMED: "", PAID: "ok", RELEASED: "ok", PAID_OUT: "ok",
             REFUND_CHECK: "warn", REFUND_DUE: "warn", REFUNDED: "flat"}
_REQUEST_WORDS = {REQUESTED: "Visit requested", ACCEPTED: "Accepted", DECLINED: "Declined",
                  WITHDRAWN: "Withdrawn", CANCELLED: "Cancelled by owner", CLOSED: "Closed"}
_PAY_WORDS = {UNPAID: "Booking amount unpaid", CLAIMED: "Payment being confirmed",
              PAID: "Booking paid", RELEASED: "Booking paid", PAID_OUT: "Booking paid",
              REFUND_CHECK: "Refund being checked",
              REFUND_DUE: "Refund due", REFUNDED: "Refunded"}


def _listing_pill(status):
    return _pill(status or ACTIVE, _LISTING_PILL.get(status, ""))


def _request_pill(status):
    return _pill(_REQUEST_WORDS.get(status, status or REQUESTED), _REQUEST_PILL.get(status, ""))


def _pay_pill(status):
    if not status:
        return ""
    return _pill(_PAY_WORDS.get(status, status), _PAY_PILL.get(status, ""))


def _split_markup(base, platform_fee, ops_fee, total, viewer):
    """All four figures, itemised. viewer: 'guest' | 'owner' | 'admin'."""
    plat, ops = setting("platform_pct", float), setting("ops_pct", float)
    to_owner = "you receive" if viewer == "owner" else "goes to the owner"
    pays = "You pay" if viewer == "guest" else "Guest pays"
    return (f"<div class='split'>Booking amount <b>{_money(base)}</b> ({to_owner}) &nbsp;·&nbsp; "
            f"GharSe platform fee ({plat:g}%) {_money(platform_fee)} &nbsp;·&nbsp; "
            f"Payment processing ({ops:g}%) {_money(ops_fee)} &nbsp;·&nbsp; "
            f"{pays} <b>{_money(total)}</b></div>")


def _row_split(r, viewer):
    return _split_markup(r["booking_amount"], r["platform_fee"], r["ops_fee"], r["customer_total"],
                         viewer)


def _user_kyc(user_id):
    rows = q("SELECT kyc_status, kyc_method, phone_verified FROM users WHERE id=?", (user_id,))
    return dict(rows[0]) if rows else {}


def _private_details(rental_id):
    rows = q("SELECT address, owner_user_id FROM rentals WHERE id=?", (rental_id,))
    return dict(rows[0]) if rows else {"address": None, "owner_user_id": None}


def _contact_block(label, person, wa_text, key, address=None):
    """The details that are shared once — and only while — a visit request is accepted."""
    parts = []
    if address:
        parts.append(f"<b>Address:</b> {h(address)}")
    phone = person["phone"]
    parts.append(f"<b>{h(label)}:</b> {h(person['first'])} · "
                 + (h(phone) if phone else "<span class='muted'>no phone number on the account yet</span>"))
    st.markdown("<br>".join(parts), unsafe_allow_html=True)
    link = wa_link(phone, wa_text) if phone else None
    if link:
        st.link_button("WhatsApp", link, key=key)


# ----------------------------------------------------------------------------- page
def rentals_home(user):
    st.markdown(
        f"<div class='page-title'>{h(RENTALS_PAGE_TITLE)}</div>"
        "<div class='page-sub'>A room, a PG bed or a flat share a few streets away — listed by "
        "identity-verified neighbours. Asking to visit is free, and the exact address and phone "
        "numbers are shared only once the owner says yes.</div>",
        unsafe_allow_html=True,
    )
    uid = _uid(user)
    if uid is None:
        st.info("Sign in to find or list a room.")
        return
    _flash(FLASH)
    tabs = st.tabs(["Find a place", "List a place", "My listings", "My requests"])
    with tabs[0]:
        _find_tab(user, uid)
    with tabs[1]:
        _list_tab(user, uid)
    with tabs[2]:
        _my_listings_tab(user, uid)
    with tabs[3]:
        _my_requests_tab(user, uid)


# ----------------------------------------------------------------------------- find
def _find_tab(user, uid):
    safety.disclaimer("rental")
    st.write("")
    fc = st.columns([1, 1, 1])
    area = fc[0].selectbox("Area", ["Any area"] + AREAS, key="rf_area")
    kinds = fc[1].multiselect("Type of place", RENTAL_KINDS, key="rf_kind", placeholder="Any type")
    sharing = fc[2].multiselect("Sharing", SHARING, key="rf_sharing", placeholder="Any sharing")
    fc = st.columns([1, 1, 1, 1])
    genders = fc[0].multiselect("Who it's for", GENDER_PREF, key="rf_gender", placeholder="Anyone")
    rent_min = fc[1].number_input("Min rent (₹/month)", 0, 1_000_000, 0, step=500, key="rf_min")
    rent_max = fc[2].number_input("Max rent (₹/month, 0 = any)", 0, 1_000_000, 0, step=500, key="rf_max")
    amen = fc[3].multiselect("Must have", AMENITIES, key="rf_amen", placeholder="No must-haves")

    sql = ("SELECT r.id, r.owner_user_id, r.kind, r.title, r.description, r.area, r.rent, r.deposit, "
           "r.available_from, r.sharing, r.gender_pref, r.amenities, r.photo, r.created_at, "
           f"{_USER_COLS} FROM rentals r JOIN users u ON u.id = r.owner_user_id "
           "WHERE r.status=? AND COALESCE(u.suspended,0)=0 "
           "AND NOT EXISTS (SELECT 1 FROM blocks b WHERE (b.user_id=? AND b.blocked_user_id=r.owner_user_id) "
           "OR (b.user_id=r.owner_user_id AND b.blocked_user_id=?))")
    params = [ACTIVE, uid, uid]
    if area != "Any area":
        sql += " AND r.area=?"
        params.append(area)
    sql += " ORDER BY r.id DESC"
    rows = []
    for r in q(sql, params):
        r = dict(r)
        mine = r["owner_user_id"] == uid
        # Only identity-verified owners are shown — a verification revoked since listing
        # takes the room off the board until it is sorted out.
        if not mine and not kyc.is_verified(r):
            continue
        if kinds and r["kind"] not in kinds:
            continue
        if sharing and r["sharing"] not in sharing:
            continue
        if genders and r["gender_pref"] not in genders:
            continue
        rent = _num(r["rent"])
        if rent < rent_min or (rent_max and rent > rent_max):
            continue
        if amen and not set(amen).issubset(set(csv_split(r["amenities"]))):
            continue
        rows.append(r)

    if not rows:
        st.info("No places match yet. Try another area or fewer must-haves — new rooms are listed "
                "by neighbours every week. Have one yourself? List it free under **List a place**.")
        return
    st.caption(f"{len(rows)} place{'s' if len(rows) != 1 else ''} open for visits.")
    open_reqs = {r["rental_id"]: dict(r) for r in q(
        "SELECT rental_id, id, status FROM rental_requests WHERE user_id=? AND status IN (?,?)",
        (uid, REQUESTED, ACCEPTED))}
    on_hold = safety.is_suspended(uid)
    for r in rows[:60]:
        _listing_card(user, uid, r, open_reqs.get(r["id"]), on_hold)
    if len(rows) > 60:
        st.caption("Showing the 60 newest. Narrow the filters to see the rest.")


def _listing_card(user, uid, r, my_req, on_hold):
    mine = r["owner_user_id"] == uid
    with st.container(border=True):
        cols = st.columns([1, 2]) if r["photo"] else [None, st.container()]
        if r["photo"]:
            with cols[0]:
                safe_image(r["photo"], width="stretch")
        amen = " ".join(_pill(a, "flat") for a in csv_split(r["amenities"]))
        desc = (r["description"] or "").strip()
        cols[1].markdown(
            f"<b>{h(r['title'])}</b> " + (_pill("Your listing") if mine else "") + "<br>"
            f"<span class='muted'>{h(r['kind'])} · {h(r['area'])} · {h(r['sharing'])} · "
            f"{h(r['gender_pref'])} · {h(_avail_label(r['available_from']))}</span>"
            f"<div class='price'>{inr(_num(r['rent']))}<span class='muted'> / month</span></div>"
            f"<span class='muted'>Deposit {inr(_num(r['deposit']))}</span><br>"
            + (f"<span class='muted'>{h(desc[:600])}</span><br>" if desc else "")
            + (amen + "<br>" if amen else "")
            + f"<span class='muted'>Listed by {h(_first_name(r, OWNER_FALLBACK))}</span> {_badge_row(r)}",
            unsafe_allow_html=True,
        )
        if mine:
            st.caption("This is how neighbours see your listing — manage it under My listings.")
            return
        if my_req:
            st.markdown(f"{_request_pill(my_req['status'])} <span class='muted'>Follow it under "
                        "My requests.</span>", unsafe_allow_html=True)
        elif on_hold:
            st.caption("Your account is on hold while the GharSe team reviews it, so you can't "
                       "request visits right now.")
        else:
            with st.expander("Request a visit — free"):
                _request_form(user, uid, r)
        bc = st.columns([1, 1, 4])
        with bc[0]:
            safety.report_button(user, "rental", r["id"], r["owner_user_id"], key=f"rrep{r['id']}")
        with bc[1]:
            safety.block_button(user, r["owner_user_id"], key=f"rblk{r['id']}")


def _request_form(user, uid, r):
    st.markdown(f"<span class='muted'>Say a little about yourself and when you'd like to see it. "
                f"{h(_first_name(r, OWNER_FALLBACK))} sees your first name and your verification badge — your phone "
                f"number is shared only if she accepts.</span>", unsafe_allow_html=True)
    with st.form(f"rreq_form{r['id']}", border=False):
        note = st.text_area("Your note", max_chars=500, key=f"rreq_note{r['id']}",
                            placeholder="I work in Koramangala, vegetarian, looking to move in by the 1st. "
                                        "Could I visit on Saturday morning?")
        ack = st.checkbox("I'll visit before paying anything, and I'll only pay through GharSe.",
                          key=f"rreq_ack{r['id']}")
        sent = st.form_submit_button("Request a visit", type="primary")
    if not sent:
        return
    note = (note or "").strip()
    if not ack:
        st.error("Please tick the box — visiting first and paying only through GharSe keeps you safe.")
        return
    if len(note) < 10:
        st.error("Write a line or two so the owner knows who's asking.")
        return
    if _contact_problem(note):
        st.error("Leave your number, email and UPI ID out of the note — they're shared automatically "
                 "once the owner accepts your visit.")
        return
    if safety.is_suspended(uid) or safety.is_blocked(uid, r["owner_user_id"]):
        st.error("You can't request a visit to this place.")
        return
    new_id = create_request(uid, r["id"], note)
    if new_id is None:
        st.session_state[FLASH] = ("That place isn't taking requests any more, or you've already asked "
                                   "to visit it — see My requests.")
    else:
        st.session_state[FLASH] = (f"Visit requested. {h(_first_name(r, OWNER_FALLBACK))} has been told; when she accepts "
                                   "you'll see the address and her number under My requests.")
    st.rerun()


def create_request(uid, rental_id, note):
    """One open request per person per place, and only on a live listing someone else owns."""
    new_id = execute(
        "INSERT INTO rental_requests (rental_id, user_id, note, status, created_at) "
        "SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM rentals WHERE id=? AND status=? AND owner_user_id<>?) "
        "AND NOT EXISTS (SELECT 1 FROM rental_requests WHERE rental_id=? AND user_id=? AND status IN (?,?)) "
        "RETURNING id",
        (rental_id, uid, note, REQUESTED, now_iso(), rental_id, ACTIVE, uid,
         rental_id, uid, REQUESTED, ACCEPTED))
    if new_id is not None:
        rows = q("SELECT owner_user_id, title, kind FROM rentals WHERE id=?", (rental_id,))
        if rows:
            guest = _contact(uid)
            _tell(rows[0]["owner_user_id"], "Visit request",
                  f"{guest['first']} would like to visit your {rows[0]['kind']} “{rows[0]['title']}”. "
                  "Open Rooms & PGs → My listings on GharSe to accept or decline.", ref=rental_id)
    return new_id


# ----------------------------------------------------------------------------- list
def _listing_form(key, existing, submit_label):
    """The listing form, for a new place or an edit. Returns cleaned values, or None."""
    ex = existing or {}
    with st.form(key):
        c = st.columns(2)
        kind = c[0].selectbox("Type of place", RENTAL_KINDS, idx(RENTAL_KINDS, ex.get("kind")),
                              key=f"{key}_kind")
        area = c[1].selectbox("Area", AREAS, idx(AREAS, ex.get("area")), key=f"{key}_area")
        title = st.text_input("Title", ex.get("title") or "", max_chars=80, key=f"{key}_title",
                              placeholder="Sunny twin-sharing room near Agara lake, food included")
        desc = st.text_area("Description", ex.get("description") or "", max_chars=1200,
                            key=f"{key}_desc",
                            placeholder="What the room is like, the house rules, who else lives there, "
                                        "the nearest bus stop or metro.")
        c = st.columns(3)
        rent = c[0].number_input("Rent (₹ / month)", 0, 1_000_000, _clamped(ex.get("rent"), 1_000_000),
                                 step=500, key=f"{key}_rent")
        deposit = c[1].number_input("Deposit (₹)", 0, 5_000_000, _clamped(ex.get("deposit"), 5_000_000),
                                    step=1000, key=f"{key}_dep")
        avail_default = _as_date(ex.get("available_from")) or today_ist()
        avail = c[2].date_input("Available from", avail_default, key=f"{key}_avail")
        c = st.columns(2)
        sharing = c[0].selectbox("Sharing", SHARING, idx(SHARING, ex.get("sharing")), key=f"{key}_share")
        gender = c[1].selectbox("Who it's for", GENDER_PREF, idx(GENDER_PREF, ex.get("gender_pref")),
                                key=f"{key}_gender")
        amen = st.multiselect("Amenities", AMENITIES,
                              [a for a in csv_split(ex.get("amenities")) if a in AMENITIES],
                              key=f"{key}_amen")
        address = st.text_area("Exact address (private)", ex.get("address") or "", max_chars=300,
                               key=f"{key}_addr",
                               help="Never shown on the listing. Shared only with someone whose "
                                    "visit request you accept.")
        photo = st.file_uploader(("Replace the photo" if ex.get("photo") else "Photo of the room")
                                 + f" (JPG or PNG, up to {MAX_IMG_MB} MB)",
                                 type=["jpg", "jpeg", "png", "webp"], key=f"{key}_photo")
        sent = st.form_submit_button(submit_label, type="primary")
    if not sent:
        return None
    title, desc, address = (title or "").strip(), (desc or "").strip(), (address or "").strip()
    errors = []
    if len(title) < 8:
        errors.append("Give it a title of at least a few words — what and where.")
    if rent <= 0:
        errors.append("Enter the monthly rent.")
    if deposit < 0:
        errors.append("The deposit can't be negative.")
    if len(address) < 10:
        errors.append("Add the exact address — it stays private until you accept a visit.")
    if _contact_problem(title, desc):
        errors.append("Keep your phone number, email and UPI ID out of the title and description — "
                      "they're shared automatically once you accept a visit.")
    img = None
    if photo is not None:
        if getattr(photo, "size", 0) > MAX_IMG_MB * 1024 * 1024:
            errors.append(f"That photo is over {MAX_IMG_MB} MB — pick a smaller one.")
        else:
            img = process_image(photo)
            if img is None:
                errors.append("That file isn't a photo we can read. Upload a JPG or PNG of the room.")
    if errors:
        for e in errors:
            st.error(e)
        return None
    return {"kind": kind, "title": title, "description": desc, "area": area, "rent": float(rent),
            "deposit": float(deposit), "available_from": avail.isoformat() if avail else None,
            "sharing": sharing, "gender_pref": gender, "amenities": csv_join(amen),
            "address": address, "photo": img}


def _list_tab(user, uid):
    if safety.is_suspended(uid):
        st.warning("Your account is on hold while the GharSe team reviews some reports, so you can't "
                   "list a place right now. The team will get back to you within 24 hours.")
        return
    if not kyc.require(user, "list a room"):
        return
    st.markdown("<span class='muted'>Listing is free. Neighbours see the room, the rent, your first "
                "name and your verification badge — never your address or number. You decide who "
                "visits, and only then are the address and both numbers shared.</span>",
                unsafe_allow_html=True)
    n = st.session_state.get("rl_new_n", 0)
    vals = _listing_form(f"rl_new_{n}", None, "List my place")
    if vals is None:
        return
    new_id = execute(
        "INSERT INTO rentals (owner_user_id, kind, title, description, area, rent, deposit, "
        "available_from, sharing, gender_pref, amenities, address, photo, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, vals["kind"], vals["title"], vals["description"], vals["area"], vals["rent"],
         vals["deposit"], vals["available_from"], vals["sharing"], vals["gender_pref"],
         vals["amenities"], vals["address"], vals["photo"], ACTIVE, now_iso()))
    st.session_state["rl_new_n"] = n + 1
    st.session_state[FLASH] = (f"“{h(vals['title'])}” is live under Find a place"
                               + ("." if vals["photo"] else " — add a photo from My listings; rooms with "
                                  "a photo get far more requests.")
                               if new_id else "Listed.")
    st.rerun()


# ----------------------------------------------------------------------------- my listings
def _owner_rental(rental_id, uid):
    rows = q("SELECT * FROM rentals WHERE id=? AND owner_user_id=?", (rental_id, uid))
    return dict(rows[0]) if rows else None


def set_listing_status(rental_id, uid, new_status, from_statuses):
    """Owner-side state change on her own listing. Returns the id, or None if it had moved on."""
    marks = ",".join("?" * len(from_statuses))
    return execute(f"UPDATE rentals SET status=? WHERE id=? AND owner_user_id=? AND status IN ({marks}) "
                   "RETURNING id", (new_status, rental_id, uid, *from_statuses))


def _close_open_requests(rental_id, why, refund_paid, keep=None):
    """Close what's still open on a listing that is no longer available.

    Booking money with GharSe (Claimed or Paid) is flagged for refund when refund_paid, or
    — when keep is given — on every request NOT in keep (keep = the booking(s) the place
    was actually let to). Otherwise a request with money in it is left alone. A confirmed
    payment becomes Refund due; an unconfirmed claim becomes Refund check, so the team
    checks whether the money really arrived before refunding anything."""
    rows = q("SELECT id, user_id, payment_status FROM rental_requests WHERE rental_id=? AND status IN (?,?)",
             (rental_id, REQUESTED, ACCEPTED))
    title = (q("SELECT title FROM rentals WHERE id=?", (rental_id,)) or [{"title": "the place"}])[0]["title"]
    keep = None if keep is None else {int(k) for k in keep}
    for r in rows:
        if r["payment_status"] in (CLAIMED, PAID):
            refund = refund_paid or (keep is not None and r["id"] not in keep)
            if not refund:
                continue
            to = REFUND_DUE if r["payment_status"] == PAID else REFUND_CHECK
            done = execute("UPDATE rental_requests SET status=?, payment_status=? WHERE id=? "
                           "AND status IN (?,?) AND payment_status=? RETURNING id",
                           (CLOSED, to, r["id"], REQUESTED, ACCEPTED, r["payment_status"]))
            extra = " The booking amount you paid will be refunded by the GharSe team."
        elif r["payment_status"] in (PAID_OUT, RELEASED):
            continue
        else:
            done = execute("UPDATE rental_requests SET status=?, payment_status=NULL WHERE id=? "
                           "AND status IN (?,?) AND (payment_status IS NULL OR payment_status=?) RETURNING id",
                           (CLOSED, r["id"], REQUESTED, ACCEPTED, UNPAID))
            extra = ""
        if done:
            _tell(r["user_id"], "Room no longer available",
                  f"“{title}” is no longer available ({why}), so your request has been closed.{extra} "
                  "There are more places under Rooms & PGs on GharSe.", ref=rental_id)


def end_tenancy(request_id, owner_uid, why="the owner has marked your stay as over"):
    """Accepted and paid → Closed: the guest has moved out. Only the owner, only on her own
    listing while it is live, paused or let (not while the team has it on hold), and only
    for a booking whose money is confirmed. The money state is kept, so a payout still due
    is still paid; contact details stop being shared, and the guest is told."""
    done = execute(
        "UPDATE rental_requests SET status=? WHERE id=? AND status=? AND payment_status IN (?,?,?) "
        "AND rental_id IN (SELECT id FROM rentals WHERE owner_user_id=? AND status IN (?,?,?)) RETURNING id",
        (CLOSED, request_id, ACCEPTED, *_PAID_IN, owner_uid, ACTIVE, PAUSED, LET))
    if done:
        x = q("SELECT rr.user_id, r.title FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
              "WHERE rr.id=?", (request_id,))[0]
        _tell(x["user_id"], "Stay over",
              f"Your booking for “{x['title']}” is closed — {why}. Contact details are no longer shared. "
              "If that isn't right, reply to the GharSe team.", ref=request_id)
    return done


def _finish_tenancies(rental_id, owner_uid):
    """A let SINGLE place relisted means its tenancy is over: the accepted, paid booking is
    closed through end_tenancy (so the guest is told), which frees the place for its next
    booking. A shared place is never closed this way — a PG with one bed free is still home
    to the others — so its owner ends a tenancy one request at a time. Returns how many
    were closed."""
    rows = q(f"SELECT id FROM rental_requests WHERE rental_id=? AND rental_id IN ({_SINGLE_PLACES}) "
             "AND status=? AND payment_status IN (?,?,?)", (rental_id, ACCEPTED, *_PAID_IN))
    return sum(1 for r in rows if end_tenancy(r["id"], owner_uid, "the owner has relisted the place"))


def _paid_open_requests(rental_id):
    return [dict(x) for x in q(
        f"SELECT rr.id, rr.payment_status, rr.booking_amount, {_USER_COLS} FROM rental_requests rr "
        "JOIN users u ON u.id = rr.user_id WHERE rr.rental_id=? AND rr.status IN (?,?) "
        "AND rr.payment_status IN (?,?) ORDER BY rr.id", (rental_id, REQUESTED, ACCEPTED, CLAIMED, PAID))]


def _is_single_place(r):
    return r.get("kind") == "Whole flat" or r.get("sharing") == "Private"


def mark_let(rental_id, uid, let_to=None):
    """Active/Paused → Let. Visit requests nobody paid on are closed. let_to: the request
    ids the place was let to — every OTHER request with booking money is flagged for a
    refund. None keeps every paid booking (there is at most one, or the owner didn't say)."""
    if not set_listing_status(rental_id, uid, LET, (ACTIVE, PAUSED)):
        return None
    _close_open_requests(rental_id, "the owner has let it", refund_paid=False, keep=let_to)
    return rental_id


def _mark_let_control(uid, r):
    rid = r["id"]
    paid = _paid_open_requests(rid)
    if len(paid) <= 1:
        if st.button("Mark let", key=f"rl_let{rid}", width="stretch",
                     help="Take it off the board and close visit requests nobody has paid on."):
            if mark_let(rid, uid):
                st.session_state[FLASH] = f"Congratulations — “{h(r['title'])}” is marked as let."
            st.rerun()
        return
    single = _is_single_place(r)
    with st.popover("Mark let", width="stretch"):
        st.caption(f"{len(paid)} people have paid a booking amount through GharSe. Pick who you've let "
                   "it to — everyone else's booking amount is refunded to them by the GharSe team.")
        labels = {x["id"]: f"{_first_name(x)} · {_money(x['booking_amount'])} · request #{x['id']}"
                  for x in paid}
        with st.form(f"rl_letf{rid}", border=False):
            if single:
                pick = st.radio("Let to", list(labels), format_func=labels.get, key=f"rl_letto{rid}")
                chosen = [pick] if pick is not None else []
            else:
                chosen = st.multiselect("Let to", list(labels), default=list(labels),
                                        format_func=labels.get, key=f"rl_letto{rid}")
            go = st.form_submit_button("Mark let", type="primary")
        if go:
            if not chosen:
                st.error("Pick who you've let it to.")
            elif mark_let(rid, uid, let_to=chosen):
                st.session_state[FLASH] = f"Congratulations — “{h(r['title'])}” is marked as let."
                st.rerun()
            else:
                st.error("That listing has already changed — refresh the page.")


def _mod_reason(rental_id):
    rows = q("SELECT body FROM notifications WHERE order_id=? AND kind IN (?,?) ORDER BY id DESC LIMIT 1",
             (rental_id, *_MOD_KINDS))
    return rows[0]["body"] if rows else None


def _my_listings_tab(user, uid):
    rows = [dict(r) for r in q("SELECT * FROM rentals WHERE owner_user_id=? ORDER BY id DESC", (uid,))]
    if not rows:
        st.info("You haven't listed a place yet. It's free — use **List a place**.")
        return
    counts = {r["rental_id"]: r["c"] for r in q(
        "SELECT rr.rental_id, COUNT(*) c FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
        "WHERE r.owner_user_id=? AND rr.status=? GROUP BY rr.rental_id", (uid, REQUESTED))}
    c = st.columns(3)
    kpi(c[0], "Live listings", f"{sum(1 for r in rows if r['status'] == ACTIVE)}")
    kpi(c[1], "Visit requests waiting", f"{sum(counts.values())}")
    paid = q("SELECT COALESCE(SUM(rr.booking_amount),0) s FROM rental_requests rr JOIN rentals r "
             "ON r.id = rr.rental_id WHERE r.owner_user_id=? AND rr.payment_status IN (?,?,?)",
             (uid, *_PAID_IN))[0]["s"]
    kpi(c[2], "Booking money earned", inr(paid))
    st.write("")
    for r in rows:
        _my_listing_card(user, uid, r, counts.get(r["id"], 0))


def _my_listing_card(user, uid, r, waiting):
    rid, status = r["id"], r["status"] or ACTIVE
    with st.container(border=True):
        cols = st.columns([1, 2]) if r["photo"] else [None, st.container()]
        if r["photo"]:
            with cols[0]:
                safe_image(r["photo"], width="stretch")
        cols[1].markdown(
            f"<b>{h(r['title'])}</b> {_listing_pill(status)}"
            + (f" {_pill(f'{waiting} waiting', 'warn')}" if waiting else "") + "<br>"
            f"<span class='muted'>{h(r['kind'])} · {h(r['area'])} · {h(r['sharing'])} · "
            f"{h(r['gender_pref'])} · {h(_avail_label(r['available_from']))}</span>"
            f"<div class='price'>{inr(_num(r['rent']))}<span class='muted'> / month</span></div>"
            f"<span class='muted'>Deposit {inr(_num(r['deposit']))}</span><br>"
            f"<span class='muted'>Address (private — shared only with visitors you accept): "
            f"{h(r['address'])}</span>",
            unsafe_allow_html=True,
        )
        if status in (HOLD, REMOVED):
            why = _mod_reason(rid)
            st.warning(("The GharSe team has put this listing on hold — it's hidden until they restore "
                        "it." if status == HOLD else "The GharSe team removed this listing.")
                       + (f" {h(why)}" if why else ""))

        b = st.columns(4)
        if status == ACTIVE and b[0].button("Pause", key=f"rl_pause{rid}", width="stretch",
                                            help="Hide it from Find a place. Requests already made stay."):
            if set_listing_status(rid, uid, PAUSED, (ACTIVE,)):
                st.session_state[FLASH] = f"Paused “{h(r['title'])}”. Resume it whenever you're ready."
            st.rerun()
        if status in (PAUSED, LET):
            label = "Resume" if status == PAUSED else "Relist"
            single = _is_single_place(r)
            tip = (None if status == PAUSED else
                   "Back on Find a place. The current tenant's booking is closed and they're told."
                   if single else
                   "Back on Find a place. Your current tenants' bookings stay as they are.")
            if b[0].button(label, key=f"rl_resume{rid}", width="stretch", help=tip):
                if safety.is_suspended(uid):
                    st.session_state[FLASH] = "Your account is on hold, so the listing can't go live yet."
                elif not kyc.is_verified(_user_kyc(uid)):
                    st.session_state[FLASH] = ("Your identity needs to be verified again before the "
                                               f"listing can go live — open {kyc.KYC_PAGE_TITLE}.")
                elif set_listing_status(rid, uid, ACTIVE, (status,)):
                    msg = f"“{h(r['title'])}” is live again."
                    if status == LET:
                        # Only a single place has one tenancy to end; a shared place keeps
                        # everyone who still lives there (each has a "Stay over" button).
                        if _finish_tenancies(rid, uid):
                            msg += " The last tenant's booking is closed and they've been told."
                        elif not single:
                            msg += (" Your current tenants' bookings stay as they are — use “Stay over” "
                                    "on a request when someone moves out.")
                    st.session_state[FLASH] = msg
                st.rerun()
        if status in (ACTIVE, PAUSED):
            with b[1]:
                _mark_let_control(uid, r)

        if status != REMOVED:
            with st.expander("Edit listing"):
                vals = _listing_form(f"rl_edit{rid}", r, "Save changes")
                if vals is not None:
                    done = execute(
                        "UPDATE rentals SET kind=?, title=?, description=?, area=?, rent=?, deposit=?, "
                        "available_from=?, sharing=?, gender_pref=?, amenities=?, address=?, "
                        "photo=COALESCE(?, photo) WHERE id=? AND owner_user_id=? AND status<>? RETURNING id",
                        (vals["kind"], vals["title"], vals["description"], vals["area"], vals["rent"],
                         vals["deposit"], vals["available_from"], vals["sharing"], vals["gender_pref"],
                         vals["amenities"], vals["address"], vals["photo"], rid, uid, REMOVED))
                    st.session_state[FLASH] = ("Saved your changes." if done else
                                               "That listing can't be edited any more.")
                    st.rerun()

        reqs = [dict(x) for x in q(
            f"SELECT rr.*, {_USER_COLS} FROM rental_requests rr JOIN users u ON u.id = rr.user_id "
            "WHERE rr.rental_id=? ORDER BY rr.id DESC", (rid,))]
        live = [x for x in reqs if x["status"] in OPEN_REQUEST or x["payment_status"] in _MONEY_LIVE]
        past = [x for x in reqs if x not in live]
        if live:
            st.markdown("**Visit requests**")
            for x in live:
                _incoming_request(user, uid, r, x)
        elif status == ACTIVE:
            st.caption("No visit requests yet.")
        if past:
            with st.expander(f"Past requests ({len(past)})"):
                for x in past:
                    st.markdown(f"{h(_first_name(x))} · {_request_pill(x['status'])} {_pay_pill(x['payment_status'])}"
                                f" <span class='muted'>{h(short_dt(x['created_at']))}</span>",
                                unsafe_allow_html=True)


def _owner_standing(uid):
    """None when the owner may accept visits and take booking money, else why not."""
    if safety.is_suspended(uid):
        return ("Your account is on hold while the GharSe team reviews it, so you can't accept "
                "visits or take booking amounts right now. Contact details stay hidden until "
                "it's sorted out.")
    if not kyc.is_verified(_user_kyc(uid)):
        return ("Your identity needs to be verified again before you can accept visits or take "
                f"booking amounts — open {kyc.KYC_PAGE_TITLE}.")
    return None


def _incoming_request(user, uid, rental, x):
    xid = x["id"]
    with st.container(border=True):
        st.markdown(
            f"<b>{h(_first_name(x))}</b> {_badge_row(x)} {_request_pill(x['status'])} "
            f"{_pay_pill(x['payment_status'])}<br>"
            f"<span class='muted'>{h(short_dt(x['created_at']))}</span><br>{h(x['note'] or '')}",
            unsafe_allow_html=True,
        )
        blocked = safety.is_blocked(uid, x["uid"])
        held = safety.is_suspended(x["uid"])
        standing = _owner_standing(uid)
        if x["status"] == REQUESTED:
            if standing:
                st.caption(standing)
            elif blocked or held:
                st.caption("You can't accept this request — "
                           + ("one of you has blocked the other." if blocked else
                              "their account is on hold while the GharSe team reviews it."))
            b = st.columns([1, 1, 3])
            can_accept = (rental["status"] in (ACTIVE, PAUSED) and not blocked and not held
                          and standing is None)
            if can_accept and b[0].button("Accept", key=f"rr_acc{xid}", type="primary",
                                          width="stretch",
                                          help="Shares the exact address and both phone numbers."):
                st.session_state[FLASH] = (
                    f"Accepted {h(_first_name(x))}. Their number is below, and they now have the address "
                    "and your number." if accept_request(xid, uid) else
                    "That request can't be accepted any more — it was answered, or one of you can't "
                    "take visits right now.")
                st.rerun()
            if b[1].button("Decline", key=f"rr_dec{xid}", width="stretch"):
                st.session_state[FLASH] = (f"Declined {h(_first_name(x))}'s request — they've been told kindly."
                                           if decline_request(xid, uid) else
                                           "That request has already been answered.")
                st.rerun()
        elif x["status"] == ACCEPTED:
            # Contact details are shared only while BOTH sides are in good standing with
            # each other — a block or a hold on either account hides them on both sides, and
            # so does the team holding the listing (the guest's side hides the owner's too).
            if blocked:
                st.caption("One of you has blocked the other, so contact details are hidden.")
            elif held:
                st.caption("Their account is on hold while the GharSe team reviews it, so contact "
                           "details are hidden for now.")
            elif standing:
                st.caption(standing)
            elif rental["status"] in (HOLD, REMOVED):
                st.caption("The GharSe team has put this listing on hold, so contact details are hidden "
                           "until it's restored." if rental["status"] == HOLD else
                           "The GharSe team removed this listing, so contact details are hidden.")
            else:
                guest = _contact(x["uid"])
                _contact_block("Guest", guest,
                               f"Hi {guest['first']}, this is about your GharSe visit request for "
                               f"“{rental['title']}”.", key=f"rr_wa{xid}")
            _owner_money(uid, rental, x, can_ask=standing is None and not blocked and not held)
            ps = x["payment_status"]
            if ps not in (PAID_OUT, RELEASED):
                with st.popover("Cancel this booking"):
                    st.caption("Use this if you're not going ahead with them. Any booking amount they "
                               "paid through GharSe is refunded to them.")
                    if st.button("Yes, cancel it", key=f"rr_cancel{xid}"):
                        st.session_state[FLASH] = ("Cancelled — they've been told."
                                                   if owner_cancel(xid, uid) else
                                                   "That booking had already changed.")
                        st.rerun()
            if ps in _PAID_IN and rental["status"] in (ACTIVE, PAUSED, LET):
                with st.popover("Stay over"):
                    st.caption("Use this once they've moved out. Their booking is closed and contact "
                               "details stop being shared; nothing is refunded, and anything GharSe "
                               "still owes you is still paid. They're told.")
                    if st.button("Yes, they've moved out", key=f"rr_end{xid}"):
                        st.session_state[FLASH] = (f"Closed {h(_first_name(x))}'s booking — they've been told."
                                                   if end_tenancy(xid, uid) else
                                                   "That booking had already changed.")
                        st.rerun()
        elif x["payment_status"] in _MONEY_LIVE:
            st.markdown(_row_split(x, "owner"), unsafe_allow_html=True)
        if not blocked:
            safety.block_button(user, x["uid"], key=f"rr_blk{xid}")


def _owner_money(uid, rental, x, can_ask=True):
    ps = x["payment_status"]
    if ps in (None, "", UNPAID):
        if ps == UNPAID:
            st.markdown(_row_split(x, "owner"), unsafe_allow_html=True)
            if can_ask:
                st.caption("Waiting for them to pay. You can change the amount until they do.")
        if not can_ask:
            return
        if _is_single_place(rental) and _other_booking_taken(rental["id"], x["id"]):
            st.caption("Someone else has already paid a booking amount for this place. Cancel their "
                       "booking first if it isn't going ahead.")
            return
        cap = _num(rental["rent"]) + _num(rental["deposit"])
        with st.form(f"rr_amt{x['id']}", border=False):
            fc = st.columns([2, 1])
            amt = fc[0].number_input(
                "Booking amount (₹) — a token or the first month, paid through GharSe", 0, int(max(cap, MIN_BOOKING)),
                _clamped(x["booking_amount"], int(max(cap, MIN_BOOKING))), step=500, key=f"rr_amtv{x['id']}",
                help="Optional. You receive exactly this; GharSe's fees are added on top for the guest.")
            go = fc[1].form_submit_button("Set booking amount" if ps != UNPAID else "Change amount")
        if go:
            if amt < MIN_BOOKING:
                st.error(f"A booking amount starts at {inr(MIN_BOOKING)}.")
            elif amt > cap:
                st.error(f"That's more than the first month and the deposit together ({inr(cap)}).")
            elif set_booking_amount(x["id"], uid, amt):
                s = split_on_top(amt)
                st.session_state[FLASH] = (f"Booking amount set: you receive {_money(s['host_payout'])}; "
                                           f"they pay {_money(s['customer_total'])} with GharSe's fees.")
                st.rerun()
            else:
                st.error("That can't be set right now — they've already paid, someone else has booked "
                         "this place, or the request has changed. Refresh the page.")
        if ps == UNPAID and st.button("Remove booking amount", key=f"rr_clear{x['id']}"):
            clear_booking_amount(x["id"], uid)
            st.rerun()
    else:
        st.markdown(_row_split(x, "owner"), unsafe_allow_html=True)
        owed = f"Paid. The GharSe team sends you {_money(x['booking_amount'])} — no need to chase anyone."
        st.caption({
            CLAIMED: "They say they've paid — the GharSe team is confirming it.",
            PAID: owed,
            RELEASED: owed,
            PAID_OUT: f"{_money(x['booking_amount'])} has been sent to you.",
            REFUND_CHECK: "This booking was called off; the GharSe team is checking the guest's payment.",
            REFUND_DUE: "This booking was called off; the guest's money is being refunded.",
            REFUNDED: "Refunded to the guest.",
        }.get(ps, ""))


def _other_booking_taken(rental_id, request_id):
    return bool(q("SELECT 1 FROM rental_requests WHERE rental_id=? AND id<>? AND status IN (?,?) "
                  "AND payment_status IN (?,?,?,?)",
                  (rental_id, request_id, REQUESTED, ACCEPTED, CLAIMED, *_PAID_IN)))


def _owner_request_guard():
    return "rental_id IN (SELECT id FROM rentals WHERE owner_user_id=?)"


def accept_request(request_id, owner_uid):
    """Requested → Accepted, only on the owner's own place while it's live or paused, only
    while the owner is verified and not on hold, only for a guest who isn't on hold, and
    only when neither has blocked the other. This is what shares the address and both
    phones, so every one of those is checked in the same statement."""
    done = execute(
        "UPDATE rental_requests SET status=? WHERE id=? AND status=? AND rental_id IN "
        "(SELECT id FROM rentals WHERE owner_user_id=? AND status IN (?,?)) "
        f"AND user_id IN ({_NOT_HELD_USERS}) AND ? IN ({_OWNER_IN_GOOD_STANDING}) "
        "AND NOT EXISTS (SELECT 1 FROM blocks b WHERE (b.user_id=? AND b.blocked_user_id=rental_requests.user_id) "
        "OR (b.user_id=rental_requests.user_id AND b.blocked_user_id=?)) RETURNING id",
        (ACCEPTED, request_id, REQUESTED, owner_uid, ACTIVE, PAUSED, owner_uid, owner_uid, owner_uid))
    if done:
        x = q("SELECT rr.user_id, r.title, r.address FROM rental_requests rr JOIN rentals r "
              "ON r.id = rr.rental_id WHERE rr.id=?", (request_id,))[0]
        owner = _contact(owner_uid, OWNER_FALLBACK)
        _tell(x["user_id"], "Visit accepted",
              f"{owner['first']} accepted your visit request for “{x['title']}”. Address: {x['address']}. "
              + (f"Her number: {owner['phone']}. " if owner["phone"] else "")
              + "Visit before you pay anything, and pay only through GharSe.", ref=request_id)
    return done


def decline_request(request_id, owner_uid):
    done = execute(f"UPDATE rental_requests SET status=? WHERE id=? AND status=? AND {_owner_request_guard()} "
                   "RETURNING id", (DECLINED, request_id, REQUESTED, owner_uid))
    if done:
        x = q("SELECT rr.user_id, r.title FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
              "WHERE rr.id=?", (request_id,))[0]
        _tell(x["user_id"], "Visit request",
              f"Sorry — the owner of “{x['title']}” can't take your visit request this time. There are "
              "more places under Rooms & PGs on GharSe.", ref=request_id)
    return done


def set_booking_amount(request_id, owner_uid, amount):
    """Set or change the booking amount while nothing has been paid on it — only by an owner
    who is verified and not on hold, and on a single place only while nobody else has a
    booking amount claimed or paid on it."""
    s = split_on_top(amount)
    done = execute(
        "UPDATE rental_requests SET booking_amount=?, platform_fee=?, ops_fee=?, customer_total=?, "
        f"payment_status=? WHERE id=? AND status=? AND (payment_status IS NULL OR payment_status=?) "
        f"AND {_owner_request_guard()} AND ? IN ({_OWNER_IN_GOOD_STANDING}) "
        f"AND {_ONE_BOOKING_PER_SINGLE_PLACE} RETURNING id",
        (s["base"], s["platform_fee"], s["ops_fee"], s["customer_total"], UNPAID, request_id, ACCEPTED,
         UNPAID, owner_uid, owner_uid))
    # Post-commit re-check: a claim on the same single place that committed while this ran
    # was invisible to the statement above. Take the amount back before the guest is asked
    # to pay a place someone else now holds (only the amount just set, only while unpaid).
    if done and execute(
            "UPDATE rental_requests SET booking_amount=NULL, platform_fee=NULL, ops_fee=NULL, "
            "customer_total=NULL, payment_status=NULL WHERE id=? AND status=? AND payment_status=? "
            f"AND ABS(customer_total - ?) < 0.005 AND {_HELD_ELSEWHERE} RETURNING id",
            (request_id, ACCEPTED, UNPAID, s["customer_total"])):
        return None
    if done:
        x = q("SELECT rr.user_id, r.title FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
              "WHERE rr.id=?", (request_id,))[0]
        _tell(x["user_id"], "Booking amount",
              f"The owner of “{x['title']}” has asked for a booking amount of {_money(s['base'])}. With "
              f"GharSe's fees you pay {_money(s['customer_total'])} — pay it from Rooms & PGs → My requests, "
              "only after you've seen the place.", ref=request_id)
    return done


def clear_booking_amount(request_id, owner_uid):
    return execute(
        "UPDATE rental_requests SET booking_amount=NULL, platform_fee=NULL, ops_fee=NULL, "
        f"customer_total=NULL, payment_status=NULL WHERE id=? AND status=? AND payment_status=? "
        f"AND {_owner_request_guard()} RETURNING id", (request_id, ACCEPTED, UNPAID, owner_uid))


# Called off: money the team CONFIRMED is owed back (Refund due); a claim nobody has
# confirmed yet goes to Refund check, where the team first sees whether it ever arrived.
_REFUND_CASE = ("CASE WHEN payment_status='Paid' THEN 'Refund due' "
                "WHEN payment_status='Claimed' THEN 'Refund check' "
                "WHEN payment_status='Unpaid' THEN NULL ELSE payment_status END")


def owner_cancel(request_id, owner_uid):
    # Paid out, or released to the owner by the team, is settled — it can't be called off.
    done = execute(
        f"UPDATE rental_requests SET status=?, payment_status={_REFUND_CASE} WHERE id=? AND status=? "
        f"AND (payment_status IS NULL OR payment_status NOT IN (?,?)) AND {_owner_request_guard()} RETURNING id",
        (CANCELLED, request_id, ACCEPTED, PAID_OUT, RELEASED, owner_uid))
    if done:
        x = q("SELECT rr.user_id, rr.payment_status, r.title FROM rental_requests rr JOIN rentals r "
              "ON r.id = rr.rental_id WHERE rr.id=?", (request_id,))[0]
        _tell(x["user_id"], "Booking cancelled",
              f"The owner of “{x['title']}” has called off your booking."
              + (" The booking amount you paid will be refunded by the GharSe team."
                 if x["payment_status"] in (REFUND_DUE, REFUND_CHECK) else ""), ref=request_id)
    return done


# ----------------------------------------------------------------------------- my requests
def claim_payment(request_id, uid, ref, expected_total):
    """Unpaid → Claimed, only for the amount the guest was actually shown — if the owner
    changed it in the meantime the claim fails instead of attaching to the wrong figure.
    Also only while the guest is not on hold, the owner is verified and not on hold, the
    listing isn't held or removed, and (for a single place) nobody else has booked it.

    Two guests claiming the same single place at once can both get past that last check on
    Postgres, so once the claim has committed _single_place_backoff runs: if this claim is
    the one that must give way, it is closed there and None is returned."""
    done = execute(
        "UPDATE rental_requests SET payment_status=?, payment_ref=? WHERE id=? AND user_id=? AND status=? "
        "AND payment_status=? AND ABS(customer_total - ?) < 0.005 "
        f"AND user_id IN ({_NOT_HELD_USERS}) AND rental_id IN "
        "(SELECT r.id FROM rentals r WHERE r.status IN (?,?,?) "
        f" AND r.owner_user_id IN ({_OWNER_IN_GOOD_STANDING})) "
        f"AND {_ONE_BOOKING_PER_SINGLE_PLACE} RETURNING id",
        (CLAIMED, ref, request_id, uid, ACCEPTED, UNPAID, float(expected_total), ACTIVE, PAUSED, LET))
    if done is not None and done in _single_place_backoff(done):
        return None
    return done


def _single_place_backoff(request_id):
    """The post-commit re-check after a claim, in the spirit of social.py's
    _over_capacity_void. On the claimed request's place, if it is a single place, every open
    claim that must give way (_GIVES_WAY) is closed as Refund check — the reference stays,
    because the guest may well have paid already, so the team matches it and refunds it —
    and that guest is told. Run by every claimer after its own commit, so whichever claim
    commits second sees both and the loser is closed; the earliest never is. Returns the
    ids it closed."""
    rows = q("SELECT rr.rental_id, r.title FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
             "WHERE rr.id=?", (request_id,))
    if not rows:
        return []
    rental_id, title = rows[0]["rental_id"], rows[0]["title"]
    closed = []
    for x in q(f"SELECT id, user_id, payment_ref FROM rental_requests WHERE rental_id=? AND status IN (?,?) "
               f"AND payment_status=? AND {_GIVES_WAY} ORDER BY id", (rental_id, REQUESTED, ACCEPTED, CLAIMED)):
        if execute(f"UPDATE rental_requests SET status=?, payment_status=? WHERE id=? AND status IN (?,?) "
                   f"AND payment_status=? AND {_GIVES_WAY} RETURNING id",
                   (CLOSED, REFUND_CHECK, x["id"], REQUESTED, ACCEPTED, CLAIMED)):
            closed.append(x["id"])
            _tell(x["user_id"], "Room already booked",
                  f"Someone else booked “{title}” a moment before you, so your booking can't go ahead and "
                  "your request is closed. If you've already paid, the GharSe team will match your payment "
                  f"(reference {x['payment_ref'] or '—'}) and refund it in full.", ref=x["id"])
    return closed


def withdraw_request(request_id, uid):
    return execute(
        f"UPDATE rental_requests SET status=?, payment_status={_REFUND_CASE} WHERE id=? AND user_id=? "
        "AND status IN (?,?) AND (payment_status IS NULL OR payment_status NOT IN (?,?)) RETURNING id",
        (WITHDRAWN, request_id, uid, REQUESTED, ACCEPTED, PAID_OUT, RELEASED))


def _my_requests_tab(user, uid):
    rows = [dict(r) for r in q(
        "SELECT rr.*, r.title, r.kind, r.sharing, r.area, r.rent, r.deposit, r.status rstatus, r.owner_user_id, "
        f"{_USER_COLS} FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
        "JOIN users u ON u.id = r.owner_user_id WHERE rr.user_id=? ORDER BY rr.id DESC", (uid,))]
    if not rows:
        st.info("You haven't asked to visit anywhere yet — browse **Find a place**.")
        return
    for x in rows:
        _my_request_card(user, uid, x)


def _my_request_card(user, uid, x):
    xid = x["id"]
    with st.container(border=True):
        st.markdown(
            f"<b>{h(x['title'])}</b> {_request_pill(x['status'])} {_pay_pill(x['payment_status'])}<br>"
            f"<span class='muted'>{h(x['kind'])} · {h(x['area'])} · {inr(_num(x['rent']))} / month · deposit "
            f"{inr(_num(x['deposit']))} · owner {h(_first_name(x, OWNER_FALLBACK))}</span> {_badge_row(x)}<br>"
            f"<span class='muted'>Your note: {h(x['note'] or '—')}</span>",
            unsafe_allow_html=True,
        )
        status, ps = x["status"], x["payment_status"]
        owner_id = x["owner_user_id"]
        blocked = safety.is_blocked(uid, owner_id)
        if status == REQUESTED:
            st.caption("Waiting for the owner. When she accepts you'll see the address and her number here.")
        elif status == ACCEPTED and blocked:
            st.caption("One of you has blocked the other, so contact details are hidden.")
        elif status == ACCEPTED:
            # The address and both numbers are shown only while neither account is on hold,
            # the owner's identity is still verified and the team hasn't held the listing.
            if safety.is_suspended(uid):
                st.caption("Your account is on hold while the GharSe team reviews it, so the address "
                           "and the owner's number are hidden for now.")
            elif safety.is_suspended(owner_id):
                st.caption("The owner's account is on hold while the GharSe team reviews it, so the "
                           "address and her number are hidden for now. Don't pay anything to anyone.")
            elif x["rstatus"] in (HOLD, REMOVED) or not kyc.is_verified(x):
                st.warning("The address and the owner's number for this place are hidden " + _UNDER_REVIEW)
            else:
                owner = _contact(owner_id, OWNER_FALLBACK)
                _contact_block("Owner", owner,
                               f"Hi {owner['first']}, this is about my GharSe visit to “{x['title']}”.",
                               key=f"mr_wa{xid}", address=_private_details(x["rental_id"])["address"])
            _guest_money(uid, x)
        if ps in (REFUND_CHECK, REFUND_DUE, REFUNDED) and status != ACCEPTED:
            st.markdown(_row_split(x, "guest"), unsafe_allow_html=True)
            st.caption({REFUND_CHECK: "The GharSe team is matching your payment; once it's found it's "
                                      "refunded to you.",
                        REFUND_DUE: "Your refund is on its way from the GharSe team."}.get(ps, "Refunded."))
        if status in OPEN_REQUEST and ps not in (PAID_OUT, RELEASED):
            with st.popover("Withdraw request"):
                st.caption("The owner is told. "
                           + ("The booking amount you paid through GharSe is refunded by the team."
                              if ps in (CLAIMED, PAID) else ""))
                if st.button("Yes, withdraw", key=f"mr_wd{xid}"):
                    done = withdraw_request(xid, uid)
                    if done:
                        guest = _contact(uid)
                        _tell(owner_id, "Visit request withdrawn",
                              f"{guest['first']} has withdrawn their request for “{x['title']}”.", ref=xid)
                    st.session_state[FLASH] = ("Withdrawn." if done else "That request had already changed.")
                    st.rerun()
        elif status == ACCEPTED and ps in (PAID_OUT, RELEASED):
            st.markdown(safety.grievance_line(), unsafe_allow_html=True)


def _guest_money(uid, x):
    ps = x["payment_status"]
    xid = x["id"]
    if not ps:
        st.caption("No booking amount asked for yet. If the owner asks for a token or the first month, "
                   "you'll pay it here — never in cash or to her directly.")
        return
    st.markdown(_row_split(x, "guest"), unsafe_allow_html=True)
    if ps == UNPAID:
        if safety.is_suspended(uid):
            st.caption("Your account is on hold while the GharSe team reviews it, so you can't pay "
                       "right now. Don't pay anything for now.")
            return
        owner_ok = (not safety.is_suspended(x["owner_user_id"]) and kyc.is_verified(x)
                    and x["rstatus"] in (ACTIVE, PAUSED, LET))
        if _is_single_place({"kind": x["kind"], "sharing": x.get("sharing")}) and \
                _other_booking_taken(x["rental_id"], xid):
            st.warning("Someone else has already booked this place, so it can't take your booking "
                       "amount. Don't pay anything for it.")
            return
        if not owner_ok:
            st.warning("Payments for this place are paused " + _UNDER_REVIEW)
            return
        total = x["customer_total"]
        link = _upi_link(total, f"GharSe room {xid}")
        pa = (setting("platform_upi", str) or "").strip()
        if link:
            st.link_button(f"Pay {_money(total)} by UPI", link, width="stretch")
            st.caption(f"Or send {_money(total)} to {h(pa)} from any UPI app, then enter the reference below. "
                       "Pay only after you've visited.")
        else:
            st.caption("Payment isn't switched on yet — the GharSe team still needs to add the platform "
                       "UPI ID. Don't pay anyone directly.")
        with st.form(f"mr_pay{xid}", border=False):
            pc = st.columns([3, 1])
            ref = pc[0].text_input("UPI reference / UTR number", key=f"mr_utr{xid}")
            go = pc[1].form_submit_button("I've paid")
        if go:
            ref = (ref or "").strip()
            if len(ref) < 6:
                st.error("Enter the reference number your UPI app showed after payment.")
            elif claim_payment(xid, uid, ref, total):
                st.session_state[FLASH] = "Thanks — the GharSe team will confirm your payment shortly."
                st.rerun()
            elif (q("SELECT payment_status FROM rental_requests WHERE id=?", (xid,))
                  or [{"payment_status": None}])[0]["payment_status"] == REFUND_CHECK:
                # Lost a same-instant race for a single place (_single_place_backoff).
                st.session_state[FLASH] = ("Someone else booked this place a moment before you. Your "
                                           "reference is recorded — if you've paid, the GharSe team will "
                                           "match it and refund you in full.")
                st.rerun()
            else:
                st.error("The booking amount changed or this place isn't taking payments right now — "
                         "refresh the page and check before paying.")
    elif ps == CLAIMED:
        st.caption(f"Payment reference {h(x['payment_ref'] or '—')} — the GharSe team is confirming it.")
    elif ps in _PAID_IN:
        st.caption("Paid and confirmed — it's booked. Keep your UPI receipt.")
    elif ps == REFUND_DUE:
        st.caption("Your refund is on its way from the GharSe team.")
    elif ps == REFUND_CHECK:
        st.caption("The GharSe team is matching your payment; once it's found it's refunded to you.")


# ----------------------------------------------------------------------------- admin
def confirm_payment(request_id):
    return execute("UPDATE rental_requests SET payment_status=? WHERE id=? AND payment_status=? RETURNING id",
                   (PAID, request_id, CLAIMED))


def reject_payment(request_id):
    return execute("UPDATE rental_requests SET payment_status=?, payment_ref=NULL WHERE id=? "
                   "AND payment_status=? RETURNING id", (UNPAID, request_id, CLAIMED))


def mark_refunded(request_id):
    return execute("UPDATE rental_requests SET payment_status=? WHERE id=? AND payment_status=? RETURNING id",
                   (REFUNDED, request_id, REFUND_DUE))


def mark_nothing_received(request_id):
    """Refund check → nothing owed: the claim on a called-off booking was never matched by
    a payment. Only for a claim the team never confirmed — confirmed money (Refund due)
    can only ever be refunded."""
    return execute("UPDATE rental_requests SET payment_status=NULL WHERE id=? AND payment_status=? "
                   "RETURNING id", (request_id, REFUND_CHECK))


def mark_money_arrived(request_id):
    """Refund check → Refund due: the claimed money did arrive, so it is owed back."""
    return execute("UPDATE rental_requests SET payment_status=? WHERE id=? AND payment_status=? RETURNING id",
                   (REFUND_DUE, request_id, REFUND_CHECK))


# A Paid booking is payable to its owner only while the owner is verified and not on hold
# and the listing isn't on hold or removed. Anything else is frozen until the team decides —
# by restoring the listing or the owner, by refunding the guest (refund_frozen), or, when the
# listing is the only problem, by releasing it to the owner (release_frozen → Released, which
# skips the listing half of the rule but never the owner half). Aliases: rr the request,
# r its listing. _freeze_reason is the same rule in Python, giving the reason.
_PAYABLE_SQL = (f"(rr.payment_status='{RELEASED}' OR r.status NOT IN ('{HOLD}','{REMOVED}')) "
                f"AND r.owner_user_id IN ({_OWNER_IN_GOOD_STANDING})")


def _paid_bookings():
    return [dict(r) for r in q(
        "SELECT rr.id, rr.user_id, rr.status, rr.payment_status, rr.booking_amount, rr.customer_total, "
        "r.title, r.owner_user_id, r.status rstatus, u.suspended, u.kyc_status, u.phone_verified "
        "FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
        "JOIN users u ON u.id = r.owner_user_id WHERE rr.payment_status IN (?,?) ORDER BY rr.id",
        (PAID, RELEASED))]


def _freeze_reason(r):
    if safety.is_suspended({"suspended": r["suspended"]}):
        return "the owner's account is on hold"
    if r.get("payment_status") != RELEASED:
        if r["rstatus"] == HOLD:
            return "the listing is on hold"
        if r["rstatus"] == REMOVED:
            return "the listing was removed"
    if not kyc.is_verified(r):
        return "the owner's identity is no longer verified"
    return None


def owner_payouts_due():
    """{owner_user_id: [(request_id, amount), …]} for every Paid (or Released) booking not
    yet paid out whose owner is in good standing and whose listing isn't held or removed
    (or which the team released)."""
    due = {}
    for r in _paid_bookings():
        if _freeze_reason(r) is None:
            due.setdefault(r["owner_user_id"], []).append((r["id"], _num(r["booking_amount"])))
    return due


def _frozen_bookings():
    """Paid bookings held back, one dict each with its reason under "why"."""
    out = []
    for r in _paid_bookings():
        why = _freeze_reason(r)
        if why:
            out.append(dict(r, why=why))
    return out


def frozen_payouts():
    """{owner_user_id: [(request_id, amount, reason), …]} — Paid bookings held back."""
    out = {}
    for r in _frozen_bookings():
        out.setdefault(r["owner_user_id"], []).append((r["id"], _num(r["booking_amount"]), r["why"]))
    return out


def refund_frozen(request_id):
    """Frozen → Refund due, from where it goes through Payments like any other refund. Only
    while the booking is still Paid and still not payable (_PAYABLE_SQL), so one that was
    paid out, released or restored meanwhile is left alone. An open request is closed with
    it. The guest and the owner are told."""
    done = execute(
        "UPDATE rental_requests SET payment_status=?, status=CASE WHEN status IN (?,?) THEN ? ELSE status END "
        "WHERE id=? AND payment_status=? AND NOT EXISTS (SELECT 1 FROM rental_requests rr "
        f"JOIN rentals r ON r.id = rr.rental_id WHERE rr.id = rental_requests.id AND {_PAYABLE_SQL}) "
        "RETURNING id", (REFUND_DUE, REQUESTED, ACCEPTED, CLOSED, request_id, PAID))
    if done:
        x = q("SELECT rr.user_id, rr.customer_total, r.title, r.owner_user_id FROM rental_requests rr "
              "JOIN rentals r ON r.id = rr.rental_id WHERE rr.id=?", (request_id,))[0]
        _tell(x["user_id"], "Booking refund",
              f"The GharSe team is refunding the {_money(x['customer_total'])} you paid for “{x['title']}”. "
              "You'll get a message when it has been sent.", ref=request_id)
        _tell(x["owner_user_id"], "Booking refunded",
              f"The booking amount for “{x['title']}” (request #{request_id}) is being refunded to the guest, "
              "so it won't be paid out to you. Reply to the GharSe team if you think that's wrong.",
              ref=request_id)
    return done


def release_frozen(request_id):
    """Frozen ONLY by its listing (on hold, or removed) → Released, which lets it into the
    owner's payout. Refused while the owner is on hold or her identity isn't verified — the
    owner half of _PAYABLE_SQL — because then the money must not move at all. Only while it
    is still Paid. The owner is told."""
    done = execute(
        "UPDATE rental_requests SET payment_status=? WHERE id=? AND payment_status=? AND rental_id IN "
        f"(SELECT r.id FROM rentals r WHERE r.status IN (?,?) AND r.owner_user_id IN ({_OWNER_IN_GOOD_STANDING})) "
        "RETURNING id", (RELEASED, request_id, PAID, HOLD, REMOVED))
    if done:
        x = q("SELECT rr.booking_amount, r.title, r.owner_user_id FROM rental_requests rr "
              "JOIN rentals r ON r.id = rr.rental_id WHERE rr.id=?", (request_id,))[0]
        _tell(x["owner_user_id"], "Payout released",
              f"The GharSe team has released {_money(x['booking_amount'])} for “{x['title']}” "
              f"(request #{request_id}). It will be sent to you with your next payout.", ref=request_id)
    return done


def record_payout(owner_uid, request_ids, method, ref):
    """Record one payout for exactly the bookings this call flips Paid (or Released) → Paid out.

    The payout row is written FIRST, then each booking is flipped by its own conditional
    UPDATE (_PAYABLE_SQL — owner in good standing, listing not held or removed unless the
    team released it — re-checked in the same statement) and added to that row as it flips.
    So there is never a booking marked paid out without a payout record carrying the team's
    reference; a second click flips nothing, and a row that ends up empty is deleted.
    Returns (amount, count) or None."""
    payout_id = execute("INSERT INTO social_payouts (user_id, amount, items_count, source, method, ref, "
                        "created_at) VALUES (?,?,?,?,?,?,?)",
                        (owner_uid, 0, 0, "rental", method, ref, now_iso()))
    total, count = 0.0, 0
    for rid in request_ids:
        got = execute(
            "UPDATE rental_requests SET payment_status=? WHERE id=? AND payment_status IN (?,?) AND EXISTS "
            "(SELECT 1 FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
            f" WHERE rr.id = rental_requests.id AND r.owner_user_id=? AND {_PAYABLE_SQL}) RETURNING id",
            (PAID_OUT, rid, PAID, RELEASED, owner_uid))
        if got:
            amt = _num(q("SELECT booking_amount FROM rental_requests WHERE id=?", (rid,))[0]["booking_amount"])
            execute("UPDATE social_payouts SET amount=amount+?, items_count=items_count+1 WHERE id=?",
                    (amt, payout_id))
            total += amt
            count += 1
    if not count:
        execute("DELETE FROM social_payouts WHERE id=? AND items_count=0", (payout_id,))
        return None
    total = round(total, 2)
    _tell(owner_uid, "Payout",
          f"{_money(total)} has been sent to you for {count} room booking{'s' if count != 1 else ''} on "
          f"GharSe. {method} reference {ref}.")
    return total, count


# The team can hold a listing that is live, paused by its owner, or LET — Let is where
# paid bookings wait (mark_let keeps them Accepted/Paid), so a scam reported after the
# owner marks the place let must still freeze its escrow (_PAYABLE_SQL / _freeze_reason).
_HOLDABLE = (ACTIVE, PAUSED, LET)


def _legacy_prehold_key(rental_id):
    """Before rentals.prehold_status existed, the status a held listing came from was kept
    in settings under this key. It is only read (and deleted) now, as the fallback for a
    listing that was put on hold before that change."""
    return f"rental_prehold_status:{int(rental_id)}"


def _legacy_prehold(rental_id):
    rows = q("SELECT value FROM settings WHERE key=?", (_legacy_prehold_key(rental_id),))
    v = rows[0]["value"] if rows else None
    return v if v in _HOLDABLE else None


def moderate_listing(rental_id, action, reason):
    """action: 'hold' | 'remove' | 'restore'. Returns the id, or None if it had moved on.

    hold works from Active, Paused or Let and records which in prehold_status in the same
    UPDATE (a SET reads the row as it was before the statement, on SQLite and Postgres
    alike); restore puts the listing back there — Active when nothing was recorded — and
    clears it, in one UPDATE that only matches a listing still on hold."""
    if action == "hold":
        done = execute("UPDATE rentals SET prehold_status=status, status=? WHERE id=? AND status IN (?,?,?) "
                       "RETURNING id", (HOLD, rental_id, *_HOLDABLE))
    elif action == "remove":
        done = execute("UPDATE rentals SET status=?, prehold_status=NULL WHERE id=? AND status<>? RETURNING id",
                       (REMOVED, rental_id, REMOVED))
    elif action == "restore":
        done = execute("UPDATE rentals SET status=COALESCE(prehold_status, ?, ?), prehold_status=NULL "
                       "WHERE id=? AND status=? RETURNING id",
                       (_legacy_prehold(rental_id), ACTIVE, rental_id, HOLD))
    else:
        return None
    if not done:
        return None
    execute("DELETE FROM settings WHERE key=?", (_legacy_prehold_key(rental_id),))
    r = q("SELECT owner_user_id, title, status FROM rentals WHERE id=?", (rental_id,))[0]
    if action == "hold":
        _tell(r["owner_user_id"], _MOD_KINDS[0],
              f"Your listing “{r['title']}” is on hold and hidden from Rooms & PGs. Reason: {reason}. "
              "Edit the listing to fix it and reply to the GharSe team to have it restored.", ref=rental_id)
    elif action == "remove":
        _close_open_requests(rental_id, "it was taken down", refund_paid=True)
        _tell(r["owner_user_id"], _MOD_KINDS[1],
              f"Your listing “{r['title']}” has been removed from Rooms & PGs. Reason: {reason}. Open "
              "requests on it were closed and any booking money is being refunded to guests.", ref=rental_id)
    else:
        _tell(r["owner_user_id"], "Room listing restored",
              {ACTIVE: f"Your listing “{r['title']}” is live on Rooms & PGs again.",
               PAUSED: f"Your listing “{r['title']}” is no longer on hold. It is still paused — resume "
                       "it from My listings when you're ready.",
               LET: f"Your listing “{r['title']}” is no longer on hold. It is marked as let, as it was "
                    "before."}.get(r["status"], f"Your listing “{r['title']}” is no longer on hold."),
              ref=rental_id)
    return done


def rentals_admin(admin_user):
    # This page shows exact addresses, phones, UPI IDs and payment references and moves
    # money, so it guards itself rather than relying on the app's routing alone.
    if (admin_user or {}).get("role") != "admin":
        st.error("Only the GharSe team can open this page.")
        return
    st.markdown(
        f"<div class='page-title'>{h(ADMIN_PAGE_TITLE)}</div>"
        "<div class='page-sub'>Rooms & PGs: confirm booking payments, pay owners what they're owed, "
        "and keep the listings honest.</div>",
        unsafe_allow_html=True,
    )
    _flash(ADMIN_FLASH)
    claims = [dict(r) for r in q(
        "SELECT rr.*, r.title, r.owner_user_id FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
        "WHERE rr.payment_status IN (?,?,?) ORDER BY rr.id", (CLAIMED, REFUND_CHECK, REFUND_DUE))]
    due = owner_payouts_due()
    frozen = _frozen_bookings()
    owed = sum(a for items in due.values() for _, a in items)
    paid_out = q("SELECT COALESCE(SUM(amount),0) s FROM social_payouts WHERE source='rental'")[0]["s"]
    c = st.columns(4)
    kpi(c[0], "Live listings", f"{q('SELECT COUNT(*) c FROM rentals WHERE status=?', (ACTIVE,))[0]['c']}")
    kpi(c[1], "Payments to check", f"{len(claims)}")
    kpi(c[2], "Owed to owners", _money(owed))
    kpi(c[3], "Paid out to date", _money(paid_out))
    st.write("")
    tabs = st.tabs(["Payments", "Owner payouts", "Listings", "All requests"])
    with tabs[0]:
        _admin_payments(claims)
    with tabs[1]:
        _admin_payouts(due, frozen)
    with tabs[2]:
        _admin_listings()
    with tabs[3]:
        rows = q("SELECT rr.id, rr.created_at, r.title, rr.status, rr.booking_amount, rr.platform_fee, "
                 "rr.ops_fee, rr.customer_total, rr.payment_status, rr.payment_ref FROM rental_requests rr "
                 "JOIN rentals r ON r.id = rr.rental_id ORDER BY rr.id DESC LIMIT 300")
        table(rows, money=("booking_amount", "platform_fee", "ops_fee", "customer_total"),
              empty="No visit requests yet.")


def _admin_payments(claims):
    if not claims:
        st.success("Nothing to check — no claimed payments or refunds waiting.")
        return
    for x in claims:
        guest, owner = _contact(x["user_id"]), _contact(x["owner_user_id"])
        with st.container(border=True):
            c = st.columns([3, 1, 1])
            c[0].markdown(f"<b>#{h(x['id'])} · {h(x['title'])}</b><br>"
                          f"<span class='muted'>{h(guest['name'])} → {h(owner['name'])} · ref "
                          f"{h(x['payment_ref'] or '—')}</span>", unsafe_allow_html=True)
            c[1].markdown(f"<div class='price'>{_money(x['customer_total'])}</div>", unsafe_allow_html=True)
            c[2].markdown(_pay_pill(x["payment_status"]), unsafe_allow_html=True)
            st.markdown(_row_split(x, "admin"), unsafe_allow_html=True)
            b = st.columns([1, 1, 3])
            if x["payment_status"] == CLAIMED:
                if b[0].button("Confirm received", key=f"ra_conf{x['id']}", width="stretch"):
                    if confirm_payment(x["id"]):
                        _tell(x["owner_user_id"], "Booking paid",
                              f"{guest['first']} has paid the booking amount for “{x['title']}”. GharSe "
                              f"will send you {_money(x['booking_amount'])}.", ref=x["id"])
                        _tell(x["user_id"], "Payment confirmed",
                              f"Your payment of {_money(x['customer_total'])} for “{x['title']}” is "
                              "confirmed. It's booked.", ref=x["id"])
                        st.session_state[ADMIN_FLASH] = f"Confirmed payment on request #{x['id']}."
                    st.rerun()
                if b[1].button("Not received", key=f"ra_rej{x['id']}", width="stretch"):
                    if reject_payment(x["id"]):
                        _tell(x["user_id"], "Payment not found",
                              f"We couldn't find your payment for “{x['title']}”. Check the UPI reference "
                              "and claim it again from Rooms & PGs → My requests.", ref=x["id"])
                        st.session_state[ADMIN_FLASH] = f"Sent request #{x['id']} back to unpaid."
                    st.rerun()
            elif x["payment_status"] == REFUND_CHECK:
                st.caption("Called off before anyone confirmed the payment. Check the account for "
                           f"reference {h(x['payment_ref'] or '—')}: if the money arrived it is refunded; if "
                           "it never did, nothing is owed.")
                if b[0].button("Money arrived", key=f"ra_arr{x['id']}", width="stretch",
                               help="Moves it to Refund due."):
                    if mark_money_arrived(x["id"]):
                        st.session_state[ADMIN_FLASH] = f"Request #{x['id']} is now due a refund."
                    st.rerun()
                with b[1].popover("No money arrived", width="stretch"):
                    st.caption("Only if no payment with this reference ever reached the platform UPI. The "
                               "guest is told and nothing is refunded.")
                    if st.button("Yes, nothing arrived", key=f"ra_none{x['id']}"):
                        if mark_nothing_received(x["id"]):
                            _tell(x["user_id"], "Payment not found",
                                  f"We couldn't find a payment with reference {x['payment_ref'] or '—'} for "
                                  f"“{x['title']}”, so there is nothing to refund. If you did pay, reply with "
                                  "your UPI receipt and the GharSe team will look again.", ref=x["id"])
                            st.session_state[ADMIN_FLASH] = f"Closed request #{x['id']} — nothing to refund."
                        st.rerun()
            else:
                if b[0].button("Mark refunded", key=f"ra_refd{x['id']}", width="stretch"):
                    if mark_refunded(x["id"]):
                        _tell(x["user_id"], "Refunded",
                              f"{_money(x['customer_total'])} for “{x['title']}” has been refunded to you.",
                              ref=x["id"])
                        st.session_state[ADMIN_FLASH] = f"Marked request #{x['id']} refunded."
                    st.rerun()


def _admin_payouts(due, frozen=None):
    """due: owner_payouts_due(). frozen: _frozen_bookings() — the held-back rows, each with
    the team's two ways out of it."""
    if not due:
        st.success("Nothing owed — every paid booking has been settled with its owner.")
    for owner_uid, items in due.items():
        owner = _contact(owner_uid)
        amount = round(sum(a for _, a in items), 2)
        with st.container(border=True):
            c = st.columns([3, 1])
            c[0].markdown(f"<b>{h(owner['name'])}</b> · @{h(owner['username'])}<br>"
                          f"<span class='muted'>{len(items)} paid booking(s) · phone "
                          f"{h(owner['phone'] or 'not on file')} · UPI {h(owner['upi'] or 'not on file')}</span>",
                          unsafe_allow_html=True)
            c[1].markdown(f"<div class='price'>{_money(amount)}</div><span class='muted'>owed</span>",
                          unsafe_allow_html=True)
            if not owner["upi"]:
                st.caption("No UPI ID on file — ask the owner for one before paying.")
            with st.form(f"ra_po{owner_uid}", border=False):
                fc = st.columns([1, 2, 1])
                method = fc[0].selectbox("Method", ["UPI", "Bank transfer", "Cash"], key=f"ra_pom{owner_uid}")
                ref = fc[1].text_input("Transaction reference", key=f"ra_por{owner_uid}")
                go = fc[2].form_submit_button(f"Record {_money(amount)}")
            if go:
                ref = (ref or "").strip()
                if len(ref) < 4:
                    st.error("Enter the transaction reference — it's the audit trail.")
                else:
                    res = record_payout(owner_uid, [i for i, _ in items], method, ref)
                    st.session_state[ADMIN_FLASH] = (
                        f"Recorded {_money(res[0])} to {h(owner['name'])}." if res else
                        "Those bookings were already paid out.")
                    st.rerun()
    by_owner = {}
    for r in frozen or []:
        by_owner.setdefault(r["owner_user_id"], []).append(r)
    for owner_uid, items in by_owner.items():
        owner = _contact(owner_uid)
        amount = round(sum(_num(r["booking_amount"]) for r in items), 2)
        reasons = sorted({r["why"] for r in items})
        with st.container(border=True):
            c = st.columns([3, 1])
            c[0].markdown(f"<b>{h(owner['name'])}</b> · @{h(owner['username'])} {_pill('Frozen', 'warn')}<br>"
                          f"<span class='muted'>{len(items)} paid booking(s) held back — "
                          f"{h('; '.join(reasons))}. Nothing is paid out until that's resolved. When waiting "
                          "won't resolve it (a removed listing never comes back), refund the guest, or "
                          "release the booking to an owner in good standing.</span>",
                          unsafe_allow_html=True)
            c[1].markdown(f"<div class='price'>{_money(amount)}</div><span class='muted'>frozen</span>",
                          unsafe_allow_html=True)
            for r in items:
                _frozen_row(r)
    st.markdown("#### Payout history")
    hist = q("SELECT sp.created_at, u.username owner, sp.amount, sp.items_count bookings, sp.method, sp.ref "
             "FROM social_payouts sp JOIN users u ON u.id = sp.user_id WHERE sp.source='rental' "
             "ORDER BY sp.id DESC LIMIT 200")
    table(hist, money=("amount",), empty="No payouts recorded yet.")


def _frozen_row(r):
    """One frozen booking: what it is, why it's held, and Refund guest / Release to owner.
    Both are single conditional UPDATEs on the state shown here, so a stale page can't
    refund a booking that was paid out meanwhile, or release one whose owner went on hold."""
    rid = r["id"]
    guest = _contact(r["user_id"])
    st.markdown(f"<span class='muted'>#{h(rid)} · {h(r['title'])} · {h(guest['name'])} paid "
                f"{_money(r['customer_total'])} ({_money(r['booking_amount'])} to the owner) — "
                f"{h(r['why'])}</span> {_request_pill(r['status'])}", unsafe_allow_html=True)
    b = st.columns([1, 1, 3])
    with b[0].popover("Refund guest", width="stretch"):
        st.caption("The booking is closed and moves to Refund due under Payments — mark it refunded "
                   "there once the money is sent. The guest and the owner are told.")
        if st.button("Yes, refund the guest", key=f"ra_frf{rid}"):
            st.session_state[ADMIN_FLASH] = (
                f"Request #{rid} is due a refund — send it, then mark it refunded under Payments."
                if refund_frozen(rid) else "That booking has changed — refresh the page.")
            st.rerun()
    if r["rstatus"] not in (HOLD, REMOVED):
        return
    # Release lifts the listing's freeze only; an owner on hold or unverified stays frozen.
    if safety.is_suspended({"suspended": r["suspended"]}) or not kyc.is_verified(r):
        b[1].caption("Can't be released while the owner is on hold or not identity-verified.")
        return
    with b[1].popover("Release to owner", width="stretch"):
        st.caption("Only when you're sure the stay was genuine. It joins the owner's payout above even "
                   "though the listing is " + ("on hold" if r["rstatus"] == HOLD else "removed")
                   + ". The owner is told.")
        if st.button("Yes, release it", key=f"ra_frl{rid}"):
            st.session_state[ADMIN_FLASH] = (
                f"Released request #{rid} to the owner — it's in Owner payouts now."
                if release_frozen(rid) else
                "That can't be released now — it changed, or the owner is on hold or not verified.")
            st.rerun()


def _admin_listings():
    statuses = [ACTIVE, PAUSED, HOLD, LET, REMOVED]
    pick = st.selectbox("Show", ["Reported", "All"] + statuses, key="ra_lst_filter")
    rows = [dict(r) for r in q(
        "SELECT r.id, r.owner_user_id, r.kind, r.title, r.description, r.area, r.rent, r.deposit, "
        "r.address, r.status, r.photo, r.created_at, u.username, u.full_name, "
        "(SELECT COUNT(*) FROM reports rp WHERE rp.target_kind='rental' AND rp.target_id=r.id "
        " AND rp.status='open') open_reports "
        "FROM rentals r JOIN users u ON u.id = r.owner_user_id ORDER BY r.id DESC LIMIT 300")]
    if pick == "Reported":
        rows = [r for r in rows if r["open_reports"]]
    elif pick != "All":
        rows = [r for r in rows if r["status"] == pick]
    if not rows:
        st.caption("No listings reported right now." if pick == "Reported" else "No listings here.")
        return
    for r in rows:
        rid = r["id"]
        with st.container(border=True):
            cols = st.columns([1, 3]) if r["photo"] else [None, st.container()]
            if r["photo"]:
                with cols[0]:
                    safe_image(r["photo"], width="stretch")
            cols[1].markdown(
                f"<b>#{h(rid)} · {h(r['title'])}</b> {_listing_pill(r['status'])}"
                + (f" {_pill(str(r['open_reports']) + ' open report(s)', 'warn')}" if r["open_reports"] else "")
                + f"<br><span class='muted'>{h(r['kind'])} · {h(r['area'])} · {inr(_num(r['rent']))} / month · "
                f"deposit {inr(_num(r['deposit']))} · by {h(r['full_name'] or r['username'])} "
                f"(@{h(r['username'])}) · {h(short_dt(r['created_at']))}</span><br>"
                f"<span class='muted'>{h((r['description'] or '')[:400])}</span><br>"
                f"<span class='muted'>Address: {h(r['address'])}</span>",
                unsafe_allow_html=True,
            )
            if r["status"] == REMOVED:
                continue
            if r["status"] == HOLD and st.button("Restore listing", key=f"ra_restore{rid}"):
                if moderate_listing(rid, "restore", ""):
                    back = q("SELECT status FROM rentals WHERE id=?", (rid,))[0]["status"]
                    st.session_state[ADMIN_FLASH] = f"Restored #{rid} ({back}); the owner has been told."
                st.rerun()
            with st.form(f"ra_mod{rid}", border=False):
                reason = st.text_input("Reason (sent to the owner)", key=f"ra_modr{rid}",
                                       placeholder="Photos don't match the room / asked for money outside GharSe")
                fc = st.columns([1, 1, 3])
                hold = fc[0].form_submit_button("Put on hold") if r["status"] in _HOLDABLE else False
                remove = fc[1].form_submit_button("Remove")
            if hold or remove:
                reason = (reason or "").strip()
                if len(reason) < 5:
                    st.error("Write the reason — the owner is told exactly why.")
                else:
                    done = moderate_listing(rid, "hold" if hold else "remove", reason)
                    st.session_state[ADMIN_FLASH] = (
                        (f"Put #{rid} on hold." if hold else f"Removed #{rid}.") + " The owner has been told."
                        if done else "That listing had already changed.")
                    st.rerun()
