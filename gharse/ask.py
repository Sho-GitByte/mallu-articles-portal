# ask.py — the neighbourhood Ask board.
#
# A customer asks for anything a neighbour might do — a special dish, jasmine strung
# for a function, a saree blouse, someone to sit with an elderly parent — and helpers
# nearby send a quote. The two settle it with some consideration: a fixed price, an
# offer, an exchange, or plain neighbourly help.
#
# It sits on the requests and bids tables. Rows written before the board existed have
# kind/category and no ask_group; they are mapped onto a group so they still show up
# in the right place, and display their original category.
#
# Care asks (children, elders, pets, patients — inside someone's home) carry a safety
# note the asker must acknowledge, and only admin-verified helpers may quote on them.
#
# Contact details stay private until a quote is accepted; then the two parties see
# each other's phone number and nobody else does.

from datetime import date, timedelta

import streamlit as st

from catalog import AREAS, ASK_CARE_GROUPS, ASK_GROUPS, CATEGORIES, CONSIDERATIONS
from store import execute, now_iso, q, today_ist
from ui import customer_of, h, idx, inr, kpi, notify_customer, notify_provider, provider_of, short_dt, table, wa_link

ASK_PAGE_TITLES = {
    "customer": "Ask the Neighbourhood",
    "provider": "Neighbourhood Asks",
    "admin": "Asks",
}

# One line per group, so a customer knows where her ask belongs before opening it.
_GROUP_HINTS = {
    "Food & Kitchen": "home meals, a dish you miss, tiffin, festival and function cooking",
    "Flowers & Pooja": "mallige stringing, garlands, pooja flowers, rangoli, decoration",
    "Clothes & Tailoring": "saree designing, fall & pico, blouses, alterations, ironing",
    "Care at Home": "babysitting, elder care, pet care, patient attendant",
    "Home Help": "cleaning, cooking help, plants, organising, deep-clean",
    "Beauty & Wellness": "mehendi, beautician at home, hair styling, yoga",
    "Learning & Tuition": "school tuition, spoken English, music, dance, phone basics",
    "Handmade & Gifts": "hampers, crochet, diyas & candles, art, return gifts",
    "Errands & Events": "event helpers, packing & shifting, errands, party serving",
}

_SAFETY_POINTS = [
    "Meet her first, at your home with someone else present, before you leave her alone with "
    "your child, parent or pet.",
    "Only verified helpers can quote on care asks — look for the Verified chip.",
    "Never pay in advance for care. Pay after the work is done, or day by day.",
    "Tell someone at home her name and when she is coming.",
]

# How a ask was closed by the team (not one of the customer's own statuses).
CLOSED = "Closed"
OPEN, ASSIGNED, COMPLETED, CANCELLED = "Open", "Assigned", "Completed", "Cancelled"
_STATUS_ORDER = [OPEN, ASSIGNED, COMPLETED, CANCELLED, CLOSED]
_STATUS_LABEL = {OPEN: "Open", ASSIGNED: "Taken up", COMPLETED: "Completed",
                 CANCELLED: "Cancelled", CLOSED: "Closed by GharSe"}


def _consideration(prefix, fallback_index):
    for c in CONSIDERATIONS:
        if c.lower().startswith(prefix):
            return c
    return CONSIDERATIONS[fallback_index]


FIXED = _consideration("fixed", 0)
OFFERS = _consideration("open", 1)
EXCHANGE = _consideration("exchange", 2)
FREE = _consideration("free", 3)
_NO_PRICE = {EXCHANGE, FREE}

# ----------------------------------------------------------------------------- helpers
# Old rows were posted with the kind/category taxonomy; this places each old category
# in the Ask group a person would look for it under.
_LEGACY_GROUP = {
    "Flowers & Garlands": "Flowers & Pooja",
    "Tailoring & Stitching": "Clothes & Tailoring", "Saree Work & Embroidery": "Clothes & Tailoring",
    "Alterations": "Clothes & Tailoring", "Ironing & Laundry": "Clothes & Tailoring",
    "Handloom": "Handmade & Gifts", "Handicraft & Decor": "Handmade & Gifts",
    "Artificial Jewellery": "Handmade & Gifts", "Candles & Soaps": "Handmade & Gifts",
    "Gifts & Hampers": "Handmade & Gifts", "Knitting & Crochet": "Handmade & Gifts",
    "Mehendi": "Beauty & Wellness", "Beauty at Home": "Beauty & Wellness",
    "Tuition": "Learning & Tuition", "Day-care / Baby-sitting": "Care at Home",
    "Event Cooking": "Food & Kitchen", "Packing & Assembly": "Errands & Events",
    "Petty Home Chores": "Home Help",
}
for _c in CATEGORIES.get("Food", []):
    _LEGACY_GROUP.setdefault(_c, "Food & Kitchen")


def group_of(req):
    """The Ask group a row belongs to, including rows posted before groups existed."""
    g = req.get("ask_group")
    if g:
        return g
    g = _LEGACY_GROUP.get(req.get("category") or "")
    if g:
        return g
    return {"Food": "Food & Kitchen", "Product": "Handmade & Gifts"}.get(req.get("kind") or "", "Other")


def label_of(req):
    """What to call the row's category on screen: its group, else the old category."""
    return req.get("ask_group") or req.get("category") or req.get("kind") or "Ask"


def is_care(req):
    return group_of(req) in ASK_CARE_GROUPS


def _is_something_else(ask):
    return (ask or "").lower().startswith("something else")


def _kind_for(group):
    if group == "Food & Kitchen":
        return "Food"
    if group in ("Handmade & Gifts", "Flowers & Pooja"):
        return "Product"
    return "Service"


def _hint(group):
    if group in _GROUP_HINTS:
        return _GROUP_HINTS[group]
    asks = [a for a in ASK_GROUPS.get(group, []) if not _is_something_else(a)]
    return ", ".join(a.lower() for a in asks[:4])


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10]).strftime("%a %d %b")
    except (TypeError, ValueError):
        return str(v) if v else "—"


def _first_name(name):
    name = (name or "").strip()
    return name.split()[0] if name else "A neighbour"


def _status_pill(status):
    s = status or OPEN
    cls = "pill ok" if s in (ASSIGNED, COMPLETED) else ("pill warn" if s in (CANCELLED, CLOSED) else "pill")
    return f"<span class='{cls}'>{h(_STATUS_LABEL.get(s, s))}</span>"


def _money_line(req):
    """How the asker wants to settle it, in one phrase."""
    cons = req.get("consideration")
    budget = req.get("budget")
    if cons == FIXED and budget:
        return f"Will pay {inr(budget)}"
    if cons in _NO_PRICE:
        return h(cons)
    if budget:
        return f"Budget about {inr(budget)} · open to offers"
    return h(cons) if cons else "Open to offers"


def _quote_price(b):
    if b.get("price"):
        return inr(b["price"])
    return "No charge"


def _ask_body(req, show_asker=True):
    care = "<span class='pill warn'>Care · verified helpers only</span>" if is_care(req) else ""
    asker = f"Asked by {h(_first_name(req.get('asker')))} · " if show_asker and "asker" in req else ""
    details = f"<br>{h(req['description'])}" if (req.get("description") or "").strip() else ""
    return (
        f"**{h(req.get('title') or label_of(req))}** <span class='pill'>{h(label_of(req))}</span>{care}<br>"
        f"<span class='muted'>{asker}{h(req.get('area') or '—')} · needed by {h(_date(req.get('needed_by')))} · "
        f"posted {h(short_dt(req.get('created_at')))}</span>"
        f"{details}<br><b>{_money_line(req)}</b>"
    )


def _flash(key):
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


# ----------------------------------------------------------------------------- customer
def ask_customer(user):
    cust = customer_of(user)
    st.markdown(
        f"<div class='page-title'>{ASK_PAGE_TITLES['customer']}</div>"
        "<div class='page-sub'>Need something a neighbour could do? A dish you miss, garlands for a function, "
        "a blouse stitched, someone to sit with your mother for an afternoon. Ask here — helpers nearby "
        "send a quote, you choose one, and you settle it between you.</div>",
        unsafe_allow_html=True,
    )
    if not cust:
        st.error("Your account isn't linked to a customer profile yet, so you can't post an ask. "
                 "Please contact the GharSe team.")
        return
    _flash("ask_flash")
    _post_form(cust)
    st.write("")
    _my_asks(cust)


def _post_form(cust):
    n = st.session_state.get("ask_form_n", 0)   # bumped after a post, which clears the form
    k = lambda name: f"ask_{name}_{n}"          # noqa: E731

    with st.container(border=True):
        st.markdown("#### Post an ask")
        groups = list(ASK_GROUPS)
        group = st.selectbox("What kind of help?", groups, key=k("group"),
                             format_func=lambda g: f"{g} — {_hint(g)}")
        ask = st.selectbox("What exactly?", ASK_GROUPS[group], key=k("what"))
        other = ""
        if _is_something_else(ask):
            other = st.text_input("Tell us what you need, in a few words",
                                  placeholder="e.g. Someone to teach my daughter Bharatanatyam basics",
                                  key=k("other"))
        details = st.text_area(
            "Details", height=90, key=k("details"),
            placeholder="Quantity, timing, preferences, anything she should know before quoting.")

        c = st.columns(2)
        consideration = c[0].radio("How would you like to settle it?", CONSIDERATIONS, key=k("cons"))
        budget = 0.0
        with c[1]:
            if consideration == FIXED:
                budget = st.number_input("What you'll pay (₹)", 0.0, 500000.0, 0.0, step=50.0, key=k("budget"))
            elif consideration in _NO_PRICE:
                st.caption("No money changes hands. Say in the details what you'd do in return, "
                           "or just that you'd be grateful for the help.")
            else:
                budget = st.number_input("Rough budget (₹, optional)", 0.0, 500000.0, 0.0, step=50.0,
                                         key=k("budget"))
            needed = st.date_input("Needed by", today_ist() + timedelta(days=3), min_value=today_ist(),
                                   key=k("needed"))
            area = st.selectbox("Area", AREAS, index=idx(AREAS, cust.get("area")), key=k("area"))

        ack = False
        if group in ASK_CARE_GROUPS:
            with st.container(border=True):
                points = "".join(f"<li>{p}</li>" for p in _SAFETY_POINTS)
                st.markdown(f"<b>Before you invite someone into your home</b><ul>{points}</ul>",
                            unsafe_allow_html=True)
                ack = st.checkbox("I've read this and will follow it", key=k("ack"))

        if st.button("Post ask", type="primary", width="stretch", key=k("post")):
            title = other.strip() if _is_something_else(ask) else ask
            errors = []
            if _is_something_else(ask) and not other.strip():
                errors.append("Tell neighbours what you need in a few words.")
            if consideration == FIXED and not budget:
                errors.append("Enter the amount you'll pay, or choose “Open to offers”.")
            if group in ASK_CARE_GROUPS and not ack:
                errors.append("Please read the safety note and tick the box — care asks need it.")
            if errors:
                for e in errors:
                    st.error(e)
                return
            execute(
                "INSERT INTO requests (customer_id, kind, category, title, description, area, budget, "
                "needed_by, status, ask_group, consideration, safety_ack, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cust["id"], _kind_for(group), ask, title, details.strip(), area,
                 (budget or None) if consideration not in _NO_PRICE else None,
                 needed.isoformat(), OPEN, group, consideration,
                 1 if (group in ASK_CARE_GROUPS and ack) else 0, now_iso()),
            )
            st.session_state["ask_form_n"] = n + 1
            st.session_state["ask_flash"] = (f"Posted “{title}”. Helpers in {area} can see it now — "
                                             "their quotes will appear below.")
            st.rerun()


def _bids_for(request_id):
    rows = q("SELECT b.*, p.display_name, p.area AS helper_area, p.verified, p.phone AS helper_phone "
             "FROM bids b JOIN providers p ON p.id=b.provider_id WHERE b.request_id=?", (request_id,))
    bids = [dict(r) for r in rows if (r["status"] or "") != "Withdrawn"]
    rank = {"Accepted": 0, "Offered": 1, "Declined": 2}
    bids.sort(key=lambda b: (rank.get(b["status"], 3), b["price"] is None, b["price"] or 0, b["id"]))
    return bids


def _my_asks(cust):
    rows = q("SELECT * FROM requests WHERE customer_id=? ORDER BY id DESC", (cust["id"],))
    st.markdown("#### Your asks")
    if not rows:
        st.caption("You haven't asked for anything yet. Your asks and every quote on them will show here.")
        return
    for r in rows:
        req = dict(r)
        status = req.get("status") or OPEN
        bids = _bids_for(req["id"])
        with st.container(border=True):
            c = st.columns([4, 1])
            c[0].markdown(_ask_body(req, show_asker=False), unsafe_allow_html=True)
            c[1].markdown(_status_pill(status), unsafe_allow_html=True)

            if status == CLOSED:
                st.caption("The GharSe team closed this ask. We've sent you a message saying why.")

            accepted = next((b for b in bids if b["status"] == "Accepted"), None)
            if accepted and status in (ASSIGNED, COMPLETED):
                phone = (accepted.get("helper_phone") or "").strip()
                st.markdown(
                    f"<b>{h(accepted['display_name'])}</b> is taking this up — "
                    f"{h(_quote_price(accepted))}. Call or WhatsApp her on <b>{h(phone or 'no number on file')}</b>.",
                    unsafe_allow_html=True)
                mine = (cust.get("phone") or "").strip()
                st.caption(f"Your number {mine} has been shared with her." if mine else
                           "Add your phone number in your preferences so she can reach you.")
                link = wa_link(phone, f"Hi {accepted['display_name']}, this is about my GharSe ask "
                                      f"\"{req.get('title')}\".") if phone else None
                if link and status == ASSIGNED:
                    st.link_button("WhatsApp her", link, key=f"askwa{req['id']}")

            if not bids:
                if status == OPEN:
                    st.caption("No quotes yet. Helpers nearby see it as soon as it's posted.")
            else:
                st.markdown(f"<span class='muted'>{len(bids)} quote{'s' if len(bids) != 1 else ''}</span>",
                            unsafe_allow_html=True)
            for b in bids:
                bc = st.columns([3, 1, 1])
                badge = "<span class='pill ok'>Verified</span>" if b["verified"] else ""
                note = f" · {h(b['note'])}" if b.get("note") else ""
                eta = f"Ready by {h(b['eta'])}" if b.get("eta") else ""
                bc[0].markdown(
                    f"{h(b['display_name'])} · {h(b['helper_area'] or '')} {badge}<br>"
                    f"<span class='muted'>{eta}{note}</span>", unsafe_allow_html=True)
                bc[1].markdown(f"<div class='price'>{h(_quote_price(b))}</div>", unsafe_allow_html=True)
                if status == OPEN and b["status"] == "Offered":
                    if bc[2].button("Accept", key=f"askacc{b['id']}", width="stretch"):
                        _accept(cust, req, b)
                        st.rerun()
                else:
                    bc[2].markdown(f"<span class='pill'>{h(b['status'])}</span>", unsafe_allow_html=True)

            bcols = st.columns(3)
            if status == ASSIGNED and bcols[0].button("Mark completed", key=f"askdone{req['id']}",
                                                      width="stretch"):
                execute("UPDATE requests SET status=? WHERE id=?", (COMPLETED, req["id"]))
                if accepted:
                    notify_provider(accepted["provider_id"], "ask_completed",
                                    f"{_first_name(cust.get('full_name'))} marked \"{req.get('title')}\" as "
                                    "completed. Thank you for helping a neighbour.")
                st.rerun()
            if status == OPEN and bcols[0].button("Cancel ask", key=f"askcancel{req['id']}", width="stretch"):
                execute("UPDATE requests SET status=? WHERE id=? AND status=?", (CANCELLED, req["id"], OPEN))
                execute("UPDATE bids SET status='Declined' WHERE request_id=? AND status='Offered'", (req["id"],))
                st.rerun()


def _accept(cust, req, b):
    # Re-read both rows: the helper may have withdrawn, or another tab accepted first.
    fresh = q("SELECT status FROM requests WHERE id=?", (req["id"],))
    bid = q("SELECT status FROM bids WHERE id=?", (b["id"],))
    if not fresh or fresh[0]["status"] != OPEN or not bid or bid[0]["status"] != "Offered":
        st.session_state["ask_flash"] = "That quote is no longer available."
        return
    execute("UPDATE bids SET status='Accepted' WHERE id=?", (b["id"],))
    others = q("SELECT provider_id FROM bids WHERE request_id=? AND id<>? AND status='Offered'",
               (req["id"], b["id"]))
    execute("UPDATE bids SET status='Declined' WHERE request_id=? AND id<>? AND status='Offered'",
            (req["id"], b["id"]))
    execute("UPDATE requests SET status=? WHERE id=?", (ASSIGNED, req["id"]))
    who = cust.get("full_name") or "Your neighbour"
    phone = (cust.get("phone") or "").strip()
    notify_provider(
        b["provider_id"], "ask_accepted",
        f"{who} accepted your quote ({_quote_price(b)}) for \"{req.get('title')}\" in {req.get('area')}, "
        f"needed by {_date(req.get('needed_by'))}."
        + (f" Their number: {phone}." if phone else " Open GharSe to see the details."))
    for o in others:
        notify_provider(o["provider_id"], "ask_declined",
                        f"\"{req.get('title')}\" in {req.get('area')} went to another helper this time. "
                        "Thank you for quoting — there are more asks waiting on GharSe.")
    st.session_state["ask_flash"] = (f"You chose {b['display_name']}. Her number is below — "
                                     "and yours has been shared with her.")


# ----------------------------------------------------------------------------- helper
def ask_helper(user):
    p = provider_of(user)
    st.markdown(
        f"<div class='page-title'>{ASK_PAGE_TITLES['provider']}</div>"
        "<div class='page-sub'>What your neighbours are asking for, nearest first. Send a quote on anything "
        "you can do well — they choose, and you settle it between you.</div>",
        unsafe_allow_html=True,
    )
    if not p:
        st.error("Your account isn't linked to a helper profile yet. Please contact the GharSe team.")
        return
    _flash("ask_helper_flash")
    tab_open, tab_mine = st.tabs(["Open asks", "My quotes"])
    with tab_open:
        _open_asks(p)
    with tab_mine:
        _my_quotes(p)


def _open_asks(p):
    groups = ["All groups"] + list(ASK_GROUPS)
    c = st.columns([2, 1])
    pick = c[0].selectbox("Show", groups, key="askh_group")
    mine_only = c[1].checkbox(f"Only {p.get('area') or 'my area'}", key="askh_area")

    rows = q(
        "SELECT r.*, c.full_name AS asker, "
        "(SELECT COUNT(*) FROM bids b WHERE b.request_id=r.id AND b.status<>'Withdrawn') AS quotes "
        "FROM requests r JOIN customers c ON c.id=r.customer_id WHERE r.status=? "
        "ORDER BY CASE WHEN r.area=? THEN 0 ELSE 1 END, r.needed_by, r.id DESC",
        (OPEN, p.get("area") or ""),
    )
    reqs = [dict(r) for r in rows]
    if pick != "All groups":
        reqs = [r for r in reqs if group_of(r) == pick]
    if mine_only:
        reqs = [r for r in reqs if r.get("area") == p.get("area")]
    if not reqs:
        st.info("No open asks here right now. New ones appear the moment a neighbour posts.")
        return

    verified = bool(p.get("verified"))
    shown_other_area = False
    for req in reqs:
        if req.get("area") != p.get("area") and not shown_other_area and p.get("area"):
            shown_other_area = True
            st.markdown("<span class='muted'>Further away</span>", unsafe_allow_html=True)
        mine = q("SELECT * FROM bids WHERE request_id=? AND provider_id=?", (req["id"], p["id"]))
        bid = dict(mine[0]) if mine else None
        with st.container(border=True):
            c = st.columns([4, 1])
            c[0].markdown(_ask_body(req), unsafe_allow_html=True)
            n = req.get("quotes") or 0
            c[1].markdown(f"<span class='muted'>{n} quote{'s' if n != 1 else ''} so far</span>",
                          unsafe_allow_html=True)
            if is_care(req) and not verified:
                st.markdown(
                    "<span class='pill warn'>Verification needed</span> <span class='muted'>Care asks mean "
                    "going into someone's home to look after a child, an elder or a pet, so only helpers the "
                    "GharSe team has verified can quote. Finish verification under your profile and this "
                    "opens up.</span>", unsafe_allow_html=True)
                continue
            _quote_form(p, req, bid)


def _quote_form(p, req, bid):
    no_price = req.get("consideration") in _NO_PRICE
    live = bid and bid["status"] == "Offered"
    if live:
        st.markdown(f"<span class='pill ok'>Your quote: {h(_quote_price(bid))}</span>"
                    f"<span class='muted'>{h(bid.get('note') or '')}</span>", unsafe_allow_html=True)
    label = "Update quote" if live else "Send quote"
    with st.expander("Edit your quote" if live else "Quote on this", expanded=False):
        with st.form(f"askq{req['id']}"):
            c = st.columns([1, 1, 2])
            price = 0.0
            if no_price:
                c[0].markdown(f"<span class='muted'>{h(req.get('consideration'))} — no price needed.</span>",
                              unsafe_allow_html=True)
            else:
                default = float((bid or {}).get("price") or req.get("budget") or 0)
                price = c[0].number_input("Your price (₹)", 0.0, 500000.0, default, step=25.0,
                                          key=f"askqp{req['id']}")
            eta = c[1].text_input("When you can do it", (bid or {}).get("eta") or "", key=f"askqe{req['id']}",
                                  placeholder="e.g. Sat morning")
            note_label = ("What you'd like in return, or a note to them" if req.get("consideration") == EXCHANGE
                          else "A note to them" + (" (required)" if no_price else ""))
            note = c[2].text_input(note_label, (bid or {}).get("note") or "", key=f"askqn{req['id']}",
                                   placeholder="How you'd do it, what you've done before")
            if st.form_submit_button(label, key=f"askqs{req['id']}"):
                if not no_price and price <= 0:
                    st.error("Enter your price.")
                    return
                if no_price and not note.strip():
                    st.error("Write a short note so they know what you're offering.")
                    return
                fresh = q("SELECT status FROM requests WHERE id=?", (req["id"],))
                if not fresh or fresh[0]["status"] != OPEN:
                    st.error("This ask has just closed.")
                    return
                value = None if no_price else price
                if bid:
                    execute("UPDATE bids SET price=?, eta=?, note=?, status='Offered', created_at=? WHERE id=?",
                            (value, eta.strip(), note.strip(), now_iso(), bid["id"]))
                else:
                    execute("INSERT INTO bids (request_id, provider_id, price, eta, note, status, created_at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (req["id"], p["id"], value, eta.strip(), note.strip(), "Offered", now_iso()))
                offer = inr(price) if value else (req.get("consideration") or "help")
                notify_customer(req["customer_id"], "ask_quote",
                                f"{p.get('display_name') or 'A helper'} has {'updated her' if live else 'sent a'} "
                                f"quote ({offer}) for your ask \"{req.get('title')}\". "
                                "Open GharSe to compare quotes and choose.")
                st.session_state["ask_helper_flash"] = "Quote updated." if live else "Quote sent. You'll hear if they choose you."
                st.rerun()
    if live and st.button("Withdraw quote", key=f"askqw{req['id']}"):
        execute("UPDATE bids SET status='Withdrawn' WHERE id=? AND status='Offered'", (bid["id"],))
        st.session_state["ask_helper_flash"] = "Quote withdrawn."
        st.rerun()


def _my_quotes(p):
    rows = q(
        "SELECT b.id AS bid_id, b.price, b.eta, b.note, b.status AS bid_status, r.*, "
        "c.full_name AS asker, c.phone AS asker_phone "
        "FROM bids b JOIN requests r ON r.id=b.request_id JOIN customers c ON c.id=r.customer_id "
        "WHERE b.provider_id=? AND b.status<>'Withdrawn' ORDER BY b.id DESC",
        (p["id"],),
    )
    if not rows:
        st.caption("You haven't quoted on anything yet. Quotes you send, and the asks you take up, show here.")
        return
    for r in rows:
        d = dict(r)
        won = d["bid_status"] == "Accepted"
        with st.container(border=True):
            c = st.columns([4, 1])
            c[0].markdown(_ask_body(d), unsafe_allow_html=True)
            pill = ("<span class='pill ok'>You're taking this up</span>" if won else
                    f"<span class='pill'>{h(d['bid_status'])}</span>")
            c[1].markdown(f"{pill}<br>{_status_pill(d['status'])}", unsafe_allow_html=True)
            st.markdown(f"<span class='muted'>Your quote: {h(_quote_price(d))}"
                        f"{(' · ' + h(d['note'])) if d.get('note') else ''}</span>", unsafe_allow_html=True)
            if won and d["status"] in (ASSIGNED, COMPLETED):
                phone = (d.get("asker_phone") or "").strip()
                st.markdown(
                    f"Reach <b>{h(d.get('asker') or 'them')}</b> on <b>{h(phone or 'no number on file')}</b>.",
                    unsafe_allow_html=True)
                mine = (p.get("phone") or "").strip()
                st.caption(f"Your number {mine} has been shared with them." if mine else
                           "Add your phone number to your profile so they can reach you.")
                link = wa_link(phone, f"Hi, I'm {p.get('display_name')} from GharSe, about your ask "
                                      f"\"{d.get('title')}\".") if phone else None
                if link and d["status"] == ASSIGNED:
                    st.link_button("WhatsApp them", link, key=f"askhwa{d['bid_id']}")


# ----------------------------------------------------------------------------- admin
def ask_admin():
    st.markdown(
        f"<div class='page-title'>{ASK_PAGE_TITLES['admin']}</div>"
        "<div class='page-sub'>What neighbours are asking each other for, and whether it's getting done. "
        "Close anything that breaks the rules — the asker is told why.</div>",
        unsafe_allow_html=True,
    )
    _flash("ask_admin_flash")
    rows = [dict(r) for r in q(
        "SELECT r.*, c.full_name AS asker, "
        "(SELECT COUNT(*) FROM bids b WHERE b.request_id=r.id AND b.status<>'Withdrawn') AS quotes "
        "FROM requests r LEFT JOIN customers c ON c.id=r.customer_id ORDER BY r.id")]
    count = lambda s: sum(1 for r in rows if (r.get("status") or OPEN) == s)  # noqa: E731
    c = st.columns(4)
    kpi(c[0], "Asks posted", f"{len(rows)}")
    kpi(c[1], "Open", f"{count(OPEN)}")
    kpi(c[2], "Taken up", f"{count(ASSIGNED)}")
    kpi(c[3], "Completed", f"{count(COMPLETED)}")
    st.write("")
    if not rows:
        st.info("No asks yet. They appear here the moment a customer posts one.")
        return

    st.markdown("#### By group and status")
    grid = {}
    for r in rows:
        g = group_of(r)
        grid.setdefault(g, {s: 0 for s in _STATUS_ORDER})
        s = r.get("status") or OPEN
        grid[g][s] = grid[g].get(s, 0) + 1
    order = list(ASK_GROUPS) + sorted(g for g in grid if g not in ASK_GROUPS)
    summary = []
    for g in order:
        if g in grid:
            line = {"group": g}
            line.update({_STATUS_LABEL[s]: grid[g].get(s, 0) for s in _STATUS_ORDER})
            line["total"] = sum(grid[g].values())
            summary.append(line)
    table(summary, ["group"] + [_STATUS_LABEL[s] for s in _STATUS_ORDER] + ["total"])

    st.markdown("#### Open asks, oldest first")
    open_rows = [r for r in rows if (r.get("status") or OPEN) == OPEN]
    if not open_rows:
        st.caption("Nothing open right now.")
    for req in open_rows:
        with st.container(border=True):
            c = st.columns([4, 1])
            c[0].markdown(_ask_body(req), unsafe_allow_html=True)
            n = req.get("quotes") or 0
            safety = ("<br><span class='pill ok'>Safety note acknowledged</span>" if req.get("safety_ack")
                      else ("<br><span class='pill warn'>No safety acknowledgement</span>" if is_care(req) else ""))
            c[1].markdown(f"<span class='muted'>#{h(req['id'])} · {n} quote{'s' if n != 1 else ''}</span>{safety}",
                          unsafe_allow_html=True)
            with st.form(f"askclose{req['id']}"):
                fc = st.columns([3, 1])
                reason = fc[0].text_input("Reason (sent to the asker)", key=f"askcr{req['id']}",
                                          placeholder="e.g. Asks for medical care we can't arrange safely")
                if fc[1].form_submit_button("Close ask", key=f"askcb{req['id']}"):
                    if not reason.strip():
                        st.error("Write the reason — the asker deserves to know why.")
                    else:
                        execute("UPDATE requests SET status=? WHERE id=?", (CLOSED, req["id"]))
                        execute("UPDATE bids SET status='Declined' WHERE request_id=? AND status='Offered'",
                                (req["id"],))
                        notify_customer(req["customer_id"], "ask_closed",
                                        f"The GharSe team closed your ask \"{req.get('title')}\": {reason.strip()}. "
                                        "Reply to this message if you think it was a mistake.")
                        st.session_state["ask_admin_flash"] = f"Closed ask #{req['id']}. The asker has been told why."
                        st.rerun()

    st.markdown("#### Recently settled")
    done = [r for r in reversed(rows) if (r.get("status") or OPEN) != OPEN][:50]
    table([{"id": r["id"], "group": label_of(r), "ask": r.get("title"), "area": r.get("area"),
            "asker": r.get("asker"), "quotes": r.get("quotes") or 0,
            "status": _STATUS_LABEL.get(r.get("status"), r.get("status")), "posted_at": r.get("created_at")}
           for r in done], empty="Nothing settled yet.")
