# safety.py — reports, blocks, account holds, the GharSe Social and Rooms disclaimers,
# the Social terms, and the grievance officer's contact.
#
# The founder's line is that everyone on GharSe is KYC-identified and the app only
# connects people. That is shown — but a disclaimer is not a defence on its own. Under
# India's IT Rules 2021 an intermediary must publish a grievance officer and act on
# complaints, and once GharSe takes the money for a ticket or a room it carries real
# responsibility for what it sells. So this module also makes the platform act:
# anyone can report, repeated reports from different members in good standing put an
# account on hold automatically, and the team has one page to review, dismiss, resolve,
# hold and lift. Reporters are told when the team has reviewed their report.
#
# Only reporters in good standing count toward an automatic hold: phone-verified, not on
# hold themselves, and either ID-verified or linked to that account by a paid booking or
# room request. A reporter whose earlier report on the same account the team dismissed
# never counts against it again; one whose report was resolved counts again only with a
# report filed after that resolution. Three throwaway sign-ups therefore cannot take a
# verified host's events down, the same people cannot re-hold an account the team has
# cleared, and a genuine victim of a repeat offender is not dropped from the count.
#
# Every state change here is a single conditional statement (… WHERE NOT EXISTS / WHERE
# status='open' … RETURNING id), so a double click or two admins on the same row can
# never close a report twice or send the same message twice. Within one process the
# store's connection lock also makes the WHERE NOT EXISTS inserts single-shot; across
# several workers on Postgres that needs the unique indexes described in the notes.

import streamlit as st

from catalog import REPORT_REASONS, REPORTS_TO_SUSPEND
from store import execute, now_iso, q, setting
from ui import h, inr, kpi, notify, short_dt, split_on_top, table

ADMIN_PAGE_TITLE = "Reports"

FOUNDER_LINE = ("All users are KYC-identified; this application is only a platform for connecting "
                "people, but security lies with the individuals.")

# What a report points at, for the admin page. Table and column names come only from
# this map, never from a request.
_TARGET_TITLES = {
    "event": ("events", "title"),
    "rental": ("rentals", "title"),
    "listing": ("listings", "title"),
    "job": ("jobs", "title"),
    "request": ("requests", "title"),
    "skill_offer": ("skill_offers", "skill"),
}

# Payment states that prove two accounts really dealt with each other (event bookings
# and room requests share these names).
_PAID_STATES = ("Paid", "Paid out", "Refund due", "Refunded")


# ----------------------------------------------------------------------------- helpers
def _uid(v):
    """A user id from a user dict, a users row or a bare id; None when there is none."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v["id"])
    except (KeyError, IndexError, TypeError, ValueError):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _person(user_id):
    """Name, phone and side for a WhatsApp message. The phone lives on the account, or
    on the helper / customer profile for accounts made before it moved there."""
    rows = q("SELECT u.id, u.username, u.full_name, u.phone, u.role, "
             "p.phone pphone, p.owner_name pname, c.phone cphone, c.full_name cname "
             "FROM users u LEFT JOIN providers p ON p.id = u.provider_id "
             "LEFT JOIN customers c ON c.id = u.customer_id WHERE u.id=?", (user_id,))
    if not rows:
        return {"name": "A GharSe member", "phone": "", "role": "user", "username": ""}
    r = dict(rows[0])
    return {
        "name": r["full_name"] or r["pname"] or r["cname"] or r["username"],
        "phone": r["phone"] or r["pphone"] or r["cphone"] or "",
        "role": r["role"] if r["role"] in ("provider", "customer") else "user",
        "username": r["username"],
    }


def _tell_user(user_id, kind, body):
    who = _person(user_id)
    if who["phone"]:
        notify(who["phone"], who["name"], who["role"], kind, body)


def _tell_team(kind, body, ref=None):
    # The team reads these in the WhatsApp Outbox; notifications.order_id is the table's
    # generic reference column.
    notify("", "GharSe team", "admin", kind, body, order_id=ref)


def _money(x):
    """₹438.90 — paise shown, because the itemised Social fees are not whole rupees."""
    paise = int(round(float(x or 0) * 100))
    rupees, rest = divmod(abs(paise), 100)
    out = inr(rupees) + (f".{rest:02d}" if rest else "")
    return ("-" + out) if paise < 0 else out


def _viewer_is_admin():
    try:
        u = st.session_state.get("user") or {}
        return u.get("role") == "admin"
    except Exception:
        return False


def _flash(key):
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


# ----------------------------------------------------------------------------- state checks
_MISSING = object()


def is_blocked(user_id_a, user_id_b):
    """True when either account has blocked the other."""
    a, b = _uid(user_id_a), _uid(user_id_b)
    if a is None or b is None or a == b:
        return False
    return bool(q("SELECT 1 FROM blocks WHERE (user_id=? AND blocked_user_id=?) "
                  "OR (user_id=? AND blocked_user_id=?)", (a, b, b, a)))


def _flag(val):
    """users.suspended as a bool: anything but 0 / NULL / '' means on hold."""
    try:
        return int(val or 0) != 0
    except (TypeError, ValueError):
        return bool(val)


def _suspended_by_id(user_id):
    rows = q("SELECT suspended FROM users WHERE id=?", (user_id,))
    return bool(rows) and _flag(rows[0]["suspended"])


def is_suspended(user_or_row):
    """True when the account is on hold. Takes a user dict, a users row, a user id or a
    numeric string.

    An id is looked up. A users row or the signed-in user dict (anything with a
    'username') is re-read by its id, because the session dict is loaded at sign-in and
    goes stale the moment the account is held. Any other row (an event joined with its
    host's u.suspended, say) is read as it is."""
    if user_or_row is None or isinstance(user_or_row, bool):
        return False
    if isinstance(user_or_row, (int, str)):
        uid = _uid(user_or_row)
        return _suspended_by_id(uid) if uid is not None else False
    if _get(user_or_row, "username", _MISSING) is not _MISSING:
        uid = _uid(user_or_row)
        if uid is not None:
            return _suspended_by_id(uid)
    val = _get(user_or_row, "suspended", _MISSING)
    if val is _MISSING:
        return False
    return _flag(val)



# ----------------------------------------------------------------------------- report
def _counting_reporters(target_user_id):
    """The reporters whose open reports count toward an automatic hold on this account.

    A reporter counts only when in good standing: phone-verified (one phone per
    account), not on hold, and either ID-verified or linked to the account by a paid
    event booking or room request in either direction. "Paid" means real money changed
    hands: a free event's booking is recorded 'Paid' at once with customer_total 0, so
    it does not count — otherwise three phone-only accounts could book a free meetup
    and hold its host.

    What the team did with the reporter's earlier reports on this account matters too:
    - dismissed (the team found it unfounded): that reporter never counts toward a hold
      on this account again, so three people cannot re-hold someone the team cleared.
    - resolved (it was genuine and acted on): only their open reports filed after the
      latest resolution count, so a real victim of a repeat offender stays in the count,
      but a report that was already open when the team acted counts as part of what
      was handled.
      A resolved report with no resolved_at (older data) is treated like a dismissal,
      so old rows cannot re-trigger a hold.
    Everyone else's report still reaches the team."""
    if target_user_id is None:
        return set()
    # One NOT EXISTS for both rules: r counts unless the same reporter has a dismissed
    # report here, or a resolved one with no time or a time at or after r was filed
    # (ISO strings from now_iso() compare lexically; an r with no created_at is never
    # after anything).
    rows = q(
        "SELECT DISTINCT r.reporter_user_id rid FROM reports r "
        "JOIN users u ON u.id = r.reporter_user_id "
        "WHERE r.target_user_id=? AND r.status='open' AND r.reporter_user_id<>r.target_user_id "
        "AND COALESCE(u.phone_verified,0)=1 AND COALESCE(u.suspended,0)=0 "
        "AND NOT EXISTS (SELECT 1 FROM reports c WHERE c.reporter_user_id=r.reporter_user_id "
        "AND c.target_user_id=r.target_user_id AND (c.status='dismissed' "
        "OR (c.status='resolved' AND (COALESCE(c.resolved_at,'')='' "
        "OR c.resolved_at>=COALESCE(r.created_at,''))))) "
        "AND (u.kyc_status='verified' "
        "OR EXISTS (SELECT 1 FROM event_bookings b JOIN events e ON e.id = b.event_id "
        "WHERE b.payment_status IN (?,?,?,?) AND COALESCE(b.customer_total,0) > 0 "
        "AND ((b.user_id=u.id AND e.host_user_id=r.target_user_id) "
        "OR (b.user_id=r.target_user_id AND e.host_user_id=u.id))) "
        "OR EXISTS (SELECT 1 FROM rental_requests rr JOIN rentals rt ON rt.id = rr.rental_id "
        "WHERE rr.payment_status IN (?,?,?,?) AND COALESCE(rr.customer_total,0) > 0 "
        "AND ((rr.user_id=u.id AND rt.owner_user_id=r.target_user_id) "
        "OR (rr.user_id=r.target_user_id AND rt.owner_user_id=u.id))))",
        (target_user_id,) + _PAID_STATES + _PAID_STATES)
    return {r["rid"] for r in rows}


def _maybe_suspend(target_user_id):
    """Put the account on hold once REPORTS_TO_SUSPEND different reporters in good
    standing have open reports against it. Only the request that flips the flag sends
    the messages."""
    if target_user_id is None:
        return False
    n = len(_counting_reporters(target_user_id))
    if n < REPORTS_TO_SUSPEND:
        return False
    reason = (f"On hold automatically: {n} different people reported this account. "
              "The GharSe team is reviewing.")
    flipped = execute("UPDATE users SET suspended=1, suspended_reason=? "
                      "WHERE id=? AND COALESCE(suspended,0)=0 AND role!='admin' RETURNING id",
                      (reason, target_user_id))
    if flipped is None:
        return False
    who = _person(target_user_id)
    _tell_team("Account on hold",
               f"{who['name']} (@{who['username']}, account #{target_user_id}) is on hold after "
               f"{n} different verified members reported them. Review it under Reports — lift the hold or "
               f"keep it, and close the reports.", ref=target_user_id)
    _tell_user(target_user_id, "Account on hold",
               "Your GharSe account is on hold while our team looks into some reports. Until then "
               "you can't host, book or list on GharSe Social or Rooms & PGs. We'll get back to you "
               "within 24 hours.")
    return True


def _file_report(reporter_id, target_kind, target_id, target_user_id, reason, details):
    """Insert one open report unless this reporter already has one open on this target.
    Returns the new id, or None when one was already open."""
    # COALESCE so a report on something with no id still dedupes (NULL = NULL is not true).
    return execute(
        "INSERT INTO reports (reporter_user_id, target_kind, target_id, target_user_id, reason, "
        "details, status, created_at) SELECT ?,?,?,?,?,?,?,? WHERE NOT EXISTS ("
        "SELECT 1 FROM reports WHERE reporter_user_id=? AND target_kind=? "
        "AND COALESCE(target_id,-1)=COALESCE(?,-1) AND status='open') RETURNING id",
        (reporter_id, target_kind, target_id, target_user_id, reason, details, "open", now_iso(),
         reporter_id, target_kind, target_id))


def report_button(reporter, target_kind, target_id, target_user_id, key):
    """A small Report control for anything on GharSe — an event, a room, a profile.

    One open report per reporter per target; nobody can report themselves. When
    REPORTS_TO_SUSPEND different reporters in good standing (see _counting_reporters)
    have open reports against the same account it goes on hold until the team reviews
    it. A held account can still report — the team must hear complaints — but its
    reports never count toward holding anyone."""
    rid = _uid(reporter)
    tuid = _uid(target_user_id)
    if rid is None or (tuid is not None and rid == tuid):
        return
    flash_key = f"{key}_report_flash"
    if st.session_state.get(flash_key):
        _flash(flash_key)
        return
    open_already = q("SELECT 1 FROM reports WHERE reporter_user_id=? AND target_kind=? "
                     "AND COALESCE(target_id,-1)=COALESCE(?,-1) AND status='open'",
                     (rid, target_kind, target_id))
    if open_already:
        st.markdown("<span class='pill flat'>Reported</span> <span class='muted'>The GharSe team "
                    "has your report and is looking into it.</span>", unsafe_allow_html=True)
        return
    with st.popover("Report", key=f"{key}_report_pop"):
        st.markdown("<span class='muted'>Tell the GharSe team what went wrong. The person you "
                    "report isn't told who reported them.</span>", unsafe_allow_html=True)
        with st.form(f"{key}_report_form", clear_on_submit=True, border=False):
            reason = st.selectbox("What happened?", REPORT_REASONS, key=f"{key}_report_reason")
            details = st.text_area("Details (optional)", max_chars=600, key=f"{key}_report_details",
                                   placeholder="When it happened and what was said or done.")
            sent = st.form_submit_button("Send report", type="primary")
        if sent:
            new_id = _file_report(rid, target_kind, target_id, tuid, reason,
                                  (details or "").strip() or None)
            if new_id is None:
                st.session_state[flash_key] = "You've already reported this — the team has it."
            else:
                _maybe_suspend(tuid)
                st.session_state[flash_key] = (
                    "Thank you — your report is with the GharSe team. We read every report and act "
                    f"on it, and we'll message you once it has been reviewed. If {REPORTS_TO_SUSPEND} "
                    "different verified members report the same account, it is put on hold until "
                    "the team has reviewed it. You can also block them. If you are in danger right "
                    "now, call 112.")
            st.rerun()


# ----------------------------------------------------------------------------- block
def block_button(user, other_user_id, key):
    """Block or unblock another account. Idempotent: blocking twice is one block."""
    me, other = _uid(user), _uid(other_user_id)
    if me is None or other is None or me == other:
        return
    mine = q("SELECT 1 FROM blocks WHERE user_id=? AND blocked_user_id=?", (me, other))
    if mine:
        if st.button("Unblock", key=f"{key}_unblock",
                     help="They will be able to see and book what you post again."):
            execute("DELETE FROM blocks WHERE user_id=? AND blocked_user_id=?", (me, other))
            st.rerun()
        st.caption("You've blocked this account.")
    else:
        if st.button("Block", key=f"{key}_block",
                     help="You won't see each other's events, rooms or requests, and they can't book yours."):
            execute("INSERT INTO blocks (user_id, blocked_user_id, created_at) SELECT ?,?,? "
                    "WHERE NOT EXISTS (SELECT 1 FROM blocks WHERE user_id=? AND blocked_user_id=?) "
                    "RETURNING id", (me, other, now_iso(), me, other))
            st.rerun()


# ----------------------------------------------------------------------------- grievance
def grievance_line():
    """The grievance officer's contact, as escaped markup. The IT Rules 2021 require it
    to be published; until admin sets it, admin is told where, and everyone else is
    pointed at the team."""
    name = (setting("grievance_name", str) or "").strip()
    email = (setting("grievance_email", str) or "").strip()
    phone = (setting("grievance_phone", str) or "").strip()
    parts = [p for p in (name, email, phone) if p]
    if parts:
        return ("<span class='muted'>Grievance officer: " + " · ".join(h(p) for p in parts)
                + " — complaints are acknowledged within 24 hours and resolved within 15 days.</span>")
    if _viewer_is_admin():
        return ("<span class='pill warn'>Grievance officer not set</span> <span class='muted'>The IT "
                "Rules 2021 require GharSe to publish one. Add the name, email and phone under "
                "Fees &amp; Settings.</span>")
    return ("<span class='muted'>Questions or complaints? Write to the GharSe team — use Report on "
            "anything that worries you and the team will get back to you.</span>")


# ----------------------------------------------------------------------------- disclaimers
def _fee_line():
    plat, ops = setting("platform_pct", float), setting("ops_pct", float)
    ex = split_on_top(399)
    return (f"Bookings and fees are processed by GharSe, which adds a platform fee of {plat:g}% and a "
            f"processing fee of {ops:g}% on top of the price — a {_money(ex['base'])} ticket costs you "
            f"{_money(ex['customer_total'])} and the host keeps {_money(ex['host_payout'])}. Every "
            "booking shows all four figures before you pay.")


_SOCIAL_RULES = [
    "Every host is identity-verified before they can list an event.",
    "The exact address is shared only after your booking is paid. Until then you see the area.",
    "House parties are 18+. Carry an ID — the host may ask.",
    "No alcohol is sold through GharSe. Hosts are responsible for local permissions — excise, "
    "their society, and the police for large gatherings.",
    "Meet in groups, tell someone where you're going, and leave if anything feels wrong.",
    "Report anything unsafe with the Report button on the event or the profile.",
    "Refunds follow the refund rule shown on each event.",
]

_RENTAL_RULES = [
    "Every owner is identity-verified before they can list a room.",
    "Visit the room before you pay any deposit.",
    "Never pay outside the app — GharSe can only help with money that went through GharSe.",
    "GharSe does not inspect properties. Check the room, the lease and the owner yourself.",
    "Report fake or misleading listings with the Report button.",
]


def _rules_markup(rules):
    return "".join(f"<li>{h(r)}</li>" for r in rules)


def disclaimer(kind):
    """The platform disclaimer for "social" or "rental": the founder's line, the rules
    that actually apply, and the grievance officer."""
    rules = _SOCIAL_RULES if kind == "social" else _RENTAL_RULES
    label = "GharSe Social — please read" if kind == "social" else "Rooms & PGs — please read"
    with st.container(border=True):
        st.markdown(
            f"<span class='pill'>{h(label)}</span><br>"
            f"<span class='muted'><i>{h(FOUNDER_LINE)}</i></span>"
            f"<ul>{_rules_markup(rules)}</ul>"
            f"<span class='muted'>GharSe acts on reports: {REPORTS_TO_SUSPEND} reports from different "
            f"verified members put an account on hold until the team reviews it. In an emergency "
            f"call 112."
            f"</span><br>{grievance_line()}",
            unsafe_allow_html=True,
        )


_CONDUCT = [
    "Be who your verified profile says you are. One account per person.",
    "Respect the host's house rules, the neighbours and the end time.",
    "No harassment, no pressure, no means no. Hosts may ask anyone to leave.",
    "Nothing illegal at an event — no drugs, no sale of alcohol, no gambling for money.",
    "Keep payments in the app. Asking for or offering money outside GharSe is reported and removed.",
    "Hosts describe the event honestly and run it as listed, or cancel in the app so guests are refunded.",
]


def accept_terms(user):
    """The GharSe Social terms with a required tick. Records users.social_terms_at and
    returns True once accepted — on later visits it returns True without asking."""
    uid = _uid(user)
    if uid is None:
        return False
    rows = q("SELECT social_terms_at FROM users WHERE id=?", (uid,))
    if rows and rows[0]["social_terms_at"]:
        return True
    with st.container(border=True):
        st.markdown("<div class='page-sub'>Before you join GharSe Social</div>"
                    "<span class='muted'>One read, once. You won't be asked again.</span>",
                    unsafe_allow_html=True)
        disclaimer("social")
        st.markdown(
            f"<b>How to behave</b><ul>{_rules_markup(_CONDUCT)}</ul>"
            f"<b>Money</b><br><span class='muted'>{h(_fee_line())}</span>",
            unsafe_allow_html=True,
        )
        with st.form(f"social_terms_{uid}", border=False):
            tick = st.checkbox("I've read these terms. I understand GharSe connects people and that I'm "
                               "responsible for my own safety, and I agree to the rules above.",
                               key=f"social_terms_tick_{uid}")
            ok = st.form_submit_button("I agree", type="primary")
        if ok:
            if not tick:
                st.error("Tick the box to agree before you continue.")
                return False
            stamp = now_iso()
            execute("UPDATE users SET social_terms_at=? WHERE id=? AND social_terms_at IS NULL "
                    "RETURNING id", (stamp, uid))
            try:
                user["social_terms_at"] = stamp
            except TypeError:
                pass
            st.rerun()
    return False


# ----------------------------------------------------------------------------- admin
def _target_label(kind, target_id):
    spec = _TARGET_TITLES.get(kind)
    base = f"{kind or 'item'} #{target_id}" if target_id is not None else (kind or "item")
    if not spec or target_id is None:
        return base
    table_name, col = spec
    try:
        rows = q(f"SELECT {col} t FROM {table_name} WHERE id=?", (target_id,))
    except Exception:
        return base
    return f"{base} · {rows[0]['t']}" if rows and rows[0]["t"] else base


def _tell_reporter(reporter_user_id, created_at=None):
    """The IT Rules promise a complaint is acted on; tell the reporter it was reviewed —
    never what was done to the other person."""
    if reporter_user_id is None:
        return
    when = f" on {short_dt(created_at)}" if created_at else ""
    _tell_user(reporter_user_id, "Your report",
               f"The GharSe team has reviewed the report you sent{when} and closed it. Thank you for "
               "telling us — reports like yours keep GharSe safe. If anything else happens, report it "
               "again from the app or write to our grievance officer.")


def _close_report(report_id, status, admin_id):
    """Close one open report. Only the request that closes it tells the reporter."""
    done = execute("UPDATE reports SET status=?, resolved_by=?, resolved_at=? "
                   "WHERE id=? AND status='open' RETURNING id",
                   (status, admin_id, now_iso(), report_id))
    if done is not None:
        rows = q("SELECT reporter_user_id, created_at FROM reports WHERE id=?", (report_id,))
        if rows:
            _tell_reporter(rows[0]["reporter_user_id"], rows[0]["created_at"])
    return done


def _close_all_for(target_user_id, status, admin_id):
    """Close every open report on an account, one atomic close each, and tell each
    reporter once."""
    told = set()
    for r in q("SELECT id, reporter_user_id, created_at FROM reports "
               "WHERE target_user_id=? AND status='open' ORDER BY id", (target_user_id,)):
        done = execute("UPDATE reports SET status=?, resolved_by=?, resolved_at=? "
                       "WHERE id=? AND status='open' RETURNING id",
                       (status, admin_id, now_iso(), r["id"]))
        if done is not None and r["reporter_user_id"] not in told:
            told.add(r["reporter_user_id"])
            _tell_reporter(r["reporter_user_id"], r["created_at"])
    return len(told)


def _admin_handle(admin_id):
    rows = q("SELECT username FROM users WHERE id=?", (admin_id,)) if admin_id is not None else []
    return f"@{rows[0]['username']}" if rows else f"admin #{admin_id}"


def _suspend(target_user_id, reason, admin_id):
    done = execute("UPDATE users SET suspended=1, suspended_reason=? "
                   "WHERE id=? AND COALESCE(suspended,0)=0 AND role!='admin' RETURNING id",
                   (reason, target_user_id))
    if done is not None:
        _tell_user(target_user_id, "Account on hold",
                   f"Your GharSe account is on hold. Reason: {reason}. Until it's lifted you can't "
                   "host, book or list on GharSe Social or Rooms & PGs. Reply to this message if you "
                   "think this is a mistake.")
        # The audit trail of who held whom, until users.suspended_by / suspended_at exist.
        who = _person(target_user_id)
        _tell_team("Hold placed",
                   f"{_admin_handle(admin_id)} put @{who['username']} (account #{target_user_id}) on "
                   f"hold. Reason: {reason}", ref=target_user_id)
    return done


def _unsuspend(target_user_id, note, admin_id, close_reports):
    # <> 0 rather than = 1, so a hold stored as any other non-zero value can be lifted too.
    done = execute("UPDATE users SET suspended=0, suspended_reason=NULL "
                   "WHERE id=? AND COALESCE(suspended,0)<>0 RETURNING id", (target_user_id,))
    if done is not None:
        if close_reports:
            _close_all_for(target_user_id, "resolved", admin_id)
        _tell_user(target_user_id, "Account active",
                   "Your GharSe account is active again. " + (f"Note from the team: {note}. " if note else "")
                   + "Thank you for your patience.")
        who = _person(target_user_id)
        _tell_team("Hold lifted",
                   f"{_admin_handle(admin_id)} lifted the hold on @{who['username']} (account "
                   f"#{target_user_id})." + (f" Note: {note}" if note else ""), ref=target_user_id)
    return done


def _hold_controls(acct, admin_id, key):
    """Suspend / lift controls for one account."""
    if acct["role"] == "admin":
        st.caption("Admin accounts can't be put on hold here.")
        return
    if acct["suspended"]:
        with st.form(f"{key}_lift", border=False):
            note = st.text_input("Note to them (optional)", key=f"{key}_lift_note",
                                 placeholder="e.g. We spoke to everyone involved.")
            close = st.checkbox("Also close their open reports as resolved", value=True,
                                key=f"{key}_lift_close")
            if st.form_submit_button("Lift hold"):
                if _unsuspend(acct["id"], note.strip(), admin_id, close) is None:
                    st.session_state["rep_flash"] = "That hold was already lifted."
                else:
                    st.session_state["rep_flash"] = "Hold lifted and they've been told."
                st.rerun()
    else:
        with st.form(f"{key}_hold", border=False):
            reason = st.text_input("Reason — they will see this", key=f"{key}_hold_reason",
                                   placeholder="e.g. Several guests reported feeling unsafe at an event.")
            if st.form_submit_button("Put account on hold"):
                if not reason.strip():
                    st.error("Write a reason — the account holder is told why.")
                    return
                if _suspend(acct["id"], reason.strip(), admin_id) is None:
                    st.session_state["rep_flash"] = "That account was already on hold."
                else:
                    st.session_state["rep_flash"] = "Account on hold and they've been told why."
                st.rerun()


def admin_reports(admin_user):
    if _get(admin_user, "role") != "admin":
        st.error("Only the GharSe team can open this page.")
        return
    st.markdown("<div class='page-title'>Reports</div>"
                "<div class='page-sub'>What people have reported, grouped by account. Dismiss what "
                "needs no action, resolve what you've handled, and hold accounts that aren't safe.</div>",
                unsafe_allow_html=True)
    admin_id = _uid(admin_user)
    _flash("rep_flash")
    st.markdown(grievance_line(), unsafe_allow_html=True)

    n_open = q("SELECT COUNT(*) c FROM reports WHERE status='open'")[0]["c"]
    n_accts = q("SELECT COUNT(*) c FROM (SELECT DISTINCT target_user_id FROM reports "
                "WHERE status='open') AS t")[0]["c"]
    n_held = q("SELECT COUNT(*) c FROM users WHERE COALESCE(suspended,0)<>0")[0]["c"]
    c = st.columns(3)
    kpi(c[0], "Open reports", f"{n_open}")
    kpi(c[1], "Accounts reported", f"{n_accts}")
    kpi(c[2], "On hold", f"{n_held}")
    st.write("")

    st.markdown("#### Open reports")
    rows = q("SELECT r.*, ru.username reporter FROM reports r "
             "LEFT JOIN users ru ON ru.id = r.reporter_user_id "
             "WHERE r.status='open' ORDER BY r.target_user_id, r.id")
    if not rows:
        st.caption("No open reports. Anything people report appears here.")
    groups = {}
    for r in rows:
        groups.setdefault(r["target_user_id"], []).append(dict(r))
    # Most-reported first.
    order = sorted(groups, key=lambda t: (-len({x["reporter_user_id"] for x in groups[t]}),
                                          groups[t][0]["id"]))
    for tuid in order:
        reps = groups[tuid]
        people = len({x["reporter_user_id"] for x in reps})
        counting = _counting_reporters(tuid)
        acct = None
        if tuid is not None:
            a = q("SELECT id, username, full_name, role, suspended, suspended_reason, kyc_status "
                  "FROM users WHERE id=?", (tuid,))
            acct = dict(a[0]) if a else None
        with st.container(border=True):
            if acct:
                who = _person(tuid)
                held = (" <span class='pill warn'>On hold</span>" if acct["suspended"] else "")
                st.markdown(
                    f"<b>{h(acct['full_name'] or acct['username'])}</b> "
                    f"<span class='muted'>@{h(acct['username'])} · account #{h(acct['id'])} · "
                    f"{h(who['phone'] or 'no phone on file')} · KYC {h(acct['kyc_status'] or 'none')}"
                    f"</span>{held}<br>"
                    f"<span class='pill'>{len(reps)} open report{'s' if len(reps) != 1 else ''}</span> "
                    f"<span class='pill flat'>from {people} {'people' if people != 1 else 'person'}</span> "
                    f"<span class='pill flat'>{len(counting)} of {REPORTS_TO_SUSPEND} toward an automatic hold</span>"
                    + (f"<br><span class='muted'>Hold reason: {h(acct['suspended_reason'])}</span>"
                       if acct["suspended"] and acct["suspended_reason"] else ""),
                    unsafe_allow_html=True)
            else:
                st.markdown("<b>No account linked</b> <span class='muted'>These reports point at content "
                            "whose owner isn't known.</span>", unsafe_allow_html=True)
            reasons = {}
            for x in reps:
                reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
            st.markdown(" ".join(f"<span class='pill flat'>{h(k)} × {v}</span>" for k, v in reasons.items()),
                        unsafe_allow_html=True)
            for x in reps:
                cols = st.columns([5, 1, 1])
                cols[0].markdown(
                    f"<b>{h(x['reason'])}</b> <span class='muted'>· {h(_target_label(x['target_kind'], x['target_id']))}"
                    f" · by @{h(x['reporter'] or 'deleted account')} · {h(short_dt(x['created_at']))}</span>"
                    + ("" if x["reporter_user_id"] in counting else
                       " <span class='pill flat'>Not counted toward a hold</span>")
                    + (f"<br><span class='muted'>{h(x['details'])}</span>" if x["details"] else ""),
                    unsafe_allow_html=True)
                if cols[1].button("Dismiss", key=f"rep_dis_{x['id']}", use_container_width=True,
                                  help="No action needed."):
                    if _close_report(x["id"], "dismissed", admin_id) is None:
                        st.session_state["rep_flash"] = "Someone already closed that report."
                    st.rerun()
                if cols[2].button("Resolve", key=f"rep_res_{x['id']}", use_container_width=True,
                                  help="You've dealt with it."):
                    if _close_report(x["id"], "resolved", admin_id) is None:
                        st.session_state["rep_flash"] = "Someone already closed that report."
                    st.rerun()
            if acct:
                _hold_controls(acct, admin_id, f"rep_acct_{tuid}")

    st.markdown("#### Accounts on hold")
    held = q("SELECT id, username, full_name, role, suspended, suspended_reason FROM users "
             "WHERE COALESCE(suspended,0)<>0 ORDER BY id")
    if not held:
        st.caption("No accounts are on hold.")
    for r in held:
        acct = dict(r)
        open_n = q("SELECT COUNT(*) c FROM reports WHERE target_user_id=? AND status='open'",
                   (acct["id"],))[0]["c"]
        with st.container(border=True):
            st.markdown(
                f"<b>{h(acct['full_name'] or acct['username'])}</b> "
                f"<span class='muted'>@{h(acct['username'])} · account #{h(acct['id'])} · "
                f"{open_n} open report{'s' if open_n != 1 else ''}</span><br>"
                f"<span class='muted'>{h(acct['suspended_reason'] or 'No reason recorded.')}</span>",
                unsafe_allow_html=True)
            _hold_controls(acct, admin_id, f"rep_held_{acct['id']}")

    st.markdown("#### Recently closed")
    closed = q("SELECT r.resolved_at, r.status, r.reason, r.target_kind, r.target_id, "
               "tu.username reported, ru.username reporter, au.username closed_by FROM reports r "
               "LEFT JOIN users tu ON tu.id = r.target_user_id "
               "LEFT JOIN users ru ON ru.id = r.reporter_user_id "
               "LEFT JOIN users au ON au.id = r.resolved_by "
               "WHERE r.status!='open' ORDER BY r.id DESC LIMIT 50")
    table(closed, empty="Nothing closed yet.")
