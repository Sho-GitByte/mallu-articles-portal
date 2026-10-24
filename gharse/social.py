# social.py — GharSe Social: house parties, friends meets, plan & play, hangouts, trips.
#
# Anyone verified can host. A house party is ticketed — say ₹399 a seat, up to 50 guests,
# for around four hours, with the host's own theme, rules and games. Guests who like it
# book and pay; GharSe adds 8% platform + 2% processing ON TOP (ui.split_on_top), so the
# host keeps every rupee of her price: 50 × ₹399 is ₹19,950 to her and ₹1,995 to GharSe.
#
# Money follows the marketplace's UPI pay-then-claim flow (app.py customer_orders /
# admin_orders): the guest pays the platform UPI ID, submits the UTR ("Claimed"), the team
# confirms ("Paid"). Only then does the guest see the exact address and the host's phone,
# and the host see the guest's first name and phone. Hosts are paid in escrow: only for
# Paid bookings of events that have already ENDED, recorded in social_payouts.
#
# Seats are a shared resource two people can race for, so every step that guards seats or
# money is one conditional statement (… WHERE … RETURNING id):
#   * reserve  — INSERT … SELECT … WHERE capacity >= seats already held + qty, then a
#                post-commit re-check that voids this booking if the event is now over
#                capacity (that second check is what makes it safe on Postgres, where two
#                concurrent INSERT … SELECTs can read the same count). The last booking to
#                commit always sees every other one, so capacity can never be exceeded.
#   * claim / confirm / reject / re-open / refund / cancel — UPDATE … WHERE <expected state>.
#                Every move to "Claimed" (a claim, a lapsed hold taken back, a re-open)
#                stamps claimed_at in that same UPDATE, and the team's "Waiting …" flag
#                counts from it; rows from before the column count from created_at.
#   * payout   — INSERT … SELECT … WHERE amount <= (earned − already paid), then the same
#                post-commit re-check, so a host can never be paid twice.
#
# One account can hold only so many PAID seats on an event while its payment is being
# checked: the admin's "social_seat_cap" setting (Fees & Settings), read through
# paid_seat_cap() by the INSERT, the lapsed-hold re-take and the seat picker alike.
#
# How a cancelled booking records its refund (the schema has no refund column yet):
# customer_total stays what the guest paid; ticket_total / platform_fee / ops_fee /
# host_payout are rewritten to the part that is KEPT under the event's refund rule.
# refund_of(b) = customer_total − kept. A full refund keeps nothing; "No refunds" keeps it
# all and the host is still paid for that seat once the event has ended. If the host then
# calls the event off after such a guest was partly refunded, the rest goes back on its own
# row (qty 0, status "Event cancelled", payment_status "Refund due", customer_total = the
# remainder), so the original row still reads exactly what was refunded the first time.
# The same happens when the team had already SENT the part refund but not yet marked it
# when the event was called off (which turns that row into a full refund): "I already sent
# ₹X" records X as refunded on the original row and queues the rest on its own row
# (record_part_refund). Either way, once its rest has its own row the original's kept part
# sits in ticket_total alone — host_payout and both fees 0, since nobody keeps anything of
# a cancelled event — and the rest is queued only by the one conditional UPDATE that moves
# the original into that shape, so it can never be queued twice.
#
# Payment references are 12-digit UPI UTRs, and a reference can be on one booking only. A
# claim the team rejects is final for that booking: the seats are released and the
# reference stays recorded (payment_status "Rejected"); if the money turns up after all,
# the team re-opens it from Events Admin and it is refunded.
#
# Tables: events, event_bookings, social_payouts (read/write); users (read, and write of
# users.interests); notifications (via ui.notify).

import re
from datetime import datetime, time, timedelta

import streamlit as st

import kyc
import safety
import store
from catalog import AREAS, EVENT_HINTS, EVENT_KINDS, INTERESTS, REFUND_RULES
from store import execute, now_ist, q, setting
from ui import (
    MAX_IMG_MB,
    csv_join,
    csv_split,
    h,
    inr,
    kpi,
    notify,
    process_image,
    safe_image,
    split_on_top,
    table,
    wa_link,
)

SOCIAL_PAGE_TITLE = "Events & Meetups"
ADMIN_PAGE_TITLE = "Events Admin"

KIND_CAPS = {"House party": 50, "Trip": 40, "Hangout": 30, "Friends meet": 100, "Plan & play": 100}
ADULT_KINDS = {"House party", "Trip"}          # identity-verified guests only, 18+
ADULT_AGE = 18
MAX_SEATS_PER_BOOKING = 10
CLAIM_SLA_HOURS = 2                            # admin sees a claim older than this flagged
HOLD_MINUTES = 60                              # unpaid seats are held this long, then released
MAX_EVENT_DAYS = 14
HOST_ACK = ("No alcohol will be sold through GharSe, and I'm responsible for any permissions my "
            "society, the police or the excise department require.")

ACTIVE = "Booked"                              # event_bookings.status that holds seats
CLOSED = ("Expired", "Void", "Event cancelled")  # statuses where nothing is kept: all comes back
UTR_RE = re.compile(r"^\d{12}$")              # a UPI UTR / RRN is 12 digits
MONEY_IN = ("Paid", "Refund due", "Refunded")  # payment received by GharSe
_BOOK_PURPOSE = {"House party": "book a house party", "Trip": "join a trip"}


# ----------------------------------------------------------------------------- time
def _parse(ts):
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=store.IST)
    return t.astimezone(store.IST)


def _iso(dt):
    """Every event time is stored as IST ISO-8601 with its offset — the same shape as
    store.now_iso() — so plain string comparison in SQL orders them correctly."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=store.IST)
    return dt.astimezone(store.IST).isoformat(timespec="seconds")


def _now_iso():
    return _iso(now_ist())


def _clock(t):
    return t.strftime("%I:%M %p").lstrip("0")


def when(starts_at, ends_at=None):
    s, e = _parse(starts_at), _parse(ends_at) if ends_at else None
    if not s:
        return str(starts_at or "")
    out = f"{s.strftime('%a %d %b')} · {_clock(s)}"
    if e:
        out += f" – {_clock(e)}" if e.date() == s.date() else f" – {e.strftime('%a %d %b')}, {_clock(e)}"
    return out


# ----------------------------------------------------------------------------- money
def rs(x):
    """₹438.90 — paise shown, because the itemised fees are not whole rupees."""
    paise = round(float(x or 0) * 100)
    rupees, rest = divmod(abs(paise), 100)
    out = inr(rupees) + (f".{rest:02d}" if rest else "")
    return ("-" + out) if paise < 0 else out


def price_line(price):
    """'₹399 + ₹39.90 fees = ₹438.90' — or 'Free'."""
    if not price:
        return "Free"
    s = split_on_top(price)
    return f"{rs(s['base'])} + {rs(s['platform_fee'] + s['ops_fee'])} fees = {rs(s['customer_total'])}"


def breakdown(split, qty=1, label="Tickets"):
    """All four figures, itemised: what the host asks, both fees, what the guest pays."""
    plat, ops = setting("platform_pct", float), setting("ops_pct", float)
    unit = rs(split["base"] / qty) if qty else rs(split["base"])
    return (f"<span class='muted'>{h(label)} {unit} × {qty}</span> {rs(split['base'])}<br>"
            f"<span class='muted'>GharSe platform fee ({plat:g}%)</span> {rs(split['platform_fee'])}<br>"
            f"<span class='muted'>Payment processing ({ops:g}%)</span> {rs(split['ops_fee'])}<br>"
            f"<b>You pay {rs(split['customer_total'])}</b> "
            f"<span class='muted'>· the host keeps {rs(split['host_payout'])}</span>")


def refund_fraction(rule, starts_at, at=None):
    """Share of what was paid that comes back if cancelled at `at`, under a REFUND_RULES
    entry. Parsed from the wording so a new rule in the catalog still works."""
    rule = rule or ""
    if not rule or rule.lower().startswith("no refund"):
        return 0.0
    start, at = _parse(starts_at), at or now_ist()
    if not start:
        return 0.0
    m_pct = re.search(r"(\d+)\s*%", rule)
    pct = 100 if rule.lower().startswith("full") else (int(m_pct.group(1)) if m_pct else 0)
    m_hrs = re.search(r"(\d+)\s*hours?", rule)
    hours = int(m_hrs.group(1)) if m_hrs else 0
    left = (start - at).total_seconds() / 3600.0
    return pct / 100.0 if left >= hours else 0.0


def kept_total(b):
    return round((b["ticket_total"] or 0) + (b["platform_fee"] or 0) + (b["ops_fee"] or 0), 2)


def refund_of(b):
    """What GharSe owes back on this booking. Seats that were never ours to keep —
    expired, voided, or an event the host called off — come back in full."""
    paid = float(b["customer_total"] or 0)
    if b["status"] in CLOSED:
        return round(paid, 2)
    if b["status"] == ACTIVE:
        return 0.0
    return round(max(0.0, paid - kept_total(b)), 2)


# ----------------------------------------------------------------------------- people
def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


# The only names a stranger ever sees. users.kyc_name (the name as on the ID) is never
# one of them and is never selected for a stranger-facing view: the display name comes
# first — users.full_name, or the name sign-up wrote to the customer / helper profile —
# then the login handle, then the caller's fallback (e.g. "A GharSe host").
# Rows that join an event or booking with its host carry the handle as `handle`, not
# `username`: safety.is_suspended treats any row with a `username` as a users row and
# would re-read it by the EVENT's id.
_NAME_KEYS = ("full_name", "cname", "pname", "username", "handle")
# Profile names for a users row aliased `u` (sign-up writes the name there, not to users).
_PROFILE_NAMES = ("(SELECT c.full_name FROM customers c WHERE c.id = u.customer_id) cname, "
                  "(SELECT p.owner_name FROM providers p WHERE p.id = u.provider_id) pname")


def _looks_like_contact(text):
    """A phone number, an email or a UPI ID — never shown as a name."""
    t = str(text or "")
    return "@" in t or sum(ch.isdigit() for ch in t) >= 6


def first_name(row, fallback="Guest"):
    for key in _NAME_KEYS:
        v = str(_get(row, key) or "").strip()
        if not v:
            continue
        first = v.split()[0][:30]
        if _looks_like_contact(v) or _looks_like_contact(first):
            continue
        return first
    return fallback


def _me(user):
    rows = q("SELECT * FROM users WHERE id=?", (user["id"],))
    return dict(rows[0]) if rows else dict(user)


def phone_ok(user_id):
    """The phone number on this account has been proved by OTP. Needed for every booking:
    a paid or free seat unlocks a host's home address, so it is never open to an account
    that is nothing more than a username."""
    rows = q("SELECT phone_verified FROM users WHERE id=?", (user_id,))
    return bool(rows and rows[0]["phone_verified"])


def _person(user_id, fallback="Guest"):
    """Name and phone for a message. Older accounts keep the phone on their helper or
    customer profile. The name is first_name()'s — never the name on the ID."""
    rows = q("SELECT u.full_name, u.username, u.phone, p.phone pphone, c.phone cphone, "
             "c.full_name cname, p.owner_name pname "
             "FROM users u LEFT JOIN providers p ON p.id = u.provider_id "
             "LEFT JOIN customers c ON c.id = u.customer_id WHERE u.id=?", (user_id,))
    if not rows:
        return {"first": fallback, "phone": ""}
    r = dict(rows[0])
    return {"first": first_name(r, fallback),
            "phone": r["phone"] or r["pphone"] or r["cphone"] or ""}


def _tell(user_id, role, kind, body, ref=None):
    p = _person(user_id)
    notify(p["phone"], p["first"], role, kind, body, ref)


def _para(text):
    return h(text).replace("\n", "<br>")


def _flash(key):
    """Flash messages often carry a name or title someone typed, so the whole text goes
    through h() — st.success / st.warning render Markdown, and a title like
    '[Re-verify UPI](https://…)' must never become a link."""
    msg = st.session_state.pop(key, None)
    if msg:
        (st.success if msg[0] == "ok" else st.warning)(h(msg[1]))


def _note(text):
    """A muted line of escaped text — for anything a person typed (a cancellation reason,
    a payment reference) that would otherwise go through st.caption's Markdown."""
    st.markdown(f"<span class='muted'>{_para(text)}</span>", unsafe_allow_html=True)


def _set_flash(key, ok, text):
    st.session_state[key] = ("ok" if ok else "warn", text)


# ----------------------------------------------------------------------------- seats
def sweep_holds():
    """Release unpaid seats held past HOLD_MINUTES, and unpaid seats for events that have
    already started. Free bookings are Paid at once and never expire."""
    cutoff = _iso(now_ist() - timedelta(minutes=HOLD_MINUTES))
    execute("UPDATE event_bookings SET status='Expired' WHERE status=? AND payment_status='Unpaid' "
            "AND (created_at < ? OR event_id IN (SELECT id FROM events WHERE starts_at <= ?))",
            (ACTIVE, cutoff, _now_iso()))


def seats_taken(event_id):
    return int(q("SELECT COALESCE(SUM(qty),0) s FROM event_bookings WHERE event_id=? AND status=?",
                 (event_id, ACTIVE))[0]["s"] or 0)


def _seats_map():
    return {r["event_id"]: int(r["s"] or 0) for r in
            q("SELECT event_id, SUM(qty) s FROM event_bookings WHERE status=? GROUP BY event_id", (ACTIVE,))}


def _over_capacity_void(booking_id, event_id):
    """The post-commit re-check. Voids this booking if, now that it is committed, the
    event holds more seats than it has — or is no longer open. Returns True if voided."""
    return execute(
        "UPDATE event_bookings SET status='Void' WHERE id=? AND status=? AND ("
        "(SELECT COALESCE(SUM(b.qty),0) FROM event_bookings b WHERE b.event_id=? AND b.status=?) "
        "> (SELECT e.capacity FROM events e WHERE e.id=?) "
        "OR NOT EXISTS (SELECT 1 FROM events e2 WHERE e2.id=? AND e2.status='Published')) RETURNING id",
        (booking_id, ACTIVE, event_id, ACTIVE, event_id, event_id)) is not None


def paid_seat_cap():
    """Paid seats one account may hold on one event while its payment is unconfirmed (Unpaid
    or Claimed). Any 12-digit number passes as a UTR until the team checks it, so without a
    cap one phone-verified account could keep a small paid event "Sold out" with a made-up
    reference. The number is the admin's (Fees & Settings), kept within
    1..MAX_SEATS_PER_BOOKING, and every check that needs it — the INSERT, the lapsed-hold
    re-take, the seat picker — reads it here, so they can't disagree. Free seats are
    confirmed at once and only need MAX_SEATS_PER_BOOKING."""
    return max(1, min(MAX_SEATS_PER_BOOKING, setting("social_seat_cap", int)))


def _cap_msg(cap):
    return (f"One account can hold up to {cap} paid seat{'s' if cap != 1 else ''} for an event while the "
            "payment is being checked. Choose fewer.")


def seat_limit(event):
    """Most seats one account can take on this event in one booking."""
    return MAX_SEATS_PER_BOOKING if not (event["ticket_price"] or 0) > 0 else paid_seat_cap()


def had_rejected(event_id, user_id):
    """This account already had a payment claim on this event that the team couldn't find."""
    return bool(q("SELECT 1 FROM event_bookings WHERE event_id=? AND user_id=? AND payment_status='Rejected'",
                  (event_id, user_id)))


REJECTED_MSG = ("Your earlier payment reference for this event couldn't be matched, so this account can't book "
                "it again. If you did pay, send the GharSe team a screenshot of the payment — we'll match it and "
                "refund you in full.")

# The account rules re-checked inside the statements: no second live booking on the same
# event, and no new seats on an event where a claim of theirs was rejected.
_ACCOUNT_TAKEN = ("EXISTS (SELECT 1 FROM event_bookings b3 WHERE b3.event_id=? AND b3.user_id=? "
                  "AND b3.id != ? AND (b3.status=? OR b3.payment_status='Rejected'))")
_ACCOUNT_FREE = "NOT " + _ACCOUNT_TAKEN


def _account_conflict_void(booking_id, event_id, user_id):
    """Post-commit re-check of the account rules, for two requests racing on Postgres:
    voids this booking if the same account now has another live booking (or a rejected
    claim) on the event. If both racers see each other both are voided — nothing has been
    paid on a fresh hold, and it can never leave one account over the cap."""
    return execute("UPDATE event_bookings SET status='Void' WHERE id=? AND status=? AND " + _ACCOUNT_TAKEN
                   + " RETURNING id", (booking_id, ACTIVE, event_id, user_id, booking_id, ACTIVE)) is not None


def reserve(event, user_id, qty):
    """Hold `qty` seats for this guest. Returns (booking_id, None) or (None, reason)."""
    qty = int(qty)
    if qty < 1 or qty > MAX_SEATS_PER_BOOKING:
        return None, f"Book between 1 and {MAX_SEATS_PER_BOOKING} seats."
    if not phone_ok(user_id):
        return None, f"Verify your phone number under {kyc.KYC_PAGE_TITLE} before booking."
    s = split_on_top((event["ticket_price"] or 0) * qty)
    free = s["customer_total"] <= 0
    cap = paid_seat_cap()                      # read once: the check, the INSERT and the message agree
    if not free and qty > cap:
        return None, _cap_msg(cap)
    if had_rejected(event["id"], user_id):
        return None, REJECTED_MSG
    now = _now_iso()
    bid = execute(
        "INSERT INTO event_bookings (event_id, user_id, qty, ticket_total, platform_fee, ops_fee, "
        "customer_total, host_payout, payment_status, payment_ref, status, created_at) "
        "SELECT ?,?,?,?,?,?,?,?,?,NULL,?,? FROM events e "
        "WHERE e.id=? AND e.status='Published' AND e.starts_at > ? AND e.host_user_id != ? "
        "AND EXISTS (SELECT 1 FROM users u WHERE u.id=? AND COALESCE(u.phone_verified,0)=1 "
        "AND COALESCE(u.suspended,0)=0) "
        "AND e.capacity >= ? + (SELECT COALESCE(SUM(b.qty),0) FROM event_bookings b "
        "WHERE b.event_id=e.id AND b.status=?) "
        "AND NOT EXISTS (SELECT 1 FROM event_bookings b2 WHERE b2.event_id=e.id AND b2.user_id=? "
        "AND (b2.status=? OR b2.payment_status='Rejected')) "
        # A paid hold is unconfirmed until the team checks the UTR: at most paid_seat_cap()
        # seats for one account (the price is the event's own, read here).
        "AND (COALESCE(e.ticket_price,0) <= 0 OR CAST(? AS INTEGER) <= CAST(? AS INTEGER)) RETURNING id",
        (event["id"], user_id, qty, s["base"], s["platform_fee"], s["ops_fee"], s["customer_total"],
         s["host_payout"], "Paid" if free else "Unpaid", ACTIVE, now,
         event["id"], now, user_id, user_id, qty, ACTIVE, user_id, ACTIVE, qty, cap))
    if bid is None:
        if had_rejected(event["id"], user_id):
            return None, REJECTED_MSG
        if q("SELECT 1 FROM event_bookings WHERE event_id=? AND user_id=? AND status=?",
             (event["id"], user_id, ACTIVE)):
            return None, "You already have seats for this event — see My bookings."
        if qty > cap and float((q("SELECT ticket_price FROM events WHERE id=?", (event["id"],))
                                or [{"ticket_price": 0}])[0]["ticket_price"] or 0) > 0:
            return None, _cap_msg(cap)
        left = max(0, int(event["capacity"] or 0) - seats_taken(event["id"]))
        if left <= 0:
            return None, "Sorry — it just sold out."
        return None, f"Only {left} seat(s) left now. Choose fewer and try again."
    if _account_conflict_void(bid, event["id"], user_id):
        return None, "You already have seats for this event — see My bookings."
    if _over_capacity_void(bid, event["id"]):
        return None, "Someone booked the last seats a moment before you. Nothing was charged."
    return bid, None


def clean_utr(ref):
    return re.sub(r"[\s-]", "", str(ref or ""))


def utr_problem(ref, booking_id):
    """Why this reference can't be accepted, or None. A UPI UTR is 12 digits, and one
    payment can only ever pay for one booking — including a booking whose claim was
    rejected, so a made-up number can't be used twice."""
    ref = clean_utr(ref)
    if not UTR_RE.match(ref):
        return ("Enter the 12-digit UPI reference (UTR) from your UPI app's payment details — "
                "digits only.")
    if q("SELECT 1 FROM event_bookings WHERE payment_ref=? AND id != ?", (ref, booking_id)):
        return "That reference is already on another booking. Check your UPI app for this payment's own reference."
    return None


_REF_FREE = "AND NOT EXISTS (SELECT 1 FROM event_bookings x WHERE x.payment_ref=? AND x.id != ?) "


def claim(booking, user_id, ref):
    """Guest says they've paid. Unpaid -> Claimed. A hold that lapsed is re-taken if the
    seats are still free; if not, the reference is still recorded so the team can refund
    money that was already sent. Only an Unpaid booking can be claimed, so once the team
    rejects a claim ("Rejected") the guest can't simply type another number into it.
    Every UPDATE here stamps claimed_at, so the team's wait counts from the claim itself.
    Returns (ok, message)."""
    bad = utr_problem(ref, booking["id"])
    if bad:
        return False, bad
    ref = clean_utr(ref)
    now = _now_iso()
    if booking["status"] == ACTIVE:
        ok = execute("UPDATE event_bookings SET payment_status='Claimed', payment_ref=?, claimed_at=? "
                     "WHERE id=? AND user_id=? AND status=? AND payment_status='Unpaid' " + _REF_FREE
                     + "RETURNING id", (ref, now, booking["id"], user_id, ACTIVE, ref, booking["id"]))
        if ok is None:
            return False, "This booking has changed — refresh and check its status."
        return True, "Thanks — the GharSe team will confirm your payment shortly."
    if booking["status"] == "Expired":
        # The seats come back only within the same account rules as a new booking: no other
        # live booking or rejected claim on this event, and no more than the paid-seat cap.
        back = execute(
            "UPDATE event_bookings SET status=?, payment_status='Claimed', payment_ref=?, claimed_at=? "
            "WHERE id=? AND user_id=? AND status='Expired' AND payment_status='Unpaid' " + _REF_FREE
            + "AND qty <= ? AND " + _ACCOUNT_FREE + " "
            "AND (SELECT e.capacity FROM events e WHERE e.id=? AND e.status='Published' AND e.starts_at > ?) "
            ">= qty + (SELECT COALESCE(SUM(b2.qty),0) FROM event_bookings b2 WHERE b2.event_id=? "
            "AND b2.status=?) RETURNING id",
            (ACTIVE, ref, now, booking["id"], user_id, ref, booking["id"], paid_seat_cap(),
             booking["event_id"], user_id, booking["id"], ACTIVE, booking["event_id"], now,
             booking["event_id"], ACTIVE))
        if (back is not None and not _account_conflict_void(back, booking["event_id"], user_id)
                and not _over_capacity_void(back, booking["event_id"])):
            return True, "Your seats were still free — they're yours again. The team will confirm your payment."
        kept = execute("UPDATE event_bookings SET payment_status='Claimed', payment_ref=?, claimed_at=? "
                       "WHERE id=? AND user_id=? AND payment_status='Unpaid' " + _REF_FREE + "RETURNING id",
                       (ref, now, booking["id"], user_id, ref, booking["id"]))
        if back is None and kept is None:
            return False, "This booking has changed — refresh and check its status."
        return True, ("Your hold had lapsed and the seats couldn't be given back, so this can't be a booking — "
                      "but your reference is recorded and the team will refund the full amount once they've "
                      "matched the payment.")
    return False, "This booking can't take a payment now."


def cancel_booking(booking, user_id, event):
    """Guest cancels before the event starts. Seats are released; what was paid is
    refunded per the event's refund rule. Returns (ok, refund, message)."""
    start = _parse(event["starts_at"])
    if start and start <= now_ist():
        return False, 0.0, "The event has started — it can't be cancelled now."
    pay = booking["payment_status"] or "Unpaid"
    frac = 1.0 if pay == "Unpaid" else refund_fraction(event["refund_rule"], event["starts_at"])
    keep = 0.0 if pay == "Unpaid" else 1.0 - frac
    kept = {k: round((booking[k] or 0) * keep, 2) for k in ("ticket_total", "platform_fee", "ops_fee", "host_payout")}
    paid = float(booking["customer_total"] or 0)
    refund = 0.0 if pay == "Unpaid" else round(max(0.0, paid - (kept["ticket_total"] + kept["platform_fee"]
                                                                  + kept["ops_fee"])), 2)
    new_pay = "Refund due" if (pay == "Paid" and refund > 0.005) else pay
    ok = execute(
        "UPDATE event_bookings SET status='Cancelled', ticket_total=?, platform_fee=?, ops_fee=?, "
        "host_payout=?, payment_status=? WHERE id=? AND user_id=? AND status=? AND payment_status=? "
        "RETURNING id",
        (kept["ticket_total"], kept["platform_fee"], kept["ops_fee"], kept["host_payout"], new_pay,
         booking["id"], user_id, ACTIVE, pay))
    if ok is None:
        return False, 0.0, "This booking changed while you were looking at it — refresh and try again."
    return True, refund, None


def cancel_event(event_id, reason, by):
    """Host or team calls an event off. Every rupee GharSe still holds for it goes back:
    live seats in full, and guests who had cancelled earlier get back whatever their refund
    rule kept — including a guest who was already partly refunded, whose remainder is queued
    on its own row. Returns True only for the call that actually cancelled it (so guests are
    told once, and the remainder rows are written once)."""
    reason = (reason or "").strip()
    flipped = execute("UPDATE events SET status='Cancelled', cancel_reason=? WHERE id=? AND status='Published' "
                      "RETURNING id", (f"{reason} ({by})", event_id))
    if flipped is None:
        return False
    ev = dict(q("SELECT * FROM events WHERE id=?", (event_id,))[0])
    # 1. Live seats: nothing is kept.
    execute("UPDATE event_bookings SET status='Event cancelled', ticket_total=0, platform_fee=0, ops_fee=0, "
            "host_payout=0, payment_status=CASE WHEN payment_status='Paid' AND customer_total > 0 "
            "THEN 'Refund due' ELSE payment_status END WHERE event_id=? AND status=?", (event_id, ACTIVE))
    # 2. Guests who cancelled earlier and whose money is still with GharSe: all of it comes back.
    execute("UPDATE event_bookings SET ticket_total=0, platform_fee=0, ops_fee=0, host_payout=0, "
            "payment_status=CASE WHEN payment_status='Paid' THEN 'Refund due' ELSE payment_status END "
            "WHERE event_id=? AND status='Cancelled' AND payment_status IN ('Paid','Refund due','Claimed','Rejected') "
            "AND customer_total > 0", (event_id,))
    # 3. Guests who cancelled earlier and were already refunded part of it: the part their
    #    rule kept now comes back too, on its own row. The original row still owes nothing
    #    more and still shows exactly what the first refund was; its kept part moves to
    #    ticket_total alone (nobody keeps anything of a cancelled event), and that move is
    #    the conditional UPDATE which lets this call queue the rest — a row already in that
    #    shape (say record_part_refund got there first) already has its rest queued.
    for b in [dict(r) for r in q("SELECT * FROM event_bookings WHERE event_id=? AND status='Cancelled' "
                                 "AND payment_status='Refunded' AND customer_total > 0", (event_id,))]:
        rest = kept_total(b)
        if rest > 0.005 and execute(
                "UPDATE event_bookings SET ticket_total=?, platform_fee=0, ops_fee=0, host_payout=0 "
                "WHERE id=? AND status='Cancelled' AND payment_status='Refunded' "
                "AND ABS(COALESCE(ticket_total,0) + COALESCE(platform_fee,0) + COALESCE(ops_fee,0) - ?) < 0.005 "
                "AND COALESCE(host_payout,0) + COALESCE(platform_fee,0) + COALESCE(ops_fee,0) > 0.005 "
                "RETURNING id", (rest, b["id"], rest)) is not None:
            execute("INSERT INTO event_bookings (event_id, user_id, qty, ticket_total, platform_fee, ops_fee, "
                    "customer_total, host_payout, payment_status, payment_ref, status, created_at) "
                    "VALUES (?,?,0,0,0,0,?,0,'Refund due',?,'Event cancelled',?)",
                    (event_id, b["user_id"], rest, b["payment_ref"], store.now_iso()))
    # Tell each guest once, with what is actually coming back to them.
    owed = {}
    for b in [dict(r) for r in q(
            "SELECT * FROM event_bookings WHERE event_id=? AND (status='Event cancelled' OR "
            "(status='Cancelled' AND payment_status IN ('Refund due','Claimed'))) ORDER BY id", (event_id,))]:
        o = owed.setdefault(b["user_id"], {"due": 0.0, "checking": 0.0, "ref": b["id"]})
        if b["payment_status"] == "Refund due":
            o["due"] += refund_of(b)
        elif b["payment_status"] == "Claimed":
            o["checking"] += float(b["customer_total"] or 0)
    for uid, o in owed.items():
        money = ""
        if o["due"] > 0.005:
            money += f" {rs(o['due'])} will be refunded to you."
        if o["checking"] > 0.005:
            money += (f" Once the team has matched your payment of {rs(o['checking'])}, all of it will be "
                      "refunded to you.")
        _tell(uid, "guest", "event cancelled",
              f"Sorry — \"{ev['title']}\" on {when(ev['starts_at'])} has been cancelled. "
              f"Reason: {reason}.{money}", o["ref"])
    if by != "host":
        _tell(ev["host_user_id"], "host", "event cancelled",
              f"The GharSe team has cancelled your event \"{ev['title']}\". Reason: {reason}. "
              f"All guests are being refunded.", event_id)
    return True


# ----------------------------------------------------------------------------- payments
# What GharSe owes back on a booking, worked out inside SQL so a payment decision is made
# on the row as it is at that instant — the same rule as refund_of().
_REFUND_SQL = ("(CASE WHEN status IN ('Expired','Void','Event cancelled') THEN COALESCE(customer_total,0) "
               "ELSE COALESCE(customer_total,0) - (COALESCE(ticket_total,0) + COALESCE(platform_fee,0) "
               "+ COALESCE(ops_fee,0)) END)")


def confirm_payment(booking_id):
    """Team confirms the money arrived. Claimed -> Paid for a live booking, or for a
    cancelled one where nothing is owed back; Claimed -> Refund due when the booking was
    cancelled, lapsed or called off and something is owed back.

    The choice is made by the UPDATE itself (each statement re-checks the booking's status
    and figures), so a cancellation landing at the same moment can never leave a
    cancelled booking marked Paid with its refund lost."""
    target = None
    for _ in range(3):
        if execute("UPDATE event_bookings SET payment_status='Paid' WHERE id=? AND payment_status='Claimed' "
                   f"AND (status=? OR (status != ? AND {_REFUND_SQL} <= 0.005)) RETURNING id",
                   (booking_id, ACTIVE, ACTIVE)) is not None:
            target = "Paid"
            break
        if execute("UPDATE event_bookings SET payment_status='Refund due' WHERE id=? AND payment_status='Claimed' "
                   f"AND status != ? AND {_REFUND_SQL} > 0.005 RETURNING id", (booking_id, ACTIVE)) is not None:
            target = "Refund due"
            break
        rows = q("SELECT payment_status FROM event_bookings WHERE id=?", (booking_id,))
        if not rows or rows[0]["payment_status"] != "Claimed":
            return None
    if target is None:
        return None
    b = dict(q("SELECT * FROM event_bookings WHERE id=?", (booking_id,))[0])
    ev = dict(q("SELECT * FROM events WHERE id=?", (b["event_id"],))[0])
    if target == "Paid" and b["status"] == ACTIVE:
        _tell(b["user_id"], "guest", "payment confirmed",
              f"Payment confirmed — you're going to \"{ev['title']}\" on {when(ev['starts_at'])}. "
              f"Address unlocked: open GharSe → {SOCIAL_PAGE_TITLE} → My bookings for the exact address "
              f"and the host's number.", booking_id)
        guest = _person(b["user_id"])
        _tell(ev["host_user_id"], "host", "guest paid",
              f"{guest['first']} is confirmed for \"{ev['title']}\" ({b['qty']} seat(s)). Their number is "
              f"in My events.", booking_id)
    elif target == "Refund due":
        _tell(b["user_id"], "guest", "refund due",
              f"We received your {rs(b['customer_total'])} for \"{ev['title']}\". The seats weren't held, "
              f"so {rs(refund_of(b))} is being refunded to you.", booking_id)
    return target


def reject_payment(booking_id):
    """Team can't find the money. The claim is closed for good on this booking: a live
    booking's seats are released (status Void), and the reference stays recorded so it
    can't be used again. If the guest did pay, the team re-opens it (reopen_payment) and
    it goes to refunds."""
    ok = execute("UPDATE event_bookings SET payment_status='Rejected', "
                 "status=CASE WHEN status=? THEN 'Void' ELSE status END "
                 "WHERE id=? AND payment_status='Claimed' RETURNING id", (ACTIVE, booking_id))
    if ok is None:
        return False
    b = dict(q("SELECT * FROM event_bookings WHERE id=?", (booking_id,))[0])
    ev = q("SELECT title FROM events WHERE id=?", (b["event_id"],))
    title = ev[0]["title"] if ev else "your GharSe event"
    _tell(b["user_id"], "guest", "payment not found",
          f"We couldn't find a UPI payment with reference {b['payment_ref'] or '—'} for \"{title}\", so the "
          "booking has been closed and any seats released. If you did pay, send the GharSe team a screenshot "
          "of the payment — we'll match it and refund you in full.", booking_id)
    return True


def reopen_payment(booking_id):
    """The money turned up after a rejection: back to Claimed, so confirm_payment decides
    (a released booking goes straight to refunds). The wait starts again from now."""
    return execute("UPDATE event_bookings SET payment_status='Claimed', claimed_at=? WHERE id=? "
                   "AND payment_status='Rejected' RETURNING id", (_now_iso(), booking_id)) is not None


def mark_refunded(booking_id, amount):
    """Record that `amount` — the refund the admin saw on screen and sent — has gone back.
    Refused (False) unless the booking is still 'Refund due' AND still owes exactly that
    amount, checked in the UPDATE itself: if the host called the event off in between (so
    a partial refund became a full one), the admin is asked to recheck instead of the
    bigger figure being recorded as sent."""
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return False
    ok = execute("UPDATE event_bookings SET payment_status='Refunded' WHERE id=? AND payment_status='Refund due' "
                 f"AND ABS({_REFUND_SQL} - ?) < 0.005 RETURNING id", (booking_id, amount))
    if ok is None:
        return False
    b = dict(q("SELECT * FROM event_bookings WHERE id=?", (booking_id,))[0])
    _tell(b["user_id"], "guest", "refund sent",
          f"Your refund of {rs(refund_of(b))} for your GharSe event booking has been sent.", booking_id)
    return True


def part_refund_applies(b, ev_status):
    """A guest's own cancellation, on an event since called off, now owing everything that
    was paid. A refund the team has already seen can only grow into this shape (cancel_event
    step 2 makes a part refund a full one), so it is where record_part_refund is offered."""
    owed = refund_of(b)
    return (b["status"] == "Cancelled" and b["payment_status"] == "Refund due" and ev_status == "Cancelled"
            and owed > 0.005 and abs(owed - float(b["customer_total"] or 0)) < 0.005)


def record_part_refund(booking_id, sent, owed):
    """The team sent `sent` — the part refund they saw — but before they marked it the host
    called the event off, so the booking now owes `owed`: all the guest paid. Records `sent`
    as refunded on this row and queues the rest on its own row, the same shape cancel_event
    step 3 leaves for a guest who was already partly refunded: the original's kept part
    becomes the rest, in ticket_total alone, so refund_of() on it reads exactly `sent` and
    nothing on it is the host's or GharSe's.

    The UPDATE re-checks it all — still 'Refund due', a guest cancellation on an event that
    is now cancelled, owing exactly `owed`, which is everything paid — and only the call
    that moves the row queues the rest, so a double click or a second admin can't queue it
    twice. Returns the new row's id, or None if nothing was recorded."""
    try:
        sent, owed = round(float(sent), 2), round(float(owed), 2)
    except (TypeError, ValueError):
        return None
    if sent <= 0.005 or sent >= owed - 0.005:
        return None
    rest = round(owed - sent, 2)
    moved = execute(
        "UPDATE event_bookings SET payment_status='Refunded', ticket_total=?, platform_fee=0, ops_fee=0, "
        "host_payout=0 WHERE id=? AND status='Cancelled' AND payment_status='Refund due' "
        f"AND ABS({_REFUND_SQL} - ?) < 0.005 AND ABS(COALESCE(customer_total,0) - ?) < 0.005 "
        "AND EXISTS (SELECT 1 FROM events e WHERE e.id=event_bookings.event_id AND e.status='Cancelled') "
        "RETURNING id", (rest, booking_id, owed, owed))
    if moved is None:
        return None
    rest_id = execute(
        "INSERT INTO event_bookings (event_id, user_id, qty, ticket_total, platform_fee, ops_fee, "
        "customer_total, host_payout, payment_status, payment_ref, status, created_at) "
        "SELECT event_id, user_id, 0, 0, 0, 0, ?, 0, 'Refund due', payment_ref, 'Event cancelled', ? "
        "FROM event_bookings WHERE id=? RETURNING id", (rest, store.now_iso(), booking_id))
    b = dict(q("SELECT * FROM event_bookings WHERE id=?", (booking_id,))[0])
    ev = q("SELECT title FROM events WHERE id=?", (b["event_id"],))
    _tell(b["user_id"], "guest", "refund sent",
          f"{rs(refund_of(b))} of your refund for \"{ev[0]['title'] if ev else 'your GharSe event'}\" has been "
          f"sent. The remaining {rs(rest)} is on its way.", booking_id)
    return rest_id


# Escrow: Paid seats of events that have ENDED and were not called off. Cancelled-by-guest
# seats count for what was kept under the refund rule.
_EARNED_SQL = ("SELECT COALESCE(SUM(b.host_payout),0) FROM event_bookings b JOIN events e ON e.id=b.event_id "
               "WHERE e.host_user_id=? AND e.ends_at < ? AND e.status != 'Cancelled' "
               "AND b.status IN ('Booked','Cancelled') AND b.payment_status IN ('Paid','Refund due','Refunded')")
_PAID_SQL = "SELECT COALESCE(SUM(p.amount),0) FROM social_payouts p WHERE p.user_id=? AND p.source='event'"


def host_balance(host_id):
    now = _now_iso()
    earned = float(q(f"SELECT ({_EARNED_SQL}) s", (host_id, now))[0]["s"] or 0)
    count = int(q("SELECT COUNT(*) c FROM event_bookings b JOIN events e ON e.id=b.event_id "
                  "WHERE e.host_user_id=? AND e.ends_at < ? AND e.status != 'Cancelled' "
                  "AND b.status IN ('Booked','Cancelled') AND b.payment_status IN ('Paid','Refund due','Refunded') "
                  "AND b.host_payout > 0", (host_id, now))[0]["c"] or 0)
    paid = float(q(f"SELECT ({_PAID_SQL}) s", (host_id,))[0]["s"] or 0)
    return round(earned, 2), round(paid, 2), round(earned - paid, 2), count


def record_payout(host_id, amount, method, ref, items):
    """Record money sent to a host. Refused (None) if it is more than is owed right now or
    the reference was already used — so the same money can never be recorded twice."""
    amount, ref = round(float(amount), 2), (ref or "").strip()
    now = _now_iso()
    pid = execute(
        "INSERT INTO social_payouts (user_id, amount, items_count, source, method, ref, created_at) "
        f"SELECT ?,?,?,?,?,?,? WHERE ? > 0 AND ? <= ({_EARNED_SQL}) - ({_PAID_SQL}) + 0.005 "
        "AND NOT EXISTS (SELECT 1 FROM social_payouts x WHERE x.source='event' AND x.ref=?) RETURNING id",
        (host_id, amount, int(items), "event", method, ref, now, amount, amount, host_id, now, host_id, ref))
    if pid is None:
        return None
    # Post-commit re-check: if a second admin recorded the same money at the same moment,
    # the later row sees the balance negative and removes itself.
    over = q(f"SELECT ({_EARNED_SQL}) - ({_PAID_SQL}) s", (host_id, now, host_id))[0]["s"]
    if float(over or 0) < -0.005:
        execute("DELETE FROM social_payouts WHERE id=?", (pid,))
        return None
    return pid


# ----------------------------------------------------------------------------- shared UI
def _upi_link(amount, note):
    pa = (setting("platform_upi", str) or "").strip()
    if not pa:
        return None
    from urllib.parse import quote
    brand = setting("brand_name", str) or "GharSe"
    return (f"upi://pay?pa={quote(pa)}&pn={quote(brand)}&am={amount:.2f}"
            f"&cu=INR&tn={quote(note[:40])}")


def _status_pill(b):
    st_, pay = b["status"], b["payment_status"] or "Unpaid"
    if st_ == ACTIVE:
        if pay == "Paid":
            return "<span class='pill ok'>Confirmed</span>"
        if pay == "Claimed":
            return "<span class='pill'>Payment being checked</span>"
        return "<span class='pill warn'>Seats held — pay to confirm</span>"
    label = {"Cancelled": "Cancelled", "Event cancelled": "Event cancelled", "Expired": "Hold expired",
             "Void": "Not booked"}.get(st_, st_)
    cls = "pill warn" if st_ == "Event cancelled" else "pill flat"
    extra = ""
    if pay == "Rejected":
        extra = " <span class='pill warn'>Payment not found</span>"
    elif pay in ("Refund due", "Refunded", "Claimed") and (b["customer_total"] or 0) > 0:
        extra = f" <span class='pill {'ok' if pay == 'Refunded' else 'warn'}'>{h(pay)}</span>"
    return f"<span class='{cls}'>{h(label)}</span>{extra}"


def _hosted_by(host):
    name = first_name(host, "")
    return f"Hosted by {name}" if name else "A GharSe host"


def _event_head(ev, host):
    return (f"<span class='pill'>{h(ev['kind'])}</span> "
            f"<b>{h(ev['title'])}</b><br>"
            + (f"<span class='muted'>Theme: {h(ev['theme'])}</span><br>" if ev["theme"] else "")
            + f"<span class='muted'>{h(_hosted_by(host))}</span> {kyc.badge(host)}<br>"
            f"<span class='muted'>{h(ev['area'])}"
            + (f" · {h(ev['venue_hint'])}" if ev["venue_hint"] else "")
            + f" · {h(when(ev['starts_at'], ev['ends_at']))}</span>")


def _private_address(booking_id, user_id):
    """The exact address, read only for this guest's own booking, only once it is Paid and
    only for a phone-verified account. None otherwise. Nothing else in this module selects
    events.address for a guest."""
    rows = q("SELECT e.address FROM event_bookings b JOIN events e ON e.id=b.event_id "
             "JOIN users u ON u.id=b.user_id WHERE b.id=? AND b.user_id=? AND b.status=? "
             "AND b.payment_status='Paid' AND e.status='Published' AND COALESCE(u.phone_verified,0)=1",
             (booking_id, user_id, ACTIVE))
    return rows[0]["address"] if rows else None


def _pay_box(b, ev, viewer_id, prefix):
    """Pay, claim, or — once Paid — the exact address and the host's phone. `ev` never
    needs to carry the address: it is fetched here for the booking's own guest."""
    pay = b["payment_status"] or "Unpaid"
    if b["user_id"] != viewer_id:
        return
    if b["status"] == ACTIVE and pay == "Paid":
        address = _private_address(b["id"], viewer_id)
        if address is None:
            st.info(f"You're confirmed. Verify your phone number under {kyc.KYC_PAGE_TITLE} to see the exact "
                    "address and the host's number.")
            return
        hp = _person(ev["host_user_id"], "")
        phone = hp["phone"]
        hi = f"Hi {hp['first']}," if hp["first"] else "Hi,"
        link = wa_link(phone, f"{hi} I'm coming to {ev['title']} (GharSe booking #{b['id']}).") \
            if phone else None
        st.markdown(
            "<span class='pill ok'>Address unlocked</span><br>"
            f"<b>{_para(address)}</b><br>"
            f"<span class='muted'>Host: {h(hp['first'] or 'your host')} · "
            + (h(phone) if phone else "no phone on file — use Report if you can't reach them")
            + "</span>", unsafe_allow_html=True)
        if link:
            st.link_button("Message the host on WhatsApp", link, key=f"{prefix}wa{b['id']}")
        return
    if b["status"] == ACTIVE and pay == "Claimed":
        _note(f"Payment reference {b['payment_ref'] or '—'} — the GharSe team is confirming it. "
              "The exact address unlocks as soon as they do.")
        return
    if b["status"] in (ACTIVE, "Expired") and pay == "Unpaid" and (b["customer_total"] or 0) > 0:
        if b["status"] == ACTIVE:
            created = _parse(b["created_at"])
            if created:
                until = created + timedelta(minutes=HOLD_MINUTES)
                st.caption(f"Your seats are held until {_clock(until)}. Pay and enter the reference "
                           "before then.")
        else:
            st.caption("Your hold lapsed before a payment reference came in. If you've already paid, "
                       "enter the reference — we'll give you the seats if they're still free, or refund you.")
        link = _upi_link(b["customer_total"], f"GharSe event booking {b['id']}")
        pa = (setting("platform_upi", str) or "").strip()
        if link and b["status"] == ACTIVE:
            st.link_button(f"Pay {rs(b['customer_total'])} by UPI", link, key=f"{prefix}upi{b['id']}")
            _note(f"Or send it to {pa} from any UPI app, then enter the 12-digit reference (UTR) below.")
        elif not link:
            st.caption("Payment isn't switched on yet — the GharSe team needs to add the platform UPI ID.")
        with st.form(f"{prefix}pay{b['id']}", border=False):
            pc = st.columns([3, 1])
            ref = pc[0].text_input("UPI reference / UTR number", key=f"{prefix}utr{b['id']}", max_chars=20,
                                   help="The 12-digit number in your UPI app's payment details.")
            sent = pc[1].form_submit_button("I've paid")
        if sent:
            ok, msg = claim(b, b["user_id"], ref)
            if ok:
                guest = _person(b["user_id"])
                _tell(ev["host_user_id"], "host", "booking claimed",
                      f"{guest['first']} booked {b['qty']} seat(s) for \"{ev['title']}\" and says they've paid "
                      f"{rs(b['customer_total'])}. GharSe is confirming the payment.", b["id"])
                _set_flash("sb_flash", True, msg)
                st.rerun()
            else:
                st.error(msg)


# ----------------------------------------------------------------------------- page
def social_home(user):
    st.markdown(f"<div class='page-title'>{h(SOCIAL_PAGE_TITLE)}</div>"
                "<div class='page-sub'>House parties, friends meets, games, hangouts and trips — hosted by "
                "verified neighbours. The exact address is shared only once your seat is paid for.</div>",
                unsafe_allow_html=True)
    sweep_holds()
    me = _me(user)
    ctx = {"terms_shown": False}
    _flash("sb_flash")
    tabs = st.tabs(["Discover", "My bookings", "Host an event", "My events", "Interests"])
    with tabs[0]:
        _discover(user, me, ctx)
    with tabs[1]:
        _my_bookings(user, me)
    with tabs[2]:
        _host(user, me, ctx)
    with tabs[3]:
        _my_events(user, me)
    with tabs[4]:
        _interests(user, me)


def _terms_ok(user, me, ctx):
    """safety.accept_terms renders a form with a fixed key, so it may only be drawn once
    per run even though booking and hosting both need it."""
    if me.get("social_terms_at"):
        return True
    if ctx["terms_shown"]:
        st.info("Read and agree to the GharSe Social terms (shown on the Discover tab) first.")
        return False
    ctx["terms_shown"] = True
    return safety.accept_terms(user)


def _visible_events(me):
    rows = q("SELECT e.id, e.host_user_id, e.kind, e.title, e.description, e.theme, e.rules, e.activities, "
             "e.interests, e.area, e.venue_hint, e.starts_at, e.ends_at, e.ticket_price, e.capacity, e.min_age, "
             "e.refund_rule, e.status, e.photo, "
             "u.full_name, u.username handle, u.kyc_status, u.kyc_method, u.phone_verified, u.suspended, "
             + _PROFILE_NAMES + " FROM events e JOIN users u ON u.id = e.host_user_id "
             "WHERE e.status='Published' AND e.starts_at > ? ORDER BY e.starts_at", (_now_iso(),))
    out = []
    for r in rows:
        ev = dict(r)
        if safety.is_suspended(ev) or safety.is_blocked(me["id"], ev["host_user_id"]):
            continue
        out.append(ev)
    return out


def _discover(user, me, ctx):
    book_id = st.session_state.get("sb_book")
    if book_id:
        _booking_panel(user, me, book_id, ctx)
        st.divider()

    events = _visible_events(me)
    fc = st.columns(4)
    kind = fc[0].selectbox("Kind", ["All kinds"] + EVENT_KINDS, key="sd_kind")
    interest = fc[1].selectbox("Interest", ["Any interest"] + INTERESTS, key="sd_interest")
    area = fc[2].selectbox("Area", ["Anywhere"] + AREAS, key="sd_area")
    day = fc[3].date_input("Date", value=None, key="sd_date", min_value=store.today_ist())
    if kind != "All kinds":
        events = [e for e in events if e["kind"] == kind]
    if interest != "Any interest":
        events = [e for e in events if interest in csv_split(e["interests"])]
    if area != "Anywhere":
        events = [e for e in events if e["area"] == area]
    if day:
        events = [e for e in events if (_parse(e["starts_at"]) and _parse(e["starts_at"]).date() == day)]
    mine = set(csv_split(me.get("interests")))
    events.sort(key=lambda e: (0 if mine & set(csv_split(e["interests"])) else 1, e["starts_at"]))
    if not mine:
        st.caption("Pick your interests in the Interests tab and events you'll like come first.")

    if not events:
        st.info("No upcoming events match right now. Try fewer filters — or host one yourself under "
                "Host an event.")
        return
    taken = _seats_map()
    for ev in events:
        _event_card(user, me, ev, taken.get(ev["id"], 0), mine)
    safety.disclaimer("social")


def _event_card(user, me, ev, taken, mine):
    left = max(0, int(ev["capacity"] or 0) - taken)
    own = ev["host_user_id"] == me["id"]
    with st.container(border=True):
        cols = st.columns([1, 3]) if ev["photo"] else [None, st.container()]
        if ev["photo"]:
            with cols[0]:
                safe_image(ev["photo"], width="stretch")
        body = cols[1]
        tags = ""
        if mine & set(csv_split(ev["interests"])):
            tags += "<span class='pill ok'>For you</span> "
        if own:
            tags += "<span class='pill flat'>Your event</span> "
        body.markdown(tags + _event_head(ev, ev), unsafe_allow_html=True)
        age = f"{int(ev['min_age'])}+" if ev["min_age"] else "All ages"
        seats = (f"{left} of {int(ev['capacity'] or 0)} seats left" if left else "Sold out")
        body.markdown(
            f"<div class='price'>{h(price_line(ev['ticket_price']))}</div>"
            f"<span class='pill {'warn' if left <= 5 else ''}'>{h(seats)}</span> "
            f"<span class='pill'>{h(age)}</span> <span class='pill flat'>{h(ev['refund_rule'] or 'No refunds')}</span>",
            unsafe_allow_html=True)
        details = ""
        if ev["description"]:
            details += f"{_para(ev['description'])}<br>"
        if ev["activities"]:
            details += f"<b>Games &amp; activities</b><br><span class='muted'>{_para(ev['activities'])}</span><br>"
        if ev["rules"]:
            details += f"<b>House rules</b><br><span class='muted'>{_para(ev['rules'])}</span><br>"
        if ev["interests"]:
            details += f"<span class='muted'>Interests: {h(ev['interests'])}</span>"
        if details:
            body.markdown(details, unsafe_allow_html=True)
        a = st.columns([1, 1, 1, 2])
        if not own and left > 0 and a[0].button("Book seats", key=f"sdbook{ev['id']}", type="primary"):
            st.session_state["sb_book"] = ev["id"]
            st.rerun()
        if not own:
            with a[1]:
                safety.report_button(user, "event", ev["id"], ev["host_user_id"], key=f"sdrep{ev['id']}")
            with a[2]:
                safety.report_button(user, "user", ev["host_user_id"], ev["host_user_id"], key=f"sdreph{ev['id']}")
            with a[3]:
                safety.block_button(user, ev["host_user_id"], key=f"sdblk{ev['id']}")


def _booking_panel(user, me, event_id, ctx):
    rows = [e for e in _visible_events(me) if e["id"] == event_id]
    with st.container(border=True):
        top = st.columns([4, 1])
        if top[1].button("Close", key="sb_close"):
            st.session_state.pop("sb_book", None)
            st.rerun()
        if not rows:
            top[0].warning("This event isn't open for booking any more.")
            return
        ev = rows[0]
        top[0].markdown(_event_head(ev, ev), unsafe_allow_html=True)
        existing = q("SELECT * FROM event_bookings WHERE event_id=? AND user_id=? AND status=? ORDER BY id DESC",
                     (ev["id"], me["id"], ACTIVE))
        if existing:
            b = dict(existing[0])
            st.markdown(f"{_status_pill(b)} <span class='muted'>{h(b['qty'])} seat(s)</span>",
                        unsafe_allow_html=True)
            if (b["customer_total"] or 0) > 0:
                st.markdown(breakdown(b | {"base": b["ticket_total"]}, b["qty"]), unsafe_allow_html=True)
            # The public row has no address; _pay_box reads it itself, for this guest only.
            _pay_box(b, ev, me["id"], "sbp")
            return
        if ev["host_user_id"] == me["id"]:
            st.info("This is your own event.")
            return
        if safety.is_suspended(me["id"]):
            st.error("Your account is on hold while the GharSe team reviews some reports, so you can't book "
                     "right now. We'll be in touch within 24 hours.")
            return
        if not me.get("phone_verified"):
            st.info(f"To book seats you need to verify your phone number first — a one-time code by SMS. "
                    f"Open {kyc.KYC_PAGE_TITLE} from the menu; it takes a minute. The host's address is only "
                    "ever shared with guests whose number is verified.")
            return
        min_age = int(ev["min_age"] or 0)
        if ev["kind"] in ADULT_KINDS and not kyc.require(me, _BOOK_PURPOSE[ev["kind"]]):
            return
        # A date of birth only counts once the team has checked it (kyc.age_years returns
        # None before that), so an age limit means a verified identity.
        if min_age and not kyc.require(me, f"join an event for ages {min_age}+"):
            return
        if had_rejected(ev["id"], me["id"]):
            st.markdown(f"<span class='pill warn'>Payment not found</span> {h(REJECTED_MSG)}<br>"
                        f"{safety.grievance_line()}", unsafe_allow_html=True)
            return
        if not _terms_ok(user, me, ctx):
            return
        if min_age:
            age = kyc.age_years(me)
            if age is None:
                st.info(f"This event is {min_age}+ and we don't have a checked date of birth for you yet. "
                        f"Add it under {kyc.KYC_PAGE_TITLE}, then come back.")
                return
            if age < min_age:
                st.error(f"This event is for people aged {min_age} and over.")
                return
        left = max(0, int(ev["capacity"] or 0) - seats_taken(ev["id"]))
        if left <= 0:
            st.warning("Sold out — every seat is taken.")
            return
        limit = seat_limit(ev)
        top_qty = min(left, limit)
        qty = st.number_input("Seats", 1, top_qty, 1, step=1, key=f"sb_qty{ev['id']}",
                              help=f"Up to {top_qty}. Everyone you bring must meet the age limit."
                              + (f" One account can hold at most {limit} paid seat{'s' if limit != 1 else ''} "
                                 "while the payment is being checked." if (ev["ticket_price"] or 0) > 0 else ""))
        s = split_on_top((ev["ticket_price"] or 0) * int(qty))
        if s["customer_total"] > 0:
            st.markdown(breakdown(s, int(qty)), unsafe_allow_html=True)
            _note(f"Refunds: {ev['refund_rule'] or 'No refunds'}. Seats are held for {HOLD_MINUTES} minutes "
                  "while you pay.")
        else:
            st.caption("Free to join — your seat is confirmed straight away.")
        vouch = True
        if int(qty) > 1 and min_age:
            vouch = st.checkbox(f"Everyone I'm bringing is {min_age} or older, will carry ID, and I'm responsible "
                                "for them following the host's rules.", key=f"sb_vouch{ev['id']}")
        if st.button("Reserve seats" if s["customer_total"] > 0 else "Join for free",
                     key=f"sb_reserve{ev['id']}", type="primary"):
            if not vouch:
                st.error("Tick the box for the people you're bringing — or book one seat.")
                return
            bid, err = reserve(ev, me["id"], int(qty))
            if bid is None:
                st.error(err)
            else:
                if s["customer_total"] <= 0:
                    guest = _person(me["id"])
                    _tell(ev["host_user_id"], "host", "guest joined",
                          f"{guest['first']} is coming to \"{ev['title']}\" ({int(qty)} seat(s)).", bid)
                    _set_flash("sb_flash", True, "You're in! The exact address is under My bookings.")
                    st.session_state.pop("sb_book", None)
                else:
                    _set_flash("sb_flash", True, f"{int(qty)} seat(s) held for {HOLD_MINUTES} minutes — pay "
                                                 "below to confirm.")
                st.rerun()


# ----------------------------------------------------------------------------- my bookings
def _my_bookings(user, me):
    rows = q("SELECT b.*, e.title, e.kind, e.theme, e.area, e.venue_hint, e.starts_at, e.ends_at, "
             "e.refund_rule, e.host_user_id, e.status ev_status, e.cancel_reason, e.ticket_price, e.capacity, "
             "e.min_age, e.id ev_id, u.full_name, u.username handle, u.kyc_status, u.kyc_method, u.phone_verified, "
             + _PROFILE_NAMES + " "
             "FROM event_bookings b JOIN events e ON e.id=b.event_id JOIN users u ON u.id=e.host_user_id "
             # A voided free seat (customer_total 0) was never the guest's, and they were already
             # told so; a voided booking with money on it stays visible for its refund.
             "WHERE b.user_id=? AND (b.status != 'Void' OR (COALESCE(b.payment_status,'Unpaid') != 'Unpaid' "
             "AND COALESCE(b.customer_total,0) > 0)) "
             "ORDER BY e.starts_at DESC, b.id DESC", (me["id"],))
    if not rows:
        st.info("No bookings yet — find something on Discover.")
        return
    now = _now_iso()
    items = [dict(r) for r in rows]
    upcoming = [b for b in items if (b["ends_at"] or "") >= now]
    past = [b for b in items if (b["ends_at"] or "") < now]
    c = st.columns(3)
    kpi(c[0], "Upcoming", f"{sum(1 for b in upcoming if b['status'] == ACTIVE)}")
    kpi(c[1], "Attended", f"{sum(1 for b in past if b['status'] == ACTIVE and b['payment_status'] == 'Paid')}")
    kpi(c[2], "Refunds on the way", rs(sum(refund_of(b) for b in items if b["payment_status"] == "Refund due")))
    st.write("")
    for label, group in (("Upcoming", upcoming), ("Past", past)):
        if not group:
            continue
        st.markdown(f"#### {label}")
        for b in group:
            ev = {"id": b["ev_id"], "title": b["title"], "kind": b["kind"], "theme": b["theme"],
                  "area": b["area"], "venue_hint": b["venue_hint"],
                  "starts_at": b["starts_at"], "ends_at": b["ends_at"], "refund_rule": b["refund_rule"],
                  "host_user_id": b["host_user_id"], "ticket_price": b["ticket_price"]}
            with st.container(border=True):
                cc = st.columns([3, 1])
                cc[0].markdown(_event_head(ev, b), unsafe_allow_html=True)
                cc[1].markdown(f"<div class='price'>{rs(b['customer_total'])}</div>{_status_pill(b)}",
                               unsafe_allow_html=True)
                if b["status"] == ACTIVE and (b["customer_total"] or 0) > 0:
                    st.markdown(breakdown(b | {"base": b["ticket_total"]}, b["qty"]), unsafe_allow_html=True)
                if b["status"] == "Event cancelled":
                    _note(f"The event was cancelled. {b['cancel_reason'] or ''}")
                if b["payment_status"] == "Rejected":
                    st.markdown("<span class='muted'>We couldn't find a UPI payment with the reference you "
                                f"entered ({h(b['payment_ref'] or '—')}), so this booking was closed. If you did "
                                "pay, send the GharSe team a screenshot of the payment and we'll refund you in "
                                f"full.</span><br>{safety.grievance_line()}", unsafe_allow_html=True)
                if b["payment_status"] == "Refund due":
                    st.caption(f"{rs(refund_of(b))} is being refunded to you.")
                elif b["payment_status"] == "Refunded":
                    st.caption(f"{rs(refund_of(b))} was refunded.")
                if label == "Upcoming":
                    _pay_box(b, ev, me["id"], "mb")
                if label == "Upcoming" and b["status"] == ACTIVE and (b["starts_at"] or "") > now:
                    _cancel_control(b, ev)
                if label == "Upcoming":
                    safety.report_button(user, "event", ev["id"], ev["host_user_id"], key=f"mbrep{b['id']}")


def _cancel_control(b, ev):
    pay = b["payment_status"] or "Unpaid"
    if pay == "Unpaid":
        note = "Nothing has been paid, so nothing to refund."
    else:
        frac = refund_fraction(ev["refund_rule"], ev["starts_at"])
        back = round((b["customer_total"] or 0) * frac, 2)
        note = (f"Under this event's rule ({ev['refund_rule'] or 'No refunds'}) you'd get {rs(back)} back"
                + (" once the team has matched your payment." if pay == "Claimed" else "."))
    with st.expander("Cancel this booking"):
        _note(note)
        if st.button("Cancel my seats", key=f"mbcancel{b['id']}"):
            ok, refund, err = cancel_booking(b, b["user_id"], ev)
            if not ok:
                st.error(err)
                return
            guest = _person(b["user_id"])
            _tell(ev["host_user_id"], "host", "booking cancelled",
                  f"{guest['first']} cancelled {b['qty']} seat(s) for \"{ev['title']}\". The seats are open again.",
                  b["id"])
            msg = (f"Cancelled. {rs(refund)} will be refunded to you." if refund > 0.005
                   else "Cancelled. Your seats have been released.")
            if pay != "Unpaid":
                _tell(b["user_id"], "guest", "booking cancelled", f"Your booking for \"{ev['title']}\" is cancelled. "
                      + (f"{rs(refund)} will be refunded to you." if refund > 0.005
                         else "No refund applies under the event's refund rule."), b["id"])
            _set_flash("sb_flash", True, msg)
            st.rerun()


# ----------------------------------------------------------------------------- host
def _host(user, me, ctx):
    st.markdown("<span class='muted'>Host a house party, a meet-up, a game night, a hangout or a trip. "
                "You set the price — GharSe adds its 10% on top for the guest, so you keep every rupee "
                "of it.</span>", unsafe_allow_html=True)
    if safety.is_suspended(me["id"]):
        st.error("Your account is on hold while the GharSe team reviews some reports, so you can't host "
                 "right now. We'll be in touch within 24 hours.")
        return
    if not kyc.require(me, "host an event"):
        return
    if not _terms_ok(user, me, ctx):
        return
    _flash("sh_flash")
    kind = st.selectbox("What are you hosting?", EVENT_KINDS, key="sh_kind")
    cap_max = KIND_CAPS.get(kind, 30)
    age_floor = ADULT_AGE if kind in ADULT_KINDS else 0
    st.caption(f"{EVENT_HINTS.get(kind, '')} Up to {cap_max} guests"
               + (f", {ADULT_AGE}+ only." if age_floor else "."))
    pc = st.columns(2)
    price = pc[0].number_input("Ticket price per guest (₹) — 0 for a free meet-up", 0, 50000,
                               399 if kind == "House party" else 0, step=1, key=f"sh_price_{kind}")
    cap = pc[1].number_input("How many guests?", 1, cap_max, min(cap_max, 20), step=1, key=f"sh_cap_{kind}")
    if price:
        s = split_on_top(price)
        full = split_on_top(price * cap)
        st.markdown(
            f"<div class='price'>You'd receive {rs(full['host_payout'])}</div>"
            f"<span class='muted'>if all {int(cap)} seats sell at {rs(price)}. Each guest pays "
            f"{h(price_line(price))}; GharSe keeps {rs(full['platform_fee'] + full['ops_fee'])} "
            f"({rs(s['platform_fee'])} platform + {rs(s['ops_fee'])} processing a seat). Your money is "
            "sent after the event ends.</span>", unsafe_allow_html=True)
    else:
        st.caption("A free meet-up — no payments, no fees.")

    with st.form("sh_form"):
        title = st.text_input("Title", max_chars=80, key="sh_title",
                              placeholder="Retro Bollywood night on the terrace")
        theme = st.text_input("Theme (optional)", max_chars=80, key="sh_theme", placeholder="Dress code: 90s")
        desc = st.text_area("Describe it", max_chars=1500, key="sh_desc",
                            placeholder="What will happen, who it's for, what to bring.")
        acts = st.text_area("Games & activities", max_chars=800, key="sh_acts",
                            placeholder="Antakshari, dumb charades, a dance-off at 10")
        rules = st.text_area("Your house rules", max_chars=800, key="sh_rules",
                             placeholder="Shoes off inside. Music down by 11. No smoking indoors.")
        interests = st.multiselect("Interests it suits (up to 5)", INTERESTS, key="sh_interests",
                                   max_selections=5)
        ac = st.columns(2)
        area = ac[0].selectbox("Area", AREAS, key="sh_area")
        hint = ac[1].text_input("Venue hint — shown to everyone", max_chars=80, key="sh_hint",
                                placeholder="Near Agara lake, 2nd floor terrace")
        address = st.text_area("Exact address — shown only to guests who have paid", max_chars=300,
                               key="sh_address", placeholder="Flat, building, street, landmark, pincode")
        today = store.today_ist()
        tc = st.columns(4)
        d1 = tc[0].date_input("Starts on", today + timedelta(days=3), min_value=today, key="sh_d1")
        t1 = tc[1].time_input("at", time(19, 0), key="sh_t1")
        d2 = tc[2].date_input("Ends on", today + timedelta(days=3), min_value=today, key="sh_d2")
        t2 = tc[3].time_input("at", time(23, 0), key="sh_t2")
        mc = st.columns(2)
        min_age = mc[0].number_input("Minimum age" + (f" ({age_floor}+ for this kind)" if age_floor else
                                                      " (0 = all ages)"),
                                     age_floor, 99, ADULT_AGE, step=1, key=f"sh_age_{kind}")
        refund = mc[1].selectbox("Refund rule", REFUND_RULES, key="sh_refund")
        photo = st.file_uploader(f"Photo (optional, up to {MAX_IMG_MB} MB)", type=["jpg", "jpeg", "png", "webp"],
                                 key="sh_photo")
        ack = st.checkbox(HOST_ACK, key="sh_ack")
        submitted = st.form_submit_button("Publish event", type="primary")
    if not submitted:
        return
    starts = datetime.combine(d1, t1).replace(tzinfo=store.IST)
    ends = datetime.combine(d2, t2).replace(tzinfo=store.IST)
    errors = []
    if len((title or "").strip()) < 4:
        errors.append("Give it a title.")
    if not (desc or "").strip():
        errors.append("Describe the event so guests know what to expect.")
    if not (hint or "").strip():
        errors.append("Add a venue hint (shown publicly) — a landmark, not the address.")
    if len((address or "").strip()) < 10:
        errors.append("Add the exact address. Only paid guests will see it.")
    if starts <= now_ist() + timedelta(hours=1):
        errors.append("The start time must be at least an hour from now.")
    if ends <= starts:
        errors.append("The end time must be after the start time.")
    elif ends - starts > timedelta(days=MAX_EVENT_DAYS):
        errors.append(f"Events can run for at most {MAX_EVENT_DAYS} days.")
    if int(cap) > cap_max:
        errors.append(f"A {kind.lower()} can have at most {cap_max} guests.")
    if int(min_age) < age_floor:
        errors.append(f"A {kind.lower()} is {age_floor}+ only.")
    if not ack:
        errors.append("Tick the acknowledgement about alcohol and permissions.")
    img = None
    if photo is not None:
        if photo.size > MAX_IMG_MB * 1024 * 1024:
            errors.append(f"That photo is over {MAX_IMG_MB} MB — choose a smaller one.")
        else:
            img = process_image(photo)
            if img is None:
                errors.append("That file isn't a photo we can read. Upload a JPG or PNG.")
    if errors:
        for e in errors:
            st.error(e)
        return
    new_id = execute(
        "INSERT INTO events (host_user_id, kind, title, description, theme, rules, activities, interests, area, "
        "venue_hint, address, starts_at, ends_at, ticket_price, capacity, min_age, refund_rule, status, photo, "
        "host_ack, created_at) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS ("
        "SELECT 1 FROM events WHERE host_user_id=? AND title=? AND starts_at=? AND status='Published') RETURNING id",
        (me["id"], kind, title.strip(), desc.strip(), (theme or "").strip() or None, (rules or "").strip() or None,
         (acts or "").strip() or None, csv_join(interests) or None, area, hint.strip(), address.strip(),
         _iso(starts), _iso(ends), float(price), int(cap), int(min_age), refund, "Published", img, 1,
         store.now_iso(), me["id"], title.strip(), _iso(starts)))
    if new_id is None:
        st.warning("You've already published this event.")
        return
    _set_flash("sh_flash", True, f"\"{title.strip()}\" is live on Discover. You'll get a message for every booking.")
    st.rerun()


# ----------------------------------------------------------------------------- my events
def _my_events(user, me):
    evs = [dict(r) for r in q("SELECT * FROM events WHERE host_user_id=? ORDER BY starts_at DESC", (me["id"],))]
    if not evs:
        st.info("You haven't hosted anything yet. Start under Host an event.")
        return
    earned, paid, due, _ = host_balance(me["id"])
    now = _now_iso()
    c = st.columns(4)
    kpi(c[0], "Upcoming events",
        f"{sum(1 for e in evs if e['status'] == 'Published' and (e['ends_at'] or '') > now)}")
    kpi(c[1], "Earned (ended events)", rs(earned))
    kpi(c[2], "Paid out to you", rs(paid))
    kpi(c[3], "On its way", rs(due))
    st.caption("You're paid for confirmed seats once the event has ended. Payouts come from the GharSe team.")
    _flash("me_flash")
    for ev in evs:
        bookings = [dict(r) for r in q(
            "SELECT * FROM event_bookings WHERE event_id=? ORDER BY id", (ev["id"],))]
        active = [b for b in bookings if b["status"] == ACTIVE]
        paid_b = [b for b in active if b["payment_status"] == "Paid"]
        claimed = sum(b["qty"] for b in active if b["payment_status"] == "Claimed")
        held = sum(b["qty"] for b in active if b["payment_status"] == "Unpaid")
        sold = sum(b["qty"] for b in paid_b)
        money = 0.0 if ev["status"] == "Cancelled" else sum(
            (b["host_payout"] or 0) for b in bookings
            if b["status"] in (ACTIVE, "Cancelled") and b["payment_status"] in MONEY_IN)
        cap = int(ev["capacity"] or 0)
        ended = (ev["ends_at"] or "") < now
        if ev["status"] == "Cancelled":
            pill = "<span class='pill warn'>Cancelled</span>"
        elif ended:
            pill = "<span class='pill flat'>Ended</span>"
        else:
            pill = "<span class='pill ok'>Live</span>"
        with st.container(border=True):
            cc = st.columns([3, 1])
            cc[0].markdown(
                f"{pill} <span class='pill'>{h(ev['kind'])}</span> <b>{h(ev['title'])}</b><br>"
                f"<span class='muted'>{h(when(ev['starts_at'], ev['ends_at']))} · {h(ev['area'])}</span><br>"
                f"<span class='muted'>{sold} confirmed · {claimed} payment(s) being checked · {held} held unpaid · "
                f"{max(0, cap - sold - claimed - held)} of {cap} free</span>",
                unsafe_allow_html=True)
            cc[1].markdown(f"<div class='price'>{rs(money)}</div><span class='muted'>"
                           f"{'yours' if ended else 'yours after it ends'}</span>", unsafe_allow_html=True)
            if ev["status"] == "Cancelled":
                _note(f"Cancelled: {ev['cancel_reason'] or ''}")
            if paid_b:
                guests = []
                for b in paid_b:
                    p = _person(b["user_id"])
                    guests.append({"guest": p["first"], "phone": p["phone"] or "—", "seats": b["qty"]})
                st.markdown("<b>Confirmed guests</b>", unsafe_allow_html=True)
                table(guests, cols=["guest", "phone", "seats"])
            elif ev["status"] == "Published":
                st.caption("No confirmed guests yet.")
            if ev["status"] == "Published" and (ev["starts_at"] or "") > now:
                with st.expander("Cancel this event"):
                    st.caption("Every guest gets back all the money GharSe holds for them, and a message with "
                               "your reason.")
                    with st.form(f"mecancel{ev['id']}", border=False):
                        reason = st.text_input("Reason (guests will see it)", max_chars=200,
                                               key=f"mereason{ev['id']}")
                        go = st.form_submit_button("Cancel event and refund everyone")
                    if go:
                        if len((reason or "").strip()) < 4:
                            st.error("Tell your guests why — a short reason is enough.")
                        elif cancel_event(ev["id"], reason, "host"):
                            _set_flash("me_flash", True, f"\"{ev['title']}\" is cancelled and every guest has "
                                                         "been told. Refunds are on their way.")
                            st.rerun()
                        else:
                            st.warning("This event was already cancelled.")


# ----------------------------------------------------------------------------- interests
def _interests(user, me):
    st.markdown("<span class='muted'>What are you into? Events that match come first on Discover.</span>",
                unsafe_allow_html=True)
    have = [i for i in csv_split(me.get("interests")) if i in INTERESTS]
    with st.form("si_form", border=False):
        pick = st.multiselect("Your interests", INTERESTS, default=have, key="si_pick")
        save = st.form_submit_button("Save interests", type="primary")
    if save:
        val = csv_join(pick) or None
        execute("UPDATE users SET interests=? WHERE id=?", (val, me["id"]))
        try:
            user["interests"] = val
        except TypeError:
            pass
        _set_flash("sb_flash", True, "Saved — Discover now puts these first.")
        st.rerun()


# ----------------------------------------------------------------------------- admin
def social_admin(admin_user):
    st.markdown(f"<div class='page-title'>{h(ADMIN_PAGE_TITLE)}</div>"
                "<div class='page-sub'>Confirm ticket payments, send refunds, pay hosts after their events end, "
                "and moderate events.</div>", unsafe_allow_html=True)
    sweep_holds()
    _flash("sa_flash")
    claimed = [dict(r) for r in q(
        "SELECT b.*, e.title, e.starts_at, e.host_user_id, "
        "(SELECT COUNT(*) FROM event_bookings x WHERE x.user_id=b.user_id AND x.payment_status='Rejected') "
        "rejected_n FROM event_bookings b JOIN events e ON e.id=b.event_id "
        "WHERE b.payment_status='Claimed' ORDER BY COALESCE(b.claimed_at, b.created_at), b.id")]
    refunds = [dict(r) for r in q(
        "SELECT b.*, e.title, e.starts_at, e.status ev_status FROM event_bookings b "
        "JOIN events e ON e.id=b.event_id WHERE b.payment_status='Refund due' ORDER BY b.id")]
    fees = q("SELECT COALESCE(SUM(b.platform_fee + b.ops_fee),0) s FROM event_bookings b "
             "JOIN events e ON e.id=b.event_id WHERE e.status != 'Cancelled' "
             "AND b.payment_status IN ('Paid','Refund due','Refunded') AND b.status IN ('Booked','Cancelled')")[0]["s"]
    hosts = [r["host_user_id"] for r in q("SELECT DISTINCT host_user_id FROM events ORDER BY host_user_id")]
    owed = []
    for hid in hosts:
        earned, paid, due, cnt = host_balance(hid)
        if due > 0.005:
            owed.append((hid, earned, paid, due, cnt))
    c = st.columns(4)
    kpi(c[0], "Payments to check", f"{len(claimed)}")
    kpi(c[1], "Refunds due", rs(sum(refund_of(b) for b in refunds)))
    kpi(c[2], "Owed to hosts", rs(sum(o[3] for o in owed)))
    kpi(c[3], "GharSe fees earned", rs(fees))
    st.write("")
    tabs = st.tabs(["Payments", "Refunds", "Host payouts", "Events"])
    with tabs[0]:
        if not claimed:
            st.success("No payments waiting to be checked.")
        for b in claimed:
            g = _person(b["user_id"])
            with st.container(border=True):
                cc = st.columns([3, 1])
                note = "" if b["status"] == ACTIVE else (
                    f" · <span class='pill warn'>{h(b['status'])} — confirming sends it to refunds</span>")
                cc[0].markdown(
                    f"<b>Booking #{h(b['id'])} · {h(b['title'])} × {h(b['qty'])}</b>{note}<br>"
                    f"<span class='muted'>{h(g['first'])} (account #{h(b['user_id'])}) · {h(when(b['starts_at']))} · "
                    f"UTR {h(b['payment_ref'])}</span><br>{_claim_flags(b)}", unsafe_allow_html=True)
                cc[1].markdown(f"<div class='price'>{rs(b['customer_total'])}</div>", unsafe_allow_html=True)
                bc = st.columns([1, 1, 3])
                if bc[0].button("Confirm received", key=f"saconf{b['id']}", type="primary"):
                    res = confirm_payment(b["id"])
                    _set_flash("sa_flash", res is not None,
                               f"Booking #{b['id']} marked {res}." if res else "Someone else already handled it.")
                    st.rerun()
                if bc[1].button("Not received", key=f"sarej{b['id']}"):
                    ok = reject_payment(b["id"])
                    _set_flash("sa_flash", ok, f"Booking #{b['id']} closed as not paid; the guest was told and "
                               "any seats were released." if ok else "Someone else already handled it.")
                    st.rerun()
        rejected = [dict(r) for r in q(
            "SELECT b.*, e.title FROM event_bookings b JOIN events e ON e.id=b.event_id "
            "WHERE b.payment_status='Rejected' ORDER BY b.id DESC LIMIT 30")]
        if rejected:
            with st.expander(f"Payments marked not received ({len(rejected)})"):
                st.caption("If a guest sends proof and the money did arrive, re-open the booking: it comes back "
                           "to the list above, and confirming it sends the full amount to refunds.")
                for b in rejected:
                    g = _person(b["user_id"])
                    rc = st.columns([4, 1])
                    rc[0].markdown(
                        f"<span class='muted'>Booking #{h(b['id'])} · {h(b['title'])} · {h(g['first'])} · "
                        f"{rs(b['customer_total'])} · UTR {h(b['payment_ref'] or '—')}</span>",
                        unsafe_allow_html=True)
                    if rc[1].button("Re-open", key=f"sareo{b['id']}"):
                        ok = reopen_payment(b["id"])
                        _set_flash("sa_flash", ok, f"Booking #{b['id']} is back in the payments list."
                                   if ok else "Someone else already handled it.")
                        st.rerun()
    with tabs[1]:
        if not refunds:
            st.success("No refunds outstanding.")
        else:
            st.caption("Mark a refund only after sending the amount shown. If the amount changes before you "
                       "click (say the host cancels the event), nothing is recorded and you're asked to recheck.")
        # Part refunds the team said they had sent when "Mark refunded" was refused because the
        # booking had grown since (booking id -> amount seen). Kept for the session, so the
        # offer to record it outlives the one-time flash; dropped once the row is settled.
        sent_seen = st.session_state.setdefault(_SENT_KEY, {})
        for gone in set(sent_seen) - {b["id"] for b in refunds}:
            sent_seen.pop(gone, None)
        for b in refunds:
            g = _person(b["user_id"])
            back = refund_of(b)
            with st.container(border=True):
                cc = st.columns([3, 1])
                cc[0].markdown(
                    f"<b>Booking #{h(b['id'])} · {h(b['title'])}</b> <span class='pill flat'>{h(b['status'])}</span><br>"
                    f"<span class='muted'>{h(g['first'])} · {h(g['phone'] or 'no phone')} · paid "
                    f"{rs(b['customer_total'])} · UTR {h(b['payment_ref'] or '—')}</span>", unsafe_allow_html=True)
                cc[1].markdown(f"<div class='price'>{rs(back)}</div><span class='muted'>to refund</span>",
                               unsafe_allow_html=True)
                # The amount on screen travels with the click (callback args are bound when the
                # button is drawn), so a figure that changed since is refused, not recorded.
                st.button("Mark refunded", key=f"sarefd{b['id']}", on_click=_mark_refunded_click,
                          args=(b["id"], back))
                if part_refund_applies(b, b["ev_status"]):
                    _part_refund_controls(b, back, sent_seen.get(b["id"]))
    with tabs[2]:
        _admin_payouts(owed)
    with tabs[3]:
        _admin_events()


def _ago(ts):
    t = _parse(ts)
    if not t:
        return "unknown"
    mins = max(0, int((now_ist() - t).total_seconds() // 60))
    if mins < 60:
        return f"{mins} min"
    hrs, mins = divmod(mins, 60)
    if hrs < 48:
        return f"{hrs} h {mins} min"
    return f"{hrs // 24} days {hrs % 24} h"


def _claim_flags(b):
    """How long this claim has waited since the guest sent the reference (claimed_at), and
    how many of this account's claims the team has already found to be unpaid — so a
    squatter is easy to spot."""
    since = _get(b, "claimed_at") or b["created_at"]
    t = _parse(since)
    late = t is not None and now_ist() - t > timedelta(hours=CLAIM_SLA_HOURS)
    wait = (f"<span class='pill {'warn' if late else 'flat'}'>Waiting {h(_ago(since))}</span>")
    n = int(b.get("rejected_n") or 0)
    rej = (f"<span class='pill warn'>{n} earlier claim{'s' if n != 1 else ''} rejected on this account</span>"
           if n else "<span class='pill flat'>No rejected claims on this account</span>")
    # Claims made before claimed_at was recorded have only the booking time to go by.
    old = "" if _get(b, "claimed_at") else " <span class='muted'>· an older claim: counted from the booking</span>"
    return f"{wait} {rej}{old}"


_SENT_KEY = "sa_part_sent"


def _mark_refunded_click(booking_id, seen):
    sent_seen = st.session_state.setdefault(_SENT_KEY, {})
    if mark_refunded(booking_id, seen):
        sent_seen.pop(booking_id, None)
        _set_flash("sa_flash", True, f"Refund of {rs(seen)} for booking #{booking_id} recorded.")
        return
    rows = q("SELECT b.*, e.status ev_status FROM event_bookings b JOIN events e ON e.id=b.event_id "
             "WHERE b.id=?", (booking_id,))
    b = dict(rows[0]) if rows else None
    if b and b["payment_status"] == "Refund due":
        now_owed = refund_of(b)
        extra = ""
        if now_owed > seen + 0.005 and part_refund_applies(b, b["ev_status"]):
            # Remembered for the session: the booking below now offers to record what was sent.
            sent_seen[booking_id] = seen
            extra = (f" If you already sent {rs(seen)}, use \"I already sent {rs(seen)}\" on the booking below — "
                     f"it records that and queues the remaining {rs(now_owed - seen)} on its own. If you haven't, "
                     f"send {rs(now_owed)}, then mark it refunded.")
        _set_flash("sa_flash", False, f"Nothing recorded: booking #{booking_id} now owes {rs(now_owed)}, not the "
                                      f"{rs(seen)} you saw — it changed (for example the event was cancelled)."
                                      + extra)
    else:
        sent_seen.pop(booking_id, None)
        _set_flash("sa_flash", False, "Someone else already handled it.")


def _part_refund_controls(b, owed, seen):
    """For a guest who cancelled before the event was called off, whose part refund has
    become a full one: record a part the team had already sent, so the whole amount is
    never sent again. Right after "Mark refunded" was refused, the amount they saw is
    offered as it was; otherwise (say after a reload) they enter what they sent."""
    if seen is not None and owed > seen + 0.005:
        st.button(f"I already sent {rs(seen)} — record it and queue the remaining {rs(owed - seen)}",
                  key=f"sarefp{b['id']}", on_click=_part_refund_click, args=(b["id"], seen, owed))
        return
    with st.expander("Already sent part of this refund?"):
        _note("This guest cancelled before the event was called off, which turns a part refund into a full one. "
              "If you had already sent the smaller amount, don't send this whole amount again: record what you "
              "sent, and the rest is queued as a refund of its own.")
        with st.form(f"sarefpf{b['id']}", border=False):
            fc = st.columns([2, 2])
            fc[0].number_input("Already sent (₹)", 0.0, max(0.0, owed - 0.01), 0.0, step=1.0,
                               key=f"sarefpa{b['id']}")
            # As with "Mark refunded", the amount owed on screen is bound to the click.
            fc[1].form_submit_button("Record it and queue the rest", on_click=_part_refund_entered,
                                     args=(b["id"], owed))


def _part_refund_entered(booking_id, owed):
    sent = float(st.session_state.get(f"sarefpa{booking_id}") or 0)
    if sent <= 0.005 or sent >= owed - 0.005:
        _set_flash("sa_flash", False, f"Booking #{booking_id}: enter the part you already sent — more than ₹0 and "
                                      f"less than {rs(owed)}. For the whole amount, use Mark refunded.")
        return
    _part_refund_click(booking_id, sent, owed)


def _part_refund_click(booking_id, sent, owed):
    st.session_state.setdefault(_SENT_KEY, {}).pop(booking_id, None)
    rest_id = record_part_refund(booking_id, sent, owed)
    if rest_id is None:
        _set_flash("sa_flash", False, f"Nothing recorded: booking #{booking_id} has changed or someone else already "
                                      "handled it. Check the amount it shows now.")
        return
    _set_flash("sa_flash", True, f"Recorded {rs(sent)} refunded on booking #{booking_id}. The remaining "
                                 f"{rs(owed - sent)} is queued as booking #{rest_id} — send it, then mark it refunded.")


def _host_upi(host_id):
    rows = q("SELECT p.upi_id FROM users u JOIN providers p ON p.id=u.provider_id WHERE u.id=?", (host_id,))
    return (rows[0]["upi_id"] or "").strip() if rows else ""


def _admin_payouts(owed):
    st.caption("Escrow: a host is owed only for paid seats of events that have ended. Each payout is recorded "
               "against the money owed, so it can't be paid twice.")
    if not owed:
        st.success("Nothing owed — every ended event has been settled.")
    for hid, earned, paid, due, cnt in owed:
        p = _person(hid, "A GharSe host")
        upi = _host_upi(hid)
        with st.container(border=True):
            cc = st.columns([3, 1])
            cc[0].markdown(
                f"<b>{h(p['first'])}</b> <span class='muted'>(account #{h(hid)}) · {cnt} paid seat booking(s) · "
                f"earned {rs(earned)} · paid {rs(paid)}</span><br>"
                f"<span class='muted'>UPI: {h(upi) if upi else 'not on file — ask them'} · phone "
                f"{h(p['phone'] or '—')}</span>", unsafe_allow_html=True)
            cc[1].markdown(f"<div class='price'>{rs(due)}</div><span class='muted'>owed</span>",
                           unsafe_allow_html=True)
            with st.form(f"sapo{hid}", border=False):
                fc = st.columns([1, 1, 2])
                amount = fc[0].number_input("Amount (₹)", 0.0, float(due), float(due), step=1.0, key=f"saamt{hid}")
                method = fc[1].selectbox("Method", ["UPI", "Bank transfer", "Cash"], key=f"samethod{hid}")
                ref = fc[2].text_input("Transaction reference", key=f"saref{hid}")
                go = st.form_submit_button("Record payout")
            if go:
                if amount <= 0:
                    st.error("Enter an amount.")
                elif len((ref or "").strip()) < 4:
                    st.error("Enter the transaction reference — it's the audit trail.")
                else:
                    pid = record_payout(hid, amount, method, ref, cnt)
                    if pid is None:
                        st.error("Not recorded — that's more than is owed now, or this reference was already used.")
                    else:
                        _tell(hid, "host", "payout sent",
                              f"{rs(amount)} has been sent to you for your GharSe events. {method} reference "
                              f"{ref.strip()}.", pid)
                        _set_flash("sa_flash", True, f"Recorded {rs(amount)} to {p['first']}.")
                        st.rerun()
    st.markdown("#### Payout history")
    hist = [dict(r) for r in q("SELECT id, user_id, amount, items_count, method, ref, created_at FROM social_payouts "
                               "WHERE source='event' ORDER BY id DESC")]
    for r in hist:
        r["host"] = _person(r["user_id"], "A GharSe host")["first"]
    table(hist, cols=["created_at", "host", "amount", "items_count", "method", "ref"], money=("amount",),
          empty="No host payouts yet.")


def _admin_events():
    now = _now_iso()
    live = [dict(r) for r in q(
        "SELECT e.id, e.title, e.kind, e.area, e.starts_at, e.ends_at, e.capacity, e.host_user_id, e.ticket_price, "
        "(SELECT COALESCE(SUM(b.qty),0) FROM event_bookings b WHERE b.event_id=e.id AND b.status='Booked') taken, "
        "(SELECT COUNT(*) FROM reports r WHERE r.target_kind='event' AND r.target_id=e.id AND r.status='open') "
        "reports FROM events e WHERE e.status='Published' AND e.ends_at > ? ORDER BY e.starts_at", (now,))]
    if not live:
        st.info("No live events.")
    for ev in live:
        p = _person(ev["host_user_id"], "A GharSe host")
        with st.container(border=True):
            st.markdown(
                f"<span class='pill'>{h(ev['kind'])}</span> <b>{h(ev['title'])}</b>"
                + (f" <span class='pill warn'>{ev['reports']} open report(s)</span>" if ev["reports"] else "")
                + f"<br><span class='muted'>Host {h(p['first'])} (#{h(ev['host_user_id'])}) · {h(ev['area'])} · "
                f"{h(when(ev['starts_at'], ev['ends_at']))} · {int(ev['taken'] or 0)}/{int(ev['capacity'] or 0)} seats · "
                f"{h(price_line(ev['ticket_price']))}</span>", unsafe_allow_html=True)
            with st.expander("Cancel this event"):
                with st.form(f"saev{ev['id']}", border=False):
                    reason = st.text_input("Reason (host and guests will see it)", max_chars=200,
                                           key=f"saevr{ev['id']}")
                    go = st.form_submit_button("Cancel event and refund everyone")
                if go:
                    if len((reason or "").strip()) < 4:
                        st.error("Give a reason.")
                    elif cancel_event(ev["id"], reason, "GharSe team"):
                        _set_flash("sa_flash", True, f"\"{ev['title']}\" cancelled; guests and host told.")
                        st.rerun()
                    else:
                        st.warning("Already cancelled.")
    st.markdown("#### All events")
    rows = [dict(r) for r in q("SELECT id, created_at, title, kind, area, starts_at, capacity, ticket_price, status, "
                               "cancel_reason FROM events ORDER BY id DESC")]
    table(rows, money=("ticket_price",), empty="No events yet.")
