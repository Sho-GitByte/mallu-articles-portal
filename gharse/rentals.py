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
#      is no longer verified, or while the team has the listing on hold (the guest is told
#      only that the team is looking into it, in the words a paused payment uses).
#   4. Optional booking amount — the owner sets a token or first month (only while the
#      listing is live, paused or let — not while the team has it on hold or removed it).
#      GharSe adds its platform and processing fees ON TOP (ui.split_on_top), the guest pays
#      the platform UPI and claims it with the reference, and the team confirms it on
#      Rentals Admin. The claim is for the amount her page SHOWED her — the pay form's figures
#      travel with the click (_pay_clicked) — never one the owner set after it was drawn. The
#      click is acted on in its own callback, for the account the form was drawn for, so it
#      lands whatever the page draws next. If it can't be claimed (the amount changed, the
#      place was taken, payments were paused, the booking was called off, her account was put
#      on hold — app.py then shows her only the hold page), she has quite possibly paid
#      already, so her reference and that amount are recorded as a Refund check for the team
#      (record_unclaimed_payment) and she is told exactly what happened on her next page.
#      One reference is one payment, counted on one row of hers: a reference another row of
#      hers already counts (a payment recorded for the team, a refund, another booking) is
#      never claimed or recorded again, and the pay form is never redrawn holding a used one.
#   5. The team pays the owner what she asked for and records it in social_payouts
#      (source "rental").
#   6. The stay ends. Relisting a let SINGLE place (a whole flat, a private room) closes its
#      tenancy and tells the guest. A shared place is relisted with its tenants left as they
#      are — a PG with one bed free is still home to the others — and the owner closes one
#      tenancy at a time with "End stay" on that guest's request, which tells the guest.
#
# A listing the team REMOVES closes every open request on it, and each guest is told: money
# GharSe holds (Claimed or Paid) is flagged for refund, and a booking the team had already
# released to the owner or paid out is closed with its money state kept — a released booking
# is still paid out — so the owner's message promises refunds only where they happen.
#
# Tables: rentals, rental_requests, social_payouts.
#
#   rentals.status           Active | Paused (by the owner) | Let | On hold (by GharSe) | Removed
#   rentals.prehold_status   the status a listing had when the team put it On hold, written by
#                            the same UPDATE that holds it; restoring puts it back there
#   rental_requests.status   Requested | Accepted | Declined | Withdrawn | Cancelled | Closed
#                            | Payment recorded (not a request: a guest's payment that couldn't
#                              go towards her booking, kept as a Refund check; note says why)
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
# team) and that guest is told — on the page too, whichever claimer's re-check closed it: the
# claim is read back afterwards, never assumed to have stood, and the request card says it
# too if a rival's claim lands only after that read-back. A claim refused outright because
# someone else's claim got in first is closed the same way, with the reference the guest
# typed and the amount she was shown. A booking amount the owner sets on the place in that
# same instant is checked again the same way and, if a claim got there first, put back to
# what it was before, so no guest is asked to pay for a place someone else holds. An edit
# that would turn a shared place with more than one booking claimed or paid into a single
# place is refused (edit_listing); a claim that slips in while it runs is settled after the
# commit like any claim on a single place — the later one gives way, in words that don't
# blame a race — and the edit stands. A reference written twice at the same instant is
# settled the same way after the commit (_settle_reference): a claim gives way to another
# row of hers that counts it and goes back to Unpaid, and a duplicate record of the same
# payment removes itself.
#
# The owner's standing is re-checked INSIDE each of those statements, not just on the page:
# an owner who is on hold or whose identity is no longer verified cannot accept a visit
# (which is what shares contact details), ask for or take a booking amount, or be paid out,
# and a listing the team has put on hold freezes its payouts until it is sorted out. A frozen
# booking is settled by the team on Rentals Admin when waiting won't fix it (a removed
# listing never comes back): refund the guest, or — only while the owner is in good standing
# — release it into the owner's payout. A released booking still waits on the owner's
# standing, and the team can refund it if she goes on hold or loses her verification
# before it is paid out.
# _PAYABLE_SQL (its listing half and owner half) / _freeze_reason are the one definition of
# what can be paid out.
#
# A write can fail AFTER it has committed: store.py sends a Postgres write once and raises if
# the connection drops mid-write, so the page sees an error for a change that may well have
# happened. Every change made of several statements is therefore ordered, or repaired, so that
# stopping between any two of them leaves no money unaccounted and nothing counted twice:
#   * mark_let flags the turned-away guests' money for refund BEFORE the listing turns Let,
#     each refund only while the listing is still live or paused (a hold stops them), and
#     writes a marker just before the flip; stopped before the flip, the place is still live
#     and Mark let finishes it, and stopped after it, finish_interrupted does.
#   * record_payout writes its row empty and counts what it flipped into it only at the end,
#     so a stopped recording always leaves an empty row (or its owner's records short). Owner
#     payouts warns about exactly those owners before anyone sends them money again.
#   * finish_interrupted, run whenever Rentals Admin opens, completes the rest — a claim whose
#     reference another row of hers counts goes back to Unpaid; a claim on a single place
#     someone else holds is closed (its re-check never ran, or the place became single with
#     two claims on it), in words true either way, and confirm_payment refuses both meanwhile;
#     a payment recorded twice for one guest keeps its first row; a Let listing whose mark_let
#     stopped is finished; requests a removal left open are closed; and bookings flipped to
#     Paid out but not yet counted are added to the owner's oldest empty payout row (else her
#     latest), once none of her rows is younger than _PAYOUT_SETTLE, so a payout still being
#     recorded is never counted twice. An empty row is never deleted while it may be the only
#     record of the reference money went out under: it clears itself once the bookings that
#     money paid are recorded again, later, under the same reference, and otherwise stays on Owner payouts,
#     with that reference, until the team dismisses it.
#   * set_booking_amount's take-back needs no repair: an amount left on a single place someone
#     else holds can't be paid (claim_payment refuses it, and the guest's page says it's taken).
# Everything else is a single money write followed only by messages.
#
# A residue that only a payout id on each booking could remove: once a stopped recording's row
# has been completed (ten minutes on), the bookings it never reached look like any other money
# owed, and the warning goes with the row it completed.

import re
from datetime import date, timedelta
from urllib.parse import quote

import streamlit as st

import kyc
import safety
import store
from catalog import AMENITIES, AREAS, GENDER_PREF, RENTAL_KINDS, SHARING
from store import execute, now_iso, now_ist, q, set_setting, setting, today_ist
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
# Not a request at all: a payment a guest made that couldn't go towards her booking (see
# record_unclaimed_payment). It only ever waits under Payments as a Refund check.
RECORDED = "Payment recorded"
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
# What an "I've paid" click came to waits for its guest's next Rooms & PGs page under this
# prefix and her uid (_pay_flash_key). The click itself is acted on as it arrives (_pay_clicked).
PAY_CLICK = "rentals_pay_click"
# What a guest is told when her claim on a single place is closed for another booking:
# straight after a same-instant race, or by finish_interrupted, which can't tell a race whose
# re-check never ran from a place made single with two claims on it, so its words are true
# either way. The request card reads back which one she was sent (_lost_to_rival).
_RACE_KIND, _GAVE_WAY_KIND = "Room already booked", "Room booking closed"
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
# More than one open request on a place holds it, each with a claim or confirmed money
# (rentals.id is the place). A shared place may have that; a single place must not (edit_listing).
_TWO_HOLDERS = (
    "EXISTS (SELECT 1 FROM rental_requests a JOIN rental_requests b ON b.rental_id = a.rental_id "
    "AND b.id > a.id WHERE a.rental_id = rentals.id AND a.status IN ('Requested','Accepted') "
    "AND b.status IN ('Requested','Accepted') "
    "AND a.payment_status IN ('Claimed','Paid','Released','Paid out') "
    "AND b.payment_status IN ('Claimed','Paid','Released','Paid out'))")
# A payment reference stands for ONE payment, so it is counted on one row of the guest's: a
# row that carries it with money in it (Claimed, confirmed, being refunded or refunded — not
# one the team found nothing for). claim_payment refuses a reference already counted on any
# other row of hers (the ? is the reference): a payment recorded for the team, money being
# refunded, another booking.
_REF_COUNTS = ("o.payment_status IN ('Claimed','Paid','Released','Paid out','Refund check','Refund due',"
               "'Refunded')")
_REF_ON_ANOTHER_ROW = (
    "EXISTS (SELECT 1 FROM rental_requests o WHERE o.user_id = rental_requests.user_id "
    f"AND o.payment_ref = ? AND o.id <> rental_requests.id AND {_REF_COUNTS})")
# The same rule checked again AFTER a reference is written (see _settle_reference): an open
# claim gives way to any other row of hers counting its reference, except a LATER open claim
# (which gives way to it instead) — so whatever order two writes of one reference commit in,
# it ends up counted once, and a payment recorded for the team always outlasts a claim made
# with it.
_REF_GIVES_WAY = (
    "EXISTS (SELECT 1 FROM rental_requests o WHERE o.user_id = rental_requests.user_id "
    f"AND o.payment_ref = rental_requests.payment_ref AND o.id <> rental_requests.id AND {_REF_COUNTS} "
    "AND NOT (o.payment_status = 'Claimed' AND o.status IN ('Requested','Accepted') "
    "AND o.id > rental_requests.id))")
# A "Payment recorded" row still waiting as a Refund check that an EARLIER row of the same
# guest already covers — her reference on it, for the same amount, recorded for the team,
# being refunded, or a confirmed booking: two writes of one payment raced, and the later
# record goes. Never because of an earlier open CLAIM with that reference (the claim gives way
# to the record instead, _REF_GIVES_WAY, so her money is still recorded if the claim is then
# turned down), and never when the amounts differ: then both stay, and Rentals Admin shows
# the team that one reference is on two rows (_shared_references).
_DUPLICATE_RECORD = (
    "status='Payment recorded' AND payment_status='Refund check' AND EXISTS (SELECT 1 FROM rental_requests o "
    "WHERE o.user_id = rental_requests.user_id AND o.payment_ref = rental_requests.payment_ref "
    "AND o.id < rental_requests.id AND ABS(o.customer_total - rental_requests.customer_total) < 0.005 "
    "AND o.payment_status IN ('Paid','Released','Paid out','Refund check','Refund due','Refunded'))")


# ----------------------------------------------------------------------------- helpers
def _uid(user):
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _flash(key):
    msg = st.session_state.pop(key, None)
    if isinstance(msg, tuple):          # ("warn", text): something to check, not a success
        st.warning(msg[1])
    elif msg:
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
                 CANCELLED: "warn", CLOSED: "flat", RECORDED: "flat"}
_PAY_PILL = {UNPAID: "warn", CLAIMED: "", PAID: "ok", RELEASED: "ok", PAID_OUT: "ok",
             REFUND_CHECK: "warn", REFUND_DUE: "warn", REFUNDED: "flat"}
_REQUEST_WORDS = {REQUESTED: "Visit requested", ACCEPTED: "Accepted", DECLINED: "Declined",
                  WITHDRAWN: "Withdrawn", CANCELLED: "Cancelled by owner", CLOSED: "Closed",
                  RECORDED: "Payment recorded"}
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
    _flash(_pay_flash_key(uid))
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


def _close_open_requests(rental_id, why, refund_paid, keep=None, leave_unpaid=False, while_listing=None):
    """Close what's still open on a listing that is no longer available.

    refund_paid is for a listing gone for good (the team removed it): EVERY open request is
    closed. Booking money with GharSe (Claimed or Paid) is flagged for refund, and a booking
    the team already released to the owner or paid out is closed with its money state kept,
    so a released booking is still paid out. Otherwise (the owner let the place) a request
    with money in it is left alone, except that when keep is given every Claimed or Paid
    request NOT in keep is flagged for refund (keep = the booking(s) the place was actually
    let to). A confirmed payment becomes Refund due; an unconfirmed claim becomes Refund
    check, so the team checks whether the money really arrived before refunding anything.
    leave_unpaid skips the requests nobody paid on (mark_let closes those afterwards).
    while_listing: statuses the listing must still be in, checked inside each UPDATE, so a
    hold the team puts on it part-way stops the closing there (mark_let, before the flip).

    Each request is its own conditional UPDATE, so running this again finishes what an
    interrupted run left open and changes nothing else (see finish_interrupted)."""
    rows = q("SELECT id, user_id, payment_status FROM rental_requests WHERE rental_id=? AND status IN (?,?)",
             (rental_id, REQUESTED, ACCEPTED))
    title = (q("SELECT title FROM rentals WHERE id=?", (rental_id,)) or [{"title": "the place"}])[0]["title"]
    keep = None if keep is None else {int(k) for k in keep}
    listing_sql, listing_args = "", ()
    if while_listing:
        listing_sql = (" AND rental_id IN (SELECT id FROM rentals WHERE status IN "
                       f"({','.join('?' * len(while_listing))}))")
        listing_args = tuple(while_listing)
    for r in rows:
        if r["payment_status"] in (CLAIMED, PAID):
            refund = refund_paid or (keep is not None and r["id"] not in keep)
            if not refund:
                continue
            to = REFUND_DUE if r["payment_status"] == PAID else REFUND_CHECK
            done = execute("UPDATE rental_requests SET status=?, payment_status=? WHERE id=? "
                           f"AND status IN (?,?) AND payment_status=?{listing_sql} RETURNING id",
                           (CLOSED, to, r["id"], REQUESTED, ACCEPTED, r["payment_status"], *listing_args))
            extra = " The booking amount you paid will be refunded by the GharSe team."
        elif r["payment_status"] in (PAID_OUT, RELEASED):
            # Settled with the owner. A tenant of a let place stays; on a removed listing the
            # booking is closed — nothing is left to act on — and its money stays as it is.
            if not refund_paid:
                continue
            done = execute("UPDATE rental_requests SET status=? WHERE id=? AND status IN (?,?) "
                           f"AND payment_status=?{listing_sql} RETURNING id",
                           (CLOSED, r["id"], REQUESTED, ACCEPTED, r["payment_status"], *listing_args))
            extra = (" Your booking amount had already been settled with the owner, so it isn't refunded "
                     "through GharSe — reply to the GharSe team if you need help.")
        elif leave_unpaid:
            continue
        else:
            done = execute("UPDATE rental_requests SET status=?, payment_status=NULL WHERE id=? "
                           "AND status IN (?,?) AND (payment_status IS NULL OR payment_status=?)"
                           f"{listing_sql} RETURNING id",
                           (CLOSED, r["id"], REQUESTED, ACCEPTED, UNPAID, *listing_args))
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
        _tell(x["user_id"], "Stay ended",
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
    refund. None keeps every paid booking (there is at most one, or the owner didn't say).

    Those refunds are made BEFORE the listing turns Let, and the rest is closed after
    (_finish_letting) — including a claim that landed after the refunds looked. A write can
    fail after it has committed, so this can stop between any two statements. Stopped before
    the flip, the place is still live with Mark let on the page to finish the job — never a
    let place with a turned-away guest's money still counted as the owner's payout. After the
    flip Mark let is no longer on the page, so the flip is preceded by a marker (_letting_key,
    holding let_to) that stays until the rest is done, and finish_interrupted finishes a Let
    listing that still has one (so does calling this again).

    Each of those refunds is made only while the listing is still live or paused (checked in
    its own UPDATE), so if the team puts it on hold part-way, nobody else is refunded on the
    owner's say-so or told that she has let it; the flip then fails too."""
    if not q("SELECT 1 FROM rentals WHERE id=? AND owner_user_id=? AND status IN (?,?)",
             (rental_id, uid, ACTIVE, PAUSED)):
        # Let already, by a mark_let of hers that stopped after the flip: asking again finishes
        # that one, as it was asked (finish_interrupted does the same when Rentals Admin opens).
        marker = q("SELECT value FROM settings WHERE key=?", (_letting_key(rental_id),))
        if marker and q("SELECT 1 FROM rentals WHERE id=? AND owner_user_id=? AND status=?",
                        (rental_id, uid, LET)):
            _finish_letting(rental_id, _letting_keep(marker[0]["value"]))
            return rental_id
        return None
    keep = None if let_to is None else sorted({int(k) for k in let_to})
    if keep is not None:
        _close_open_requests(rental_id, "the owner has let it", refund_paid=False, keep=keep,
                             leave_unpaid=True, while_listing=(ACTIVE, PAUSED))
    set_setting(_letting_key(rental_id), "*" if keep is None else ",".join(str(k) for k in keep))
    if not set_listing_status(rental_id, uid, LET, (ACTIVE, PAUSED)):
        # Not let after all (the team held it, or it changed): the marker goes, unless another
        # tab's mark_let has just let it and still needs it.
        execute("DELETE FROM settings WHERE key=? AND NOT EXISTS (SELECT 1 FROM rentals WHERE id=? AND status=?)",
                (_letting_key(rental_id), rental_id, LET))
        return None
    _finish_letting(rental_id, keep)
    return rental_id


def _letting_key(rental_id):
    """Marks a mark_let that has not finished on the listing, its value the let_to request ids
    ("*" when none were given). Kept in settings because rentals has no column for it."""
    return f"rental_letting:{int(rental_id)}"


def _letting_keep(value):
    """The let_to a marker holds: None for "*", else the request ids."""
    return None if value == "*" else [int(k) for k in (value or "").split(",") if k.strip().isdigit()]


def _finish_letting(rental_id, keep):
    """mark_let's second half, on a listing that is now Let: the visit requests nobody paid on
    are closed, and with keep every other request with booking money in it is refunded — a
    claim that slipped in after the first half looked included. Run twice with keep, because a
    request closed in the first pass can lose that UPDATE to the guest's own claim landing at
    the same instant, and the second pass then refunds the claim. Only while the listing is
    still Let (a hold stops it, and it carries on once the place is restored), and the marker
    is cleared only then."""
    for _ in range(1 if keep is None else 2):
        _close_open_requests(rental_id, "the owner has let it", refund_paid=False, keep=keep,
                             while_listing=(LET,))
    execute("DELETE FROM settings WHERE key=? AND EXISTS (SELECT 1 FROM rentals WHERE id=? AND status=?)",
            (_letting_key(rental_id), rental_id, LET))


def _finish_lettings():
    """Every mark_let that stopped after its listing turned Let (its marker is still there) is
    finished (_finish_letting). A marker on a listing still live or paused is the owner's to
    finish — Mark let is on her page — one on a listing the team holds waits for it to be
    restored, and one on a removed or missing listing just goes."""
    for m in q("SELECT key, value FROM settings WHERE key LIKE ?", ("rental_letting:%",)):
        try:
            rid = int(m["key"].split(":", 1)[1])
        except (TypeError, ValueError):
            continue
        r = q("SELECT status FROM rentals WHERE id=?", (rid,))
        if r and r[0]["status"] == LET:
            _finish_letting(rid, _letting_keep(m["value"]))
        elif not r or r[0]["status"] == REMOVED:
            execute("DELETE FROM settings WHERE key=?", (m["key"],))


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
                        # everyone who still lives there (each has an "End stay" button).
                        if _finish_tenancies(rid, uid):
                            msg += " The last tenant's booking is closed and they've been told."
                        elif not single:
                            msg += (" Your current tenants' bookings stay as they are — use “End stay” "
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
                    done = edit_listing(rid, uid, vals)
                    st.session_state[FLASH] = (
                        "Saved your changes." if done else
                        ("warn", "Nothing was saved: more than one guest holds a booking on this place — paid, "
                                 "or being paid — so it can't become a whole flat or a private room; those take "
                                 "one booking at a time. Once only one is left you can change it: cancel a "
                                 "booking that isn't going ahead, or use “End stay” on a guest who has moved "
                                 "out.")
                        if _is_single_place(vals) and q(f"SELECT 1 FROM rentals WHERE id=? AND {_TWO_HOLDERS}",
                                                        (rid,)) else
                        "That listing can't be edited any more.")
                    st.rerun()

        reqs = [dict(x) for x in q(
            f"SELECT rr.*, {_USER_COLS} FROM rental_requests rr JOIN users u ON u.id = rr.user_id "
            "WHERE rr.rental_id=? ORDER BY rr.id DESC", (rid,))]
        # A guest's payment recorded apart from her booking is the team's to sort out, not the owner's.
        live = [x for x in reqs if x["status"] in OPEN_REQUEST
                or (x["payment_status"] in _MONEY_LIVE and x["status"] != RECORDED)]
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


_EDITED = ("kind", "title", "description", "area", "rent", "deposit", "available_from", "sharing",
           "gender_pref", "amenities", "address", "photo")


def edit_listing(rental_id, uid, vals):
    """Save the owner's edit to her own listing (never a removed one; a new photo replaces the
    old, no photo keeps it). Returns the id, or None if nothing was saved.

    A whole flat or a private room takes one booking at a time, so an edit that would turn a
    shared place into one is refused while more than one open request on it has a booking
    amount claimed or paid — in the same statement. A claim that commits while this runs is
    invisible to that statement on Postgres, so once the edit has committed the place is
    settled the way a claim's own re-check settles a single place (_close_claims_giving_way):
    a claim that has to give way — the later of two — is closed as Refund check, its
    reference kept, in words that don't blame a race. That claim runs the same re-check after
    its own commit, so whichever of the two runs second closes it, once. The edit stands either
    way: nothing is put back, so a place never ends up shared again with a claim on it closed
    as if it were single. Stopped before that check, finish_interrupted does it."""
    single = _is_single_place(vals)
    before = q("SELECT kind, sharing FROM rentals WHERE id=? AND owner_user_id=?", (rental_id, uid))
    turning = single and bool(before) and not _is_single_place(dict(before[0]))
    guard = f" AND (kind='Whole flat' OR sharing='Private' OR NOT {_TWO_HOLDERS})" if single else ""
    done = execute(
        "UPDATE rentals SET kind=?, title=?, description=?, area=?, rent=?, deposit=?, "
        "available_from=?, sharing=?, gender_pref=?, amenities=?, address=?, "
        f"photo=COALESCE(?, photo) WHERE id=? AND owner_user_id=? AND status<>?{guard} RETURNING id",
        (*(vals[k] for k in _EDITED), rental_id, uid, REMOVED))
    if done and turning:
        _close_claims_giving_way(rental_id, race=False)
    return done


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
            # No booking amount is asked for, changed or cleared while the team has the listing
            # on hold or has removed it — set_booking_amount refuses it there too.
            _owner_money(uid, rental, x, can_ask=standing is None and not blocked and not held
                         and rental["status"] not in (HOLD, REMOVED))
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
                with st.popover("End stay"):
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
    who is verified and not on hold, only while the listing is live, paused or let (never
    while the team has it on hold or has removed it), and on a single place only while
    nobody else has a booking amount claimed or paid on it."""
    s = split_on_top(amount)
    # What it asked for before, for the take-back below: a change that loses the race goes
    # back to the amount the guest was already told about, as if it had never run.
    before = q("SELECT booking_amount, platform_fee, ops_fee, customer_total, payment_status "
               "FROM rental_requests WHERE id=?", (request_id,))
    prev = dict(before[0]) if before and before[0]["payment_status"] == UNPAID else {}
    done = execute(
        "UPDATE rental_requests SET booking_amount=?, platform_fee=?, ops_fee=?, customer_total=?, "
        f"payment_status=? WHERE id=? AND status=? AND (payment_status IS NULL OR payment_status=?) "
        f"AND {_owner_request_guard()} AND ? IN ({_OWNER_IN_GOOD_STANDING}) "
        "AND rental_id IN (SELECT id FROM rentals WHERE status IN (?,?,?)) "
        f"AND {_ONE_BOOKING_PER_SINGLE_PLACE} RETURNING id",
        (s["base"], s["platform_fee"], s["ops_fee"], s["customer_total"], UNPAID, request_id, ACCEPTED,
         UNPAID, owner_uid, owner_uid, ACTIVE, PAUSED, LET))
    # Post-commit re-check: a claim on the same single place that committed while this ran
    # was invisible to the statement above. Take the amount back before the guest is asked
    # to pay a place someone else now holds (only the amount just set, only while unpaid).
    if done and execute(
            "UPDATE rental_requests SET booking_amount=?, platform_fee=?, ops_fee=?, customer_total=?, "
            "payment_status=? WHERE id=? AND status=? AND payment_status=? "
            f"AND ABS(customer_total - ?) < 0.005 AND {_HELD_ELSEWHERE} RETURNING id",
            (prev.get("booking_amount"), prev.get("platform_fee"), prev.get("ops_fee"),
             prev.get("customer_total"), UNPAID if prev else None,
             request_id, ACCEPTED, UNPAID, s["customer_total"])):
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
    changed it in the meantime the claim fails instead of attaching to the wrong figure. (The
    page passes the amount its pay form was drawn with, _pay_clicked, and records the payment
    some other way when this fails, record_unclaimed_payment.)
    Also only while the guest is not on hold, the owner is verified and not on hold, the
    listing isn't held or removed, and (for a single place) nobody else has booked it.

    Two guests claiming the same single place at once can both get past that last check on
    Postgres, so once the claim has committed _single_place_backoff runs. Each claimer runs
    it, and whichever runs first after both have committed closes the claim that must give
    way — which may be the OTHER guest's, before that guest's own re-check has looked. So
    the claim is read back afterwards: None is returned when it is no longer held (Refund
    check — closed by either re-check, or by anything else that called the booking off at
    that moment), and the request id only for a claim that still stands. A claim the team
    has confirmed in the meantime still stands.

    What a read-back cannot see is a rival claim still on its way: one whose statement began
    before this claim committed can commit after the read-back and, as the earlier request,
    take the place. That guest was told "thanks" truthfully at the time; the back-off then
    messages her and her card says someone else booked it a moment before her.

    A reference stands for one payment, so it is refused while another row of hers counts it
    (_REF_ON_ANOTHER_ROW) — above all a payment of hers recorded for the team, which may already
    be on its way back to her. Two tabs writing one reference at the same instant can both get
    past that on Postgres, so _settle_reference runs after the commit too, and a claim that has
    to give way goes back to Unpaid, its reference left on the other row (read back as above)."""
    single_before = _on_single_place(request_id)
    done = execute(
        "UPDATE rental_requests SET payment_status=?, payment_ref=? WHERE id=? AND user_id=? AND status=? "
        "AND payment_status=? AND ABS(customer_total - ?) < 0.005 "
        f"AND user_id IN ({_NOT_HELD_USERS}) AND rental_id IN "
        "(SELECT r.id FROM rentals r WHERE r.status IN (?,?,?) "
        f" AND r.owner_user_id IN ({_OWNER_IN_GOOD_STANDING})) "
        f"AND {_ONE_BOOKING_PER_SINGLE_PLACE} AND NOT {_REF_ON_ANOTHER_ROW} RETURNING id",
        (CLAIMED, ref, request_id, uid, ACCEPTED, UNPAID, float(expected_total), ACTIVE, PAUSED, LET, ref))
    if done is None:
        return None
    # The reference first: a claim that gives way on the place keeps its reference as a Refund
    # check, so one that has to give way to another row of hers must go back before that.
    _settle_reference(uid, ref)
    if single_before:
        _single_place_backoff(done)
    else:
        # A place that turned single under this claim (an owner's edit) wasn't booked "a
        # moment before" her, so whichever re-check closes it says so in other words.
        _single_place_backoff(done, race=False)
    back = q("SELECT payment_status FROM rental_requests WHERE id=?", (done,))
    if not back or back[0]["payment_status"] not in (CLAIMED, *_PAID_IN):
        return None
    return done


def _on_single_place(request_id):
    return bool(q(f"SELECT 1 FROM rental_requests WHERE id=? AND rental_id IN ({_SINGLE_PLACES})", (request_id,)))


def _single_place_backoff(request_id, race=True):
    """The post-commit re-check after a claim, in the spirit of social.py's
    _over_capacity_void: _close_claims_giving_way on the claimed request's place. Run by
    every claimer after its own commit, so whichever claim commits second sees both and the
    loser is closed; the earliest never is. race: as _close_claims_giving_way takes it.
    Returns the ids it closed."""
    rows = q("SELECT rental_id FROM rental_requests WHERE id=?", (request_id,))
    return _close_claims_giving_way(rows[0]["rental_id"], race=race) if rows else []


def _settle_reference(user_id, ref):
    """The post-commit re-check after a payment reference is written — by a claim, or by
    record_unclaimed_payment. One reference is one payment, counted on one row: a later
    "Payment recorded" row that an earlier row of hers already covers is removed
    (_DUPLICATE_RECORD), and a claim that has to give way (_REF_GIVES_WAY) is sent back
    (_send_back_reused_claims). Both writers run it after their own commit, so whichever runs
    second sees both rows; finish_interrupted runs the same for everyone in case neither did.
    Returns the claims it sent back."""
    if not ref:
        return []
    # Read first: a write is the only thing here that can raise on a dropped connection.
    if q(f"SELECT 1 FROM rental_requests WHERE user_id=? AND payment_ref=? AND {_DUPLICATE_RECORD} LIMIT 1",
         (user_id, ref)):
        execute(f"DELETE FROM rental_requests WHERE user_id=? AND payment_ref=? AND {_DUPLICATE_RECORD}",
                (user_id, ref))
    return _send_back_reused_claims("user_id=? AND payment_ref=?", (user_id, ref))


def _send_back_reused_claims(where="1=1", args=()):
    """Every open claim (matching `where`) whose reference another row of the same guest
    already counts (_REF_GIVES_WAY) goes back to Unpaid with its reference cleared — the
    reference stays on that other row, where the team acts on it — and the guest is told. One
    conditional UPDATE each, so running it again changes nothing. Returns the ids sent back."""
    sent = []
    for x in q(f"SELECT id, user_id, rental_id, payment_ref FROM rental_requests WHERE {where} "
               f"AND status IN (?,?) AND payment_status=? AND {_REF_GIVES_WAY} ORDER BY id",
               (*args, REQUESTED, ACCEPTED, CLAIMED)):
        if execute(f"UPDATE rental_requests SET payment_status=?, payment_ref=NULL WHERE id=? AND status IN (?,?) "
                   f"AND payment_status=? AND payment_ref=? AND {_REF_GIVES_WAY} RETURNING id",
                   (UNPAID, x["id"], REQUESTED, ACCEPTED, CLAIMED, x["payment_ref"])):
            sent.append(x["id"])
            rows = q("SELECT title FROM rentals WHERE id=?", (x["rental_id"],))
            title = rows[0]["title"] if rows else "the place"
            _tell(x["user_id"], "Payment reference already recorded",
                  f"The payment reference {x['payment_ref']} you gave for “{title}” is already recorded with "
                  "GharSe on another of your payments, so it can't be counted for this booking too — one "
                  "payment is counted once. This booking's amount is still unpaid: if you still want it, pay "
                  "that amount from Rooms & PGs → My requests and enter the new payment's reference. Reply to "
                  "the GharSe team if that isn't right.", ref=x["id"])
    return sent


def _tell_taken(user_id, request_id, title, ref):
    _tell(user_id, _RACE_KIND,
          f"Someone else booked “{title}” a moment before you, so your booking can't go ahead and "
          "your request is closed. If you've already paid, the GharSe team will match your payment "
          f"(reference {ref or '—'}) and refund it in full.", ref=request_id)


def _close_claims_giving_way(rental_id, race=True):
    """If the place is a single place, every open claim on it that must give way
    (_GIVES_WAY) is closed as Refund check — the reference stays, because the guest may well
    have paid already, so the team matches it and refunds it — and that guest is told. Each
    is one conditional UPDATE, so running it again closes only what is still left. Returns
    the ids it closed.

    race: the claim was made a moment ago on a place that was already single
    (_single_place_backoff), so the guest is told someone else booked it a moment before her.
    False says only that another booking holds the place: from a claim or an owner's edit
    that turned the place single (edit_listing), where nobody got there "a moment before"
    anyone, and from finish_interrupted, which can't tell a race whose re-check never ran from
    a place made single with two claims on it."""
    rows = q("SELECT title FROM rentals WHERE id=?", (rental_id,))
    title = rows[0]["title"] if rows else "the place"
    closed = []
    for x in q(f"SELECT id, user_id, payment_ref FROM rental_requests WHERE rental_id=? AND status IN (?,?) "
               f"AND payment_status=? AND {_GIVES_WAY} ORDER BY id", (rental_id, REQUESTED, ACCEPTED, CLAIMED)):
        if execute(f"UPDATE rental_requests SET status=?, payment_status=? WHERE id=? AND status IN (?,?) "
                   f"AND payment_status=? AND {_GIVES_WAY} RETURNING id",
                   (CLOSED, REFUND_CHECK, x["id"], REQUESTED, ACCEPTED, CLAIMED)):
            closed.append(x["id"])
            if race:
                _tell_taken(x["user_id"], x["id"], title, x["payment_ref"])
            else:
                _tell(x["user_id"], _GAVE_WAY_KIND,
                      f"“{title}” takes one booking at a time and another guest's booking holds it, so your "
                      "booking can't go ahead and your request is closed. If you've already paid, the GharSe "
                      f"team will match your payment (reference {x['payment_ref'] or '—'}) and refund it in "
                      "full.", ref=x["id"])
    return closed


def _lost_to_rival(request_id, told=True):
    """Closed as Refund check on a single place another open request now holds, after losing
    a same-instant race (_close_claims_giving_way, or record_unclaimed_payment for a claim
    that arrived too late). told: and the guest was sent the race message — so the card
    doesn't blame a race for a place that was let to someone else, or for a claim
    finish_interrupted closed. Straight after her own claim (_lost_claim_message) it was a
    race whoever closed it, and the rival's message may not be written yet."""
    sql = (f"SELECT 1 FROM rental_requests WHERE id=? AND status=? AND payment_status=? AND {_HELD_ELSEWHERE}"
           + (" AND EXISTS (SELECT 1 FROM notifications n WHERE n.order_id = rental_requests.id AND n.kind=?)"
              if told else ""))
    return bool(q(sql, (request_id, CLOSED, REFUND_CHECK) + ((_RACE_KIND,) if told else ())))


def _lost_claim_message(request_id, ref=None, race=True):
    """What the guest is told when the claim she just made is no longer held (claim_payment
    returned None and the request is now Refund check — with her reference, when ref is
    given, i.e. it was her claim that went in): lost to a rival on a single place that
    someone else now holds, or called off at that very moment. None when the claim never
    went in, or was sent back to Unpaid (record_unclaimed_payment takes it from there).
    race=False: the place wasn't a single place when she claimed (an owner's edit made it
    one), so nobody booked it "a moment before" her."""
    rows = q("SELECT payment_status, payment_ref FROM rental_requests WHERE id=?", (request_id,))
    if not rows or rows[0]["payment_status"] != REFUND_CHECK:
        return None
    if ref is not None and rows[0]["payment_ref"] != ref:
        return None
    if _lost_to_rival(request_id, told=False):
        if not race:
            return ("This place now takes one booking at a time and another guest's booking holds it, so yours "
                    "can't go ahead. Your reference is recorded — if you've paid, the GharSe team will match it "
                    "and refund you in full.")
        return ("Someone else booked this place a moment before you. Your reference is recorded — if "
                "you've paid, the GharSe team will match it and refund you in full.")
    return ("This booking was called off just as you paid. Your reference is recorded — if you've paid, "
            "the GharSe team will match it and refund you in full.")


def _unclaimed_reason(request_id, shown):
    """Why a payment the guest was shown can't be claimed now, read for the words only (the
    write that records it is its own conditional statement): (what the team reads, what she
    is told first, what she is told last)."""
    rows = q("SELECT rr.user_id, rr.status, rr.payment_status, rr.customer_total, r.status rstatus, "
             "u.suspended, u.kyc_status, u.phone_verified FROM rental_requests rr "
             "JOIN rentals r ON r.id = rr.rental_id JOIN users u ON u.id = r.owner_user_id WHERE rr.id=?",
             (request_id,))
    x = dict(rows[0]) if rows else {}
    old, now = _money(shown["customer_total"]), _money(x.get("customer_total"))
    wait = "Don't pay anything more for now."
    if x.get("status") not in OPEN_REQUEST:
        return ("the booking was called off before the payment was claimed",
                "This booking was called off just as you paid.", "")
    if safety.is_suspended(x["user_id"]):
        return ("the guest's account was put on hold before the payment was claimed",
                "Your account was put on hold just as you paid.", wait)
    if (safety.is_suspended({"suspended": x["suspended"]}) or not kyc.is_verified(x)
            or x["rstatus"] not in (ACTIVE, PAUSED, LET)):
        return ("payments for the place were paused before the payment was claimed",
                "Payments for this place were paused just as you paid, while the GharSe team looks into it.",
                wait)
    if not x["payment_status"]:
        return ("the owner removed the booking amount before the payment was claimed",
                f"The owner removed the booking amount of {old} just before your reference arrived, so "
                "nothing was claimed.", "")
    if x["payment_status"] == UNPAID and not _same_amount(shown, x):
        return (f"the booking amount changed from {old} to {now} before the payment was claimed",
                f"The booking amount changed from {old} to {now} just before your reference arrived, so "
                "nothing was claimed at the new amount.", f"Pay {now} only if you still want to go ahead.")
    if x["payment_status"] != UNPAID:
        return ("the booking already had a payment on it", "This booking already has a payment on it.", "")
    return ("the booking changed before the payment was claimed", "This booking changed just as you paid.", "")


def record_unclaimed_payment(request_id, uid, ref, shown):
    """The guest's page showed her `shown` (_figures) to pay, she says she has paid it, and it
    can't be claimed: the owner changed or removed the amount, someone else took the place,
    payments were paused, or the booking was called off between drawing the page and her
    click. The money may well be with GharSe, so her reference and the amount she was shown
    go where the team acts on them — a Refund check under Rentals Admin → Payments — and
    nothing is ever claimed at an amount she wasn't shown. Returns what she is told.

    On a single place someone else now holds, her still-open request is closed as Refund
    check with them, as _close_claims_giving_way would have closed her claim. Anything else
    leaves the request as it is (the owner's new amount stands; a paused place may come back)
    and a separate "Payment recorded" row carries them. Each is one conditional statement
    that refuses a reference another row of hers already counts — one reference is one payment. Two
    tabs writing the same reference at once can both pass that on Postgres, so it is checked
    again once it has committed (_settle_reference): a later duplicate record removes itself,
    and a claim made with the same reference gives way to this record (finish_interrupted does
    the same if that check never ran).

    Returns None when the request isn't hers (nothing is recorded, and nothing to tell)."""
    figs = (shown["booking_amount"], shown["platform_fee"], shown["ops_fee"], shown["customer_total"])
    # No row of hers counts her reference yet — the request itself included.
    same_ref = ("NOT EXISTS (SELECT 1 FROM rental_requests o WHERE o.user_id = {t}.user_id AND o.payment_ref=? "
                "AND " + _REF_COUNTS + ")")
    rows = q("SELECT r.title FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id WHERE rr.id=?",
             (request_id,))
    title = rows[0]["title"] if rows else "the place"
    paid = f"Your payment of {_money(shown['customer_total'])} (reference {ref}) is recorded"
    if q(f"SELECT 1 FROM rental_requests WHERE id=? AND user_id=? AND {_HELD_ELSEWHERE}", (request_id, uid)):
        if execute("UPDATE rental_requests SET status=?, payment_status=?, payment_ref=?, booking_amount=?, "
                   "platform_fee=?, ops_fee=?, customer_total=? WHERE id=? AND user_id=? AND status IN (?,?) "
                   f"AND (payment_status IS NULL OR payment_status=?) AND {_HELD_ELSEWHERE} "
                   f"AND {same_ref.format(t='rental_requests')} RETURNING id",
                   (CLOSED, REFUND_CHECK, ref, *figs, request_id, uid, REQUESTED, ACCEPTED, UNPAID, ref)):
            _settle_reference(uid, ref)
            if shown.get("single", True):
                _tell_taken(uid, request_id, title, ref)
                return ("Someone else booked this place a moment before you. Your reference is recorded and the "
                        "team will refund you.")
            # The owner made the place single after her form was drawn; nobody beat her to it.
            _tell(uid, _GAVE_WAY_KIND,
                  f"“{title}” now takes one booking at a time and another guest's booking holds it, so your "
                  "booking can't go ahead and your request is closed. If you've already paid, the GharSe team "
                  f"will match your payment (reference {ref or '—'}) and refund it in full.", ref=request_id)
            return ("This place now takes one booking at a time and another guest's booking holds it, so yours "
                    "can't go ahead. Your reference is recorded and the team will refund you.")
    team_why, why, then = _unclaimed_reason(request_id, shown)
    new_id = execute(
        "INSERT INTO rental_requests (rental_id, user_id, note, booking_amount, platform_fee, ops_fee, "
        "customer_total, payment_status, payment_ref, status, created_at) "
        "SELECT src.rental_id, src.user_id, ?,?,?,?,?,?,?,?,? FROM rental_requests src WHERE src.id=? "
        f"AND src.user_id=? AND {same_ref.format(t='src')} RETURNING id",
        (f"Paid for request #{request_id}, but {team_why}.", *figs, REFUND_CHECK, ref, RECORDED, now_iso(),
         request_id, uid, ref))
    if new_id is None:
        # Refused: say which of the two it was, never guess.
        if not q("SELECT 1 FROM rental_requests WHERE id=? AND user_id=?", (request_id, uid)):
            return None
        if q(f"SELECT 1 FROM rental_requests o WHERE o.id=? AND o.payment_ref=? AND {_REF_COUNTS}",
             (request_id, ref)):
            return f"Your reference {ref} is already on this booking — see it under My requests."
        return (f"Your reference {ref} is already recorded with GharSe on another of your payments, so it "
                "can't be counted for this booking too — one payment is counted once, and My requests shows "
                "where that one stands. If you still want this booking, pay the amount shown and enter the "
                "new payment's reference.")
    _settle_reference(uid, ref)
    words = f"{why} {paid}, and the GharSe team will match it and refund it in full. {then}".strip()
    _tell(uid, "Payment recorded", f"“{title}”: {words}", ref=new_id)
    return words


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
            + (f"<span class='muted'>{h(x['note'] or '')}</span>" if x["status"] == RECORDED else
               f"<span class='muted'>Your note: {h(x['note'] or '—')}</span>"),
            unsafe_allow_html=True,
        )
        status, ps = x["status"], x["payment_status"]
        owner_id = x["owner_user_id"]
        blocked = safety.is_blocked(uid, owner_id)
        drawn = None
        if status == REQUESTED:
            st.caption("Waiting for the owner. When she accepts you'll see the address and her number here.")
        elif status == ACCEPTED and blocked:
            st.caption("One of you has blocked the other, so contact details are hidden.")
        elif status == ACCEPTED:
            # The address and both numbers are shown only while neither account is on hold,
            # the owner's identity is still verified and the team hasn't held the listing.
            # Whatever the owner-side reason, the guest gets the same words as a paused
            # payment — which of the three it is stays with the team.
            if safety.is_suspended(uid):
                st.caption("Your account is on hold while the GharSe team reviews it, so the address "
                           "and the owner's number are hidden for now.")
            elif (safety.is_suspended(owner_id) or x["rstatus"] in (HOLD, REMOVED)
                  or not kyc.is_verified(x)):
                st.warning("The address and the owner's number for this place are hidden " + _UNDER_REVIEW)
            else:
                owner = _contact(owner_id, OWNER_FALLBACK)
                _contact_block("Owner", owner,
                               f"Hi {owner['first']}, this is about my GharSe visit to “{x['title']}”.",
                               key=f"mr_wa{xid}", address=_private_details(x["rental_id"])["address"])
            drawn = _guest_money(uid, x)
        late = st.session_state.get(_late_key(xid))
        if late and ((drawn and _same_amount(late, drawn)) or ps not in (UNPAID, None)):
            # Her form is back for the same amount, or her reference is on this request already
            # (a click whose connection dropped after it was saved).
            st.session_state.pop(_late_key(xid), None)
        elif late:
            # She typed a reference that couldn't be read into a form showing this amount, and
            # the booking has changed since: the form she was using stays, for the amount she saw.
            st.caption(f"You were paying {_money(late['customer_total'])} when this booking changed. If you've "
                       "already paid it, enter the reference so the GharSe team can match your payment.")
            _pay_form(uid, xid, late, late=True)
        if ps in (REFUND_CHECK, REFUND_DUE, REFUNDED) and status != ACCEPTED:
            st.markdown(_row_split(x, "guest"), unsafe_allow_html=True)
            # A claim can lose a same-instant race even after the page said thanks (the rival's
            # claim commits a moment later), so the card says so itself.
            lost = ("Someone else booked this place a moment before you. "
                    if ps == REFUND_CHECK and _lost_to_rival(xid) else "")
            st.caption(lost + {REFUND_CHECK: "The GharSe team is matching your payment; once it's found it's "
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
        elif ps in (PAID_OUT, RELEASED) and (status == ACCEPTED or x["rstatus"] == REMOVED):
            st.markdown(safety.grievance_line(), unsafe_allow_html=True)


def _figures(x):
    """A request's four money figures, as the page shows them."""
    return {k: _num(x[k]) for k in ("booking_amount", "platform_fee", "ops_fee", "customer_total")}


def _same_amount(a, b):
    return abs(_num(a["customer_total"]) - _num(b["customer_total"])) < 0.005


def _late_key(request_id):
    return f"rentals_late{int(request_id)}"


def _norm_ref(ref):
    """A payment reference as GharSe stores and compares it. Spaces, dashes and letter case
    don't make it another reference, and neither does a prefix: a UPI/IMPS UTR or RRN is a
    run of 12 digits, so when the reference holds a run of 10 or more digits, that run (the
    last one) is the reference — 'UTR 4123 4567 8901' and '412345678901' are one payment."""
    ref = re.sub(r"[^0-9A-Za-z]", "", str(ref or "")).upper()
    runs = re.findall(r"[0-9]{10,}", ref)
    return runs[-1] if runs else ref


def _pay_flash_key(uid):
    return f"{PAY_CLICK}{int(uid)}"


def _pay_clicked(uid, request_id, figures, ref_key):
    """on_click of a pay form. Streamlit runs it with the arguments the form was DRAWN with —
    `uid` the account the page was drawn for, `figures` the amount she was looking at,
    whatever the owner has set since — before the rerun her click starts, and whatever that
    rerun draws: the place may have been taken, payments paused or the booking called off,
    and for an account put on hold app.py draws its hold page and nothing else. So the click
    is acted on here and now, for that account (_act_on_pay_click), and what she is told
    waits under her own uid for her next Rooms & PGs page (_pay_flash_key). Nothing of the
    click itself waits for a later page, so none of it is lost when she signs out, and none
    of it reaches whoever signs in next. Her reference box is emptied as it is taken, so the
    form is never drawn again holding a reference that has been used."""
    ref = st.session_state.get(ref_key) or ""
    st.session_state[ref_key] = ""
    try:
        told = _act_on_pay_click(uid, request_id, figures, ref)
    except Exception as e:
        # A callback runs before the page, out of reach of app.py's notice for a dropped
        # connection, so its words wait for her page instead. A write is never sent twice,
        # so whatever was saved is what My requests shows her now.
        if not store._connection_lost(e):
            raise
        # Her reference may never have been written, and the booking may have changed since
        # the page was drawn, so the form she used stays for the amount she was shown — a
        # re-entry there claims at exactly that amount or is recorded, and a reference that
        # was saved is refused as already on this booking.
        st.session_state[_late_key(request_id)] = figures
        told = ("warn", "The connection to GharSe's database dropped while your payment reference was being "
                        "saved, so it may or may not have been recorded. Check this booking under My requests: "
                        "if it shows no payment, enter the reference again in the form for "
                        f"{_money(figures['customer_total'])}, the amount you paid.")
    st.session_state[_pay_flash_key(uid)] = told


def _pay_form(uid, request_id, figures, late=False):
    """The reference form, bound to the account and the figures drawn with it (_pay_clicked).
    late: the same form kept for an amount the guest was paying when the booking changed
    under her."""
    key, ref_key = ((f"mr_late{request_id}", f"mr_lateutr{request_id}") if late else
                    (f"mr_pay{request_id}", f"mr_utr{request_id}"))
    with st.form(key, border=False):
        pc = st.columns([3, 1])
        pc[0].text_input("UPI reference / UTR number", key=ref_key)
        pc[1].form_submit_button("Record my payment" if late else "I've paid", on_click=_pay_clicked,
                                 args=(uid, request_id, figures, ref_key))


def _act_on_pay_click(uid, request_id, shown, ref):
    """Act on one pay click with the amount the guest was shown: claim it at exactly that
    amount, or — when it can't be claimed any more — record her reference and that amount for
    the team (record_unclaimed_payment), never at any other amount. Returns what she is told."""
    # One payment, one reference: spaces, dashes, case and a prefix don't make it another.
    ref = _norm_ref(ref)
    if len(ref) < 6:
        # The form she typed into is kept, for the amount she saw, even if the booking has
        # changed meanwhile and the page would no longer offer it (see _my_request_card).
        st.session_state[_late_key(request_id)] = shown
        return ("warn", "Enter the reference number your UPI app showed after payment.")
    st.session_state.pop(_late_key(request_id), None)
    single = _on_single_place(request_id)
    if claim_payment(request_id, uid, ref, shown["customer_total"]):
        return "Thanks — the GharSe team will confirm your payment shortly."
    # Her claim went in and was closed straight away (a same-instant race, or called off) —
    # her reference is on it already. Otherwise it never went in, or went back to Unpaid
    # because another row of hers already counts that reference, and record_unclaimed_payment
    # takes it from there.
    return (_lost_claim_message(request_id, ref, race=single)
            or ("warn", record_unclaimed_payment(request_id, uid, ref, shown)
                or "That booking can't be found any more — refresh the page."))


def _guest_money(uid, x):
    """The money part of an accepted request. Returns the figures its pay form is bound to,
    or None when it draws no pay form."""
    ps = x["payment_status"]
    xid = x["id"]
    if not ps:
        st.caption("No booking amount asked for yet. If the owner asks for a token or the first month, "
                   "you'll pay it here — never in cash or to her directly.")
        return None
    st.markdown(_row_split(x, "guest"), unsafe_allow_html=True)
    if ps == UNPAID:
        if safety.is_suspended(uid):
            st.caption("Your account is on hold while the GharSe team reviews it, so you can't pay "
                       "right now. Don't pay anything for now.")
            return None
        owner_ok = (not safety.is_suspended(x["owner_user_id"]) and kyc.is_verified(x)
                    and x["rstatus"] in (ACTIVE, PAUSED, LET))
        if _is_single_place({"kind": x["kind"], "sharing": x.get("sharing")}) and \
                _other_booking_taken(x["rental_id"], xid):
            st.warning("Someone else has already booked this place, so it can't take your booking "
                       "amount. Don't pay anything for it.")
            return None
        if not owner_ok:
            st.warning("Payments for this place are paused " + _UNDER_REVIEW)
            return None
        figures = _figures(x)
        # Whether the place took one booking at a time when this form was drawn: if it
        # didn't, a booking that holds it when she clicks didn't beat her "a moment before".
        figures["single"] = _is_single_place({"kind": x["kind"], "sharing": x.get("sharing")})
        total = figures["customer_total"]
        link = _upi_link(total, f"GharSe room {xid}")
        pa = (setting("platform_upi", str) or "").strip()
        if link:
            st.link_button(f"Pay {_money(total)} by UPI", link, width="stretch")
            st.caption(f"Or send {_money(total)} to {h(pa)} from any UPI app, then enter the reference below. "
                       "Pay only after you've visited.")
        else:
            st.caption("Payment isn't switched on yet — the GharSe team still needs to add the platform "
                       "UPI ID. Don't pay anyone directly.")
        _pay_form(uid, xid, figures)
        return figures
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
    """Claimed → Paid — never for a claim that has to give way on a single place (_GIVES_WAY).
    Such a claim exists in three cases: for a moment, between two writes racing on the place
    committing — two claims, or a claim and an owner's edit that makes the place single —
    and the post-commit re-check that closes the later claim (_single_place_backoff, or
    edit_listing's); for good, when the write that should have run that re-check failed
    after committing, so it never ran; and when a listing was changed outside the app.
    finish_interrupted closes the last two when Rentals Admin opens; the first closes itself.

    Nor for a claim whose payment reference another row of the guest's already counts
    (_REF_GIVES_WAY) — for the moment before _settle_reference sends it back, or for good
    when that re-check never ran (finish_interrupted sends it back): one payment is never
    confirmed as a booking while it is also recorded for a refund."""
    return execute(f"UPDATE rental_requests SET payment_status=? WHERE id=? AND payment_status=? "
                   f"AND NOT ({_GIVES_WAY}) AND NOT ({_REF_GIVES_WAY}) RETURNING id", (PAID, request_id, CLAIMED))


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
_PAYABLE_LISTING = f"(rr.payment_status='{RELEASED}' OR r.status NOT IN ('{HOLD}','{REMOVED}'))"
_PAYABLE_OWNER = f"r.owner_user_id IN ({_OWNER_IN_GOOD_STANDING})"
_PAYABLE_SQL = f"{_PAYABLE_LISTING} AND {_PAYABLE_OWNER}"


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


def refund_frozen(request_id, payment_status=PAID):
    """Frozen → Refund due, from where it goes through Payments like any other refund.
    payment_status is the state the team was shown — Paid, or Released when a booking
    already released to the owner is frozen again because the owner went on hold or lost
    her verification. Only while it is still in that state and still not payable
    (_PAYABLE_SQL), so one that was paid out, released or restored meanwhile is left alone.
    An open request is closed with it. The guest and the owner are told."""
    if payment_status not in (PAID, RELEASED):
        return None
    done = execute(
        "UPDATE rental_requests SET payment_status=?, status=CASE WHEN status IN (?,?) THEN ? ELSE status END "
        "WHERE id=? AND payment_status=? AND NOT EXISTS (SELECT 1 FROM rental_requests rr "
        f"JOIN rentals r ON r.id = rr.rental_id WHERE rr.id = rental_requests.id AND {_PAYABLE_SQL}) "
        "RETURNING id", (REFUND_DUE, REQUESTED, ACCEPTED, CLOSED, request_id, payment_status))
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
    is still Paid. The owner is told. Both halves are the ones _PAYABLE_SQL is made of: the
    listing half fails now, and the owner half holds."""
    done = execute(
        "UPDATE rental_requests SET payment_status=? WHERE id=? AND payment_status=? AND EXISTS "
        "(SELECT 1 FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
        f" WHERE rr.id = rental_requests.id AND NOT {_PAYABLE_LISTING} AND {_PAYABLE_OWNER}) RETURNING id",
        (RELEASED, request_id, PAID))
    if done:
        x = q("SELECT rr.booking_amount, r.title, r.owner_user_id FROM rental_requests rr "
              "JOIN rentals r ON r.id = rr.rental_id WHERE rr.id=?", (request_id,))[0]
        _tell(x["owner_user_id"], "Payout released",
              f"The GharSe team has released {_money(x['booking_amount'])} for “{x['title']}” "
              f"(request #{request_id}). It will be sent to you with your next payout.", ref=request_id)
    return done


def record_payout(owner_uid, request_ids, method, ref):
    """Record one payout for exactly the bookings this call flips Paid (or Released) → Paid out.

    The payout row is written FIRST and empty, then each booking is flipped by its own
    conditional UPDATE (_PAYABLE_SQL — owner in good standing, listing not held or removed
    unless the team released it — re-checked in the same statement), and only then is what
    flipped counted into the row, in one UPDATE. A second click flips nothing, and a row
    nothing flipped for is deleted. Returns (amount, count) or None.

    A write can fail after it has committed, so this can stop between any two statements —
    and wherever it stops, its row is still empty: an empty row is a recording that stopped
    (or is still running). Owner payouts warns about it before anyone sends that owner money
    again, and the bookings it never reached are still Paid and still owed. Once the row is
    older than _PAYOUT_SETTLE, _complete_payout_records counts what it did flip into it; a
    row it flipped nothing for stays, listed on Owner payouts with its reference, until the
    bookings that money paid are recorded again, later, under that same reference or the team
    dismisses it.
    So a booking is never paid out without a record of it, and never recorded twice."""
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
            total += _num(q("SELECT booking_amount FROM rental_requests WHERE id=?", (rid,))[0]["booking_amount"])
            count += 1
    if not count:
        execute("DELETE FROM social_payouts WHERE id=? AND items_count=0", (payout_id,))
        return None
    total = round(total, 2)
    execute("UPDATE social_payouts SET amount=?, items_count=? WHERE id=? AND items_count=0",
            (total, count, payout_id))
    _tell(owner_uid, "Payout",
          f"{_money(total)} has been sent to you for {count} room booking{'s' if count != 1 else ''} on "
          f"GharSe. {method} reference {ref}.")
    return total, count


# A payout row younger than this may belong to a record_payout still running, so
# _complete_payout_records leaves its owner alone until then — far longer than recording
# a payout ever takes.
_PAYOUT_SETTLE = timedelta(minutes=10)


def _settle_cutoff():
    return (now_ist() - _PAYOUT_SETTLE).isoformat(timespec="seconds")


# What the owner of the social_payouts row in hand is short by: her Paid out bookings less her
# rental payout rows. Above zero, a recording stopped before counting what it flipped.
_PAYOUT_SHORTFALL = (
    "((SELECT COALESCE(SUM(rr.booking_amount),0) FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
    "WHERE r.owner_user_id = social_payouts.user_id AND rr.payment_status='Paid out') "
    "- (SELECT COALESCE(SUM(x.amount),0) FROM social_payouts x WHERE x.user_id = social_payouts.user_id "
    "AND x.source='rental'))")
# An empty rental payout row: a recording that stopped before counting anything (record_payout).
_EMPTY_PAYOUT = "items_count=0 AND ABS(amount) < 0.005"
# An empty row whose reference is also on a LATER payout row of the owner's that did count
# bookings — the money that went out under it is on record there (as the warning on Owner
# payouts asks: the bookings it paid recorded again with the same reference). An older row
# with the same reference never covers it: a reference the team reuses for one owner ("cash",
# a resent UTR) would otherwise hide a stopped recording and let the owner be paid twice.
_COVERED_PAYOUT = (
    "EXISTS (SELECT 1 FROM social_payouts c WHERE c.user_id = social_payouts.user_id AND c.source='rental' "
    "AND c.ref = social_payouts.ref AND c.id > social_payouts.id AND c.items_count > 0)")

# For _complete_payout_records: set one row of the owner's so that her payout rows add up to
# her Paid out bookings — amount and count read from the bookings and her OTHER rows, so
# running it twice changes nothing. The row is her oldest empty one (the shortfall is what a
# stopped recording flipped and never counted, and an empty row is a stopped recording),
# else her latest; only while none of her rows is younger than the cutoff (a recording may
# still be running) and her rows are short.
_COMPLETE_PAYOUT_SQL = (
    "UPDATE social_payouts SET "
    "amount = (SELECT COALESCE(SUM(rr.booking_amount),0) FROM rental_requests rr JOIN rentals r "
    " ON r.id = rr.rental_id WHERE r.owner_user_id=? AND rr.payment_status='Paid out') "
    " - (SELECT COALESCE(SUM(x.amount),0) FROM social_payouts x WHERE x.user_id=? AND x.source='rental' "
    " AND x.id <> social_payouts.id), "
    "items_count = (SELECT COUNT(*) FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
    " WHERE r.owner_user_id=? AND rr.payment_status='Paid out') "
    " - (SELECT COALESCE(SUM(x.items_count),0) FROM social_payouts x WHERE x.user_id=? AND x.source='rental' "
    " AND x.id <> social_payouts.id) "
    "WHERE id = COALESCE("
    " (SELECT MIN(y.id) FROM social_payouts y WHERE y.user_id=? AND y.source='rental' "
    "  AND y.items_count=0 AND ABS(y.amount) < 0.005), "
    " (SELECT MAX(y.id) FROM social_payouts y WHERE y.user_id=? AND y.source='rental')) "
    "AND NOT EXISTS (SELECT 1 FROM social_payouts y WHERE y.user_id=? AND y.source='rental' AND y.created_at >= ?) "
    "AND " + _PAYOUT_SHORTFALL + " > 0.005 RETURNING id")


def _complete_payout_records():
    """Every booking marked Paid out is in its owner's payout records. A record_payout that
    stopped after flipping bookings leaves her bookings worth more than her records, and its
    row empty; the difference goes onto her oldest empty row — so a transfer recorded after
    it under another reference is never charged with bookings it didn't pay — once none of
    her rows is younger than _PAYOUT_SETTLE, so a recording still running is never counted
    twice. (Her latest row takes it when she has no empty row: a stop from before every
    recording counted at the end.) With two stopped recordings for one owner this is a
    guess: the bookings carry no payout id, so the total lands on the oldest empty row even
    if the later one is the transfer that went out, and the other stays listed — her totals
    are right, the reference beside them may not be.

    An empty row is never deleted while it may be the only record of the reference money
    went out under: only once it is older than the cutoff, her records aren't short, and a
    later row of hers that counted bookings carries the same reference (_COVERED_PAYOUT).
    Any other empty row stays, listed on Owner payouts (_unfinished_payouts) until the team
    dismisses it. Returns the owners whose records it completed."""
    cutoff = _settle_cutoff()
    flipped = {r["uid"]: _num(r["s"]) for r in q(
        "SELECT r.owner_user_id uid, SUM(rr.booking_amount) s FROM rental_requests rr JOIN rentals r "
        "ON r.id = rr.rental_id WHERE rr.payment_status=? GROUP BY r.owner_user_id", (PAID_OUT,))}
    recorded = {r["user_id"]: _num(r["s"]) for r in q(
        "SELECT user_id, SUM(amount) s FROM social_payouts WHERE source='rental' GROUP BY user_id")}
    done = []
    for uid, s in flipped.items():
        if s - recorded.get(uid, 0.0) > 0.005 and execute(
                _COMPLETE_PAYOUT_SQL, (uid, uid, uid, uid, uid, uid, uid, cutoff)):
            done.append(uid)
    # Read first: this runs on every Rentals Admin render, and there only a write can raise on
    # a dropped connection (a read is retried).
    covered = (f"source='rental' AND {_EMPTY_PAYOUT} AND created_at < ? AND {_PAYOUT_SHORTFALL} <= 0.005 "
               f"AND {_COVERED_PAYOUT}")
    if q(f"SELECT 1 FROM social_payouts WHERE {covered} LIMIT 1", (cutoff,)):
        execute(f"DELETE FROM social_payouts WHERE {covered}", (cutoff,))
    return done


def _stopped_payouts():
    """{owner_user_id: payout row} for each owner whose payout records show a recording that
    stopped (or is still running): an empty row of hers that nothing covers yet — record_payout's
    row stays empty until it has finished — or records short of her Paid out bookings. The row
    named is her latest such empty one, else her latest."""
    short = {r["user_id"]: dict(r) for r in q(
        "SELECT user_id, amount, ref, created_at FROM social_payouts WHERE id IN (SELECT MAX(m.id) "
        "FROM social_payouts m WHERE m.source='rental' GROUP BY m.user_id) AND " + _PAYOUT_SHORTFALL + " > 0.005")}
    empty = {r["user_id"]: r for r in _unfinished_payouts()}
    return {**short, **empty}


def _unfinished_payouts():
    """Every empty rental payout row nothing covers yet (see _complete_payout_records), oldest
    first — for an owner with more than one, her latest is the last."""
    return [dict(r) for r in q(
        "SELECT id, user_id, amount, method, ref, created_at FROM social_payouts WHERE source='rental' "
        f"AND {_EMPTY_PAYOUT} AND NOT {_COVERED_PAYOUT} ORDER BY id")]


def dismiss_unfinished_payout(payout_id):
    """The team has checked the bank: nothing went out under this stopped recording's reference
    (or what did is recorded elsewhere). Deleted only while it is still empty, can no longer be
    a recording still running, and its owner's records aren't short — a short owner's empty row
    is where the repair counts what it flipped."""
    return execute(
        f"DELETE FROM social_payouts WHERE id=? AND source='rental' AND {_EMPTY_PAYOUT} AND created_at < ? "
        f"AND {_PAYOUT_SHORTFALL} <= 0.005 RETURNING id", (payout_id, _settle_cutoff()))


def _finish_removals():
    """A removal stopped part-way through closing the listing's requests (a write can fail
    after committing) leaves some open on a listing that is gone; close them now."""
    for r in q("SELECT DISTINCT rr.rental_id FROM rental_requests rr JOIN rentals r ON r.id = rr.rental_id "
               "WHERE r.status=? AND rr.status IN (?,?) "
               "AND (rr.payment_status IS NULL OR rr.payment_status IN (?,?,?,?,?))",
               (REMOVED, REQUESTED, ACCEPTED, UNPAID, CLAIMED, PAID, RELEASED, PAID_OUT)):
        _close_open_requests(r["rental_id"], "it was taken down", refund_paid=True)


def finish_interrupted():
    """Finish what a stopped change left half done. store.py sends a Postgres write once and
    raises if the connection drops, and the write may have committed, so a change made of
    several statements can stop between any two of them; each of these repairs is safe to
    run any number of times, and Rentals Admin runs them whenever it opens:
      * a claim whose reference another row of the same guest already counts goes back to
        Unpaid (and confirm_payment refuses it meanwhile) — its re-check never ran;
      * a claim on a single place someone else holds is closed as Refund check (and
        confirm_payment refuses it meanwhile) — its re-check never ran, or the place became
        single with two claims on it; the guest is told only that another booking holds it;
      * a payment recorded twice for the same guest keeps only its first row;
      * a mark_let that stopped after its listing turned Let is finished;
      * requests a removal left open on a removed listing are closed;
      * bookings paid out but missing from the owner's payout records are added to them."""
    # Before the place's back-off below: a claim it closed would keep the reference as well.
    _send_back_reused_claims()
    for r in q(f"SELECT DISTINCT rental_id FROM rental_requests WHERE status IN (?,?) AND payment_status=? "
               f"AND {_GIVES_WAY}", (REQUESTED, ACCEPTED, CLAIMED)):
        _close_claims_giving_way(r["rental_id"], race=False)
    # Read first: this runs on every Rentals Admin render.
    if q(f"SELECT 1 FROM rental_requests WHERE {_DUPLICATE_RECORD} LIMIT 1"):
        execute(f"DELETE FROM rental_requests WHERE {_DUPLICATE_RECORD}")
    _finish_lettings()
    _finish_removals()
    _complete_payout_records()


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
        # The owner is told before the requests are closed: if closing stops part-way,
        # finish_interrupted closes the rest, but nothing would tell her afterwards.
        _tell(r["owner_user_id"], _MOD_KINDS[1], _removal_message(rental_id, r["title"], reason), ref=rental_id)
        _close_open_requests(rental_id, "it was taken down", refund_paid=True)
    else:
        _tell(r["owner_user_id"], "Room listing restored",
              {ACTIVE: f"Your listing “{r['title']}” is live on Rooms & PGs again.",
               PAUSED: f"Your listing “{r['title']}” is no longer on hold. It is still paused — resume "
                       "it from My listings when you're ready.",
               LET: f"Your listing “{r['title']}” is no longer on hold. It is marked as let, as it was "
                    "before."}.get(r["status"], f"Your listing “{r['title']}” is no longer on hold."),
              ref=rental_id)
    return done


def _removal_message(rental_id, title, reason):
    """What the owner is told when the team removes her listing — read before its requests
    are closed. It promises a refund only for money GharSe still holds on an open request;
    a booking the team already released to her or paid out is not refunded by the removal,
    and a paid stay that has already ended waits for the team."""
    rows = q("SELECT status, payment_status FROM rental_requests WHERE rental_id=?", (rental_id,))
    refunding = any(x["status"] in OPEN_REQUEST and x["payment_status"] in (CLAIMED, PAID) for x in rows)
    held = any(x["status"] not in OPEN_REQUEST and x["payment_status"] == PAID for x in rows)
    settled = any(x["payment_status"] in (RELEASED, PAID_OUT) for x in rows)
    return (f"Your listing “{title}” has been removed from Rooms & PGs. Reason: {reason}. Open requests on "
            "it were closed"
            + (", and booking amounts guests paid on them are being refunded to them." if refunding else ".")
            + (" Booking amounts from stays that have already ended are held while the GharSe team looks "
               "into it." if held else "")
            + (" Booking amounts the GharSe team had already released to you or paid out are not refunded "
               "by this." if settled else ""))


def rentals_admin(admin_user):
    # This page shows exact addresses, phones, UPI IDs and payment references and moves
    # money, so it guards itself rather than relying on the app's routing alone.
    if (admin_user or {}).get("role") != "admin":
        st.error("Only the GharSe team can open this page.")
        return
    finish_interrupted()
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


def _shared_references():
    """{reference: [row, …]} for every payment reference that is on more than one request row —
    one payment that two cards would otherwise each count. References are compared as
    _norm_ref reads them, so one typed with a prefix or spaces (or saved before references
    were normalised) still matches; look a card up with _norm_ref(its payment_ref)."""
    out = {}
    for r in q("SELECT id, payment_ref, status, payment_status FROM rental_requests "
               "WHERE payment_ref IS NOT NULL AND payment_ref <> '' ORDER BY id"):
        out.setdefault(_norm_ref(r["payment_ref"]), []).append(dict(r))
    return {k: v for k, v in out.items() if len(v) > 1}


def _admin_payments(claims):
    if not claims:
        st.success("Nothing to check — no claimed payments or refunds waiting.")
        return
    shared = _shared_references()
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
            others = [o for o in shared.get(_norm_ref(x["payment_ref"]), []) if o["id"] != x["id"]]
            if others:
                st.warning(f"The same reference {h(x['payment_ref'])} is also on "
                           + ", ".join(f"request #{o['id']} ({h(o['status'])}, "
                                       f"{h(o['payment_status'] or 'no payment')})" for o in others)
                           + ". One payment is counted once — check the bank before confirming, refunding or "
                             "paying out against either.")
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
                    else:
                        st.session_state[ADMIN_FLASH] = (
                            "warn", (f"Request #{x['id']} wasn't confirmed — it changed, someone else holds "
                                     "this place, or its reference is already counted on another payment. "
                                     "Check it below before doing anything else."))
                    st.rerun()
                if b[1].button("Not received", key=f"ra_rej{x['id']}", width="stretch"):
                    if reject_payment(x["id"]):
                        _tell(x["user_id"], "Payment not found",
                              f"We couldn't find your payment for “{x['title']}”. Check the UPI reference "
                              "and claim it again from Rooms & PGs → My requests.", ref=x["id"])
                        st.session_state[ADMIN_FLASH] = f"Sent request #{x['id']} back to unpaid."
                    st.rerun()
            elif x["payment_status"] == REFUND_CHECK:
                st.caption((f"{h(x['note'] or '')} " if x["status"] == RECORDED else
                            "Called off before anyone confirmed the payment. ")
                           + f"Check the account for reference {h(x['payment_ref'] or '—')}: if the money "
                           "arrived it is refunded; if it never did, nothing is owed.")
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
    if not due and not frozen:
        st.success("Nothing owed — every paid booking has been settled with its owner.")
    elif not due:
        st.info("Nothing can be paid out right now — the paid bookings below are frozen.")
    # A recording that stopped part-way leaves the bookings it didn't reach still owed, with the
    # money for them quite possibly sent already — so each owner whose records show one is warned.
    stopped = _stopped_payouts() if due else {}
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
            last = stopped.get(owner_uid)
            if last:
                st.warning(f"A payout to this owner didn't finish recording (reference {h(last['ref'] or '—')}, "
                           f"{h(short_dt(last['created_at']))}, {_money(last['amount'])} recorded). If that "
                           "payment was meant to cover these bookings too, don't send the money again — "
                           "record them with the same reference.")
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
                    # Bookings can change between drawing the page and the click (paid out from
                    # another tab, refunded, frozen): say what was recorded, never guess why not.
                    if res is None:
                        st.session_state[ADMIN_FLASH] = (
                            "warn", ("Nothing recorded — those bookings changed: paid out, refunded or frozen. "
                                     "Check before sending money."))
                    elif res[1] < len(items):
                        st.session_state[ADMIN_FLASH] = (
                            "warn", (f"Recorded {_money(res[0])} to {h(owner['name'])}, not the "
                                     f"{_money(amount)} on the button — {len(items) - res[1]} of the "
                                     f"{len(items)} bookings changed: paid out, refunded or frozen. Check "
                                     "before sending money."))
                    else:
                        st.session_state[ADMIN_FLASH] = f"Recorded {_money(res[0])} to {h(owner['name'])}."
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
    _unfinished_payout_rows()
    st.markdown("#### Payout history")
    hist = q("SELECT sp.created_at, u.username owner, sp.amount, sp.items_count bookings, sp.method, sp.ref "
             "FROM social_payouts sp JOIN users u ON u.id = sp.user_id WHERE sp.source='rental' "
             "ORDER BY sp.id DESC LIMIT 200")
    table(hist, money=("amount",), empty="No payouts recorded yet.")


def _unfinished_payout_rows():
    """Payout recordings that stopped before counting anything, each with the reference the
    money may have gone out under. Kept until the bookings it paid are recorded under that
    reference (then it clears itself) or the team dismisses it."""
    rows = _unfinished_payouts()
    if not rows:
        return
    st.markdown("#### Payouts that didn't finish recording")
    st.caption("Each of these stopped before anything was counted into it, so the money may or may not have "
               "gone out under its reference. If it did, record the bookings it paid with that same reference "
               "and the row clears itself. Dismiss one only once the bank shows nothing was sent under it.")
    for p in rows:
        owner = _contact(p["user_id"])
        c = st.columns([4, 1])
        c[0].markdown(f"<span class='muted'>{h(owner['name'])} · @{h(owner['username'])} · "
                      f"{h(p['method'] or '')} reference <b>{h(p['ref'] or '—')}</b> · "
                      f"{h(short_dt(p['created_at']))}</span>", unsafe_allow_html=True)
        with c[1].popover("Dismiss", width="stretch"):
            st.caption("Only if no money went out under this reference, or what did is already recorded under "
                       "another one.")
            if st.button("Yes, nothing was sent", key=f"ra_podis{p['id']}"):
                st.session_state[ADMIN_FLASH] = (
                    f"Dismissed the unfinished payout record {h(p['ref'] or '—')}."
                    if dismiss_unfinished_payout(p["id"]) else
                    ("warn", "That record can't be dismissed right now — it may still be being recorded, or "
                             "it is being completed. Refresh the page."))
                st.rerun()


def _frozen_row(r):
    """One frozen booking: what it is, why it's held, and Refund guest / Release to owner.
    Both are single conditional UPDATEs on the state shown here, so a stale page can't
    refund a booking that was paid out meanwhile, or release one whose owner went on hold."""
    rid, ps = r["id"], r["payment_status"]
    guest = _contact(r["user_id"])
    st.markdown(f"<span class='muted'>#{h(rid)} · {h(r['title'])} · {h(guest['name'])} paid "
                f"{_money(r['customer_total'])} ({_money(r['booking_amount'])} to the owner) — "
                f"{h(r['why'])}</span> {_request_pill(r['status'])}"
                + (f" {_pill('Released', 'flat')}" if ps == RELEASED else ""), unsafe_allow_html=True)
    b = st.columns([1, 1, 3])
    with b[0].popover("Refund guest", width="stretch"):
        st.caption("The booking is closed and moves to Refund due under Payments — mark it refunded "
                   "there once the money is sent. The guest and the owner are told.")
        if st.button("Yes, refund the guest", key=f"ra_frf{rid}"):
            st.session_state[ADMIN_FLASH] = (
                f"Request #{rid} is due a refund — send it, then mark it refunded under Payments."
                if refund_frozen(rid, ps) else "That booking has changed — refresh the page.")
            st.rerun()
    if ps == RELEASED:
        b[1].caption("Already released — it's the owner who is held back now.")
        return
    if r["rstatus"] not in (HOLD, REMOVED):
        return
    # Release lifts the listing's freeze only: offered when the same booking, released,
    # would be payable (_freeze_reason), so an owner on hold or unverified stays frozen.
    if _freeze_reason(dict(r, payment_status=RELEASED)) is not None:
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
