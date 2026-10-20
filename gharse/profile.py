# profile.py — "My Profile": the account page every signed-in person has, whatever
# their role.
#
#   Header  — photo (or a monogram), the name people know you by, your role in your own
#             terms, your area and how long you've been here.
#   Details — full name, mobile, email, a short bio and a photo, saved to the users row.
#             For a helper or a customer the name and phone are also copied into their
#             kitchen / preferences record when those are still blank, so WhatsApp
#             updates about orders actually reach them.
#   Theme   — 20 app themes from themes.py; the pick is saved to users.theme.
#   Password — change it, after proving you know the current one.
#
# This page never shows anyone else's details; it is only ever about the person
# looking at it. Kitchen, FSSAI and food preferences are edited on their own pages,
# and this one says which.
#
# Tables: users (read/write), providers and customers (read; a blank name is filled, and
# the phone follows users.phone when it was blank or still the old users.phone).
#
# FSSAI: this page never decides who may sell food. It asks fssai.can_sell_food() and
# fssai.food_block_reason() (imported lazily), so it can never disagree with the gate.

import re
from datetime import date, datetime

import streamlit as st

from store import execute, hash_pw, q, today_ist, verify_pw
from ui import MAX_IMG_MB, customer_of, h, process_image, provider_of, safe_image

PROFILE_PAGE_TITLE = "My Profile"


# This file's name is also the name of a standard-library module, and Streamlit puts
# the app folder first on sys.path, so `import cProfile` (which does `import profile`
# and then uses profile.run, .runctx, ._Utils, ...) or any profiling tool would land
# here. Every name this page does not define is forwarded, on first use, to the real
# standard-library module (PEP 562 module __getattr__). The stdlib file is loaded only
# when something actually asks for one of its names, and only once.
# (Renaming this file would be cleaner, but app.py imports it as `profile`.)
_STD_CACHE = []


def _stdlib_profile():
    if not _STD_CACHE:
        import importlib.util
        import os
        import sysconfig
        path = os.path.join(sysconfig.get_paths()["stdlib"], "profile.py")
        spec = importlib.util.spec_from_file_location("_stdlib_profile", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _STD_CACHE.append(mod)
    return _STD_CACHE[0]


def __getattr__(name):
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module 'profile' has no attribute {name!r}")
    try:
        std = _stdlib_profile()
    except Exception:  # pragma: no cover - a trimmed Python without the stdlib source
        raise AttributeError(f"module 'profile' has no attribute {name!r}") from None
    try:
        return getattr(std, name)
    except AttributeError:
        raise AttributeError(f"module 'profile' has no attribute {name!r}") from None

ROLE_LABEL = {"provider": "Helper", "customer": "Customer", "admin": "Admin"}
ROLE_LINE = {
    "provider": "You cook, stitch or care for neighbours from your own home.",
    "customer": "You buy home-made and book help from women in your neighbourhood.",
    "admin": "You keep GharSe running for everyone in it.",
}

# Where the role-specific record is edited, so this page can point at it.
PROVIDER_PAGE = "My Kitchen"
CUSTOMER_PAGE = "My Preferences"

BIO_MAX = 300
MIN_PASSWORD = 8
MAX_PW_TRIES = 5          # wrong current-password attempts per session before the form locks

# fssai.py's providers.fssai_status codes, in plain words. Anything else is treated the
# way fssai.py treats it: as not registered.
FSSAI_STATUS_WORDS = {
    "none": "Not registered yet",
    "applied": "Applied, waiting for the certificate",
    "registered": "Registered",
}

_FLASH = "profile_flash"
_FLASH_WARN = "profile_flash_warn"
_PW_TRIES = "profile_pw_tries"
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")

# A tiny scoped rule for the monogram circle. Colours come from the theme's own
# variables, so every one of the 20 themes paints it correctly.
_CSS = """
<style>
.gs-mono { width:96px; height:96px; border-radius:50%; display:flex; align-items:center;
  justify-content:center; font-size:2.1rem; font-weight:800; letter-spacing:.02em;
  background:var(--btn-bg); color:var(--btn-txt); box-shadow:0 6px 18px var(--shadow); }
.gs-mono span { color:var(--btn-txt); }
.gs-prof-name { font-size:1.45rem; font-weight:800; line-height:1.2; margin-bottom:.25rem; }
.gs-prof-line { margin-top:.35rem; }
</style>
"""


# --------------------------------------------------------------------------- helpers
def normalise_phone(raw):
    """A 10-digit Indian mobile number from whatever was typed, or None if it isn't one.
    Accepts spaces, dashes, a leading 0, +91 or 91. Empty input gives ''."""
    s = (raw or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if s.startswith("+") and not s.startswith("+91"):
        return None
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return None


def valid_email(raw):
    s = (raw or "").strip()
    return s == "" or (bool(_EMAIL_RE.match(s)) and ".." not in s)


def password_problem(new, confirm, username, current=None):
    """The first thing wrong with a proposed new password, or None if it is fine."""
    if len(new or "") < MIN_PASSWORD:
        return f"Your new password needs at least {MIN_PASSWORD} characters."
    if len((new or "").strip()) < MIN_PASSWORD:
        return (f"Your new password needs at least {MIN_PASSWORD} characters that aren't spaces — "
                "spaces at the start or end don't count.")
    if (new or "").strip().lower() == (username or "").strip().lower():
        return "Your new password can't be the same as your username."
    if new != confirm:
        return "The two new passwords don't match. Type the same one in both boxes."
    if current is not None and new == current:
        return "That's the password you already have. Pick a new one."
    return None


def _initials(name):
    words = [w for w in re.split(r"[\s._-]+", name or "") if w and w[0].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def _member_since(created_at):
    try:
        return datetime.fromisoformat(str(created_at)).strftime("%B %Y")
    except (TypeError, ValueError):
        return None


def _fresh_user(user):
    """The users row as it is now — the session copy can be a sign-in old. None when the
    account no longer exists (removed while this session was open)."""
    if not user or user.get("id") is None:
        return None
    rows = q("SELECT * FROM users WHERE id=?", (user["id"],))
    return dict(rows[0]) if rows else None


def _linked(me):
    if not me.get("linked_id"):
        return {}
    if me["role"] == "provider":
        return provider_of(me)
    if me["role"] == "customer":
        return customer_of(me)
    return {}


def display_name(me, linked):
    """users.full_name, else the linked record's name, else the username."""
    if (me.get("full_name") or "").strip():
        return me["full_name"].strip()
    if me.get("role") == "provider":
        for k in ("owner_name", "display_name"):
            if (linked.get(k) or "").strip():
                return linked[k].strip()
    if me.get("role") == "customer" and (linked.get("full_name") or "").strip():
        return linked["full_name"].strip()
    return me.get("username") or "Friend"


def _name_prefill(me, linked):
    """The person's own name — never the kitchen's — for the Full name box."""
    if (me.get("full_name") or "").strip():
        return me["full_name"].strip()
    key = {"provider": "owner_name", "customer": "full_name"}.get(me.get("role"))
    return (linked.get(key) or "").strip() if key else ""


def _update_session(**fields):
    u = dict(st.session_state.get("user") or {})
    u.update(fields)
    st.session_state["user"] = u


def _flash():
    msg = st.session_state.pop(_FLASH, None)
    if msg:
        st.success(msg)
    warn = st.session_state.pop(_FLASH_WARN, None)
    if warn:
        st.warning(warn)


def pretty_phone(raw):
    """98450 12345 for a 10-digit mobile; anything else as it was typed."""
    n = normalise_phone(raw)
    return f"{n[:5]} {n[5:]}" if n else (raw or "").strip()


def fssai_status_label(p):
    """providers.fssai_status ('none' / 'applied' / 'registered') in plain words."""
    code = (p or {}).get("fssai_status") or "none"
    return FSSAI_STATUS_WORDS.get(code, FSSAI_STATUS_WORDS["none"])


def _expiry_words(raw):
    """'valid until 05 Jan 2027' / 'expired on 01 Jan 2020', or None if no usable date."""
    try:
        exp = date.fromisoformat(str(raw or "").strip()[:10])
    except ValueError:
        return None
    if exp < today_ist():
        return f"expired on {exp:%d %b %Y}"
    return f"valid until {exp:%d %b %Y}"


def fssai_summary(p):
    """A helper's FSSAI position for this page, decided entirely by fssai.py's public API:

    {"status": plain-words status, "can_sell": True / False (None if fssai.py could not be
     asked), "reason": fssai.food_block_reason() when she can't sell, else None}."""
    out = {"status": fssai_status_label(p), "can_sell": None, "reason": None}
    try:
        import fssai
        can = bool(fssai.can_sell_food(p))
        out["can_sell"] = can
        if not can:
            out["reason"] = fssai.food_block_reason(p)
    except (ImportError, AttributeError):
        pass
    return out


# --------------------------------------------------------------------------- sections
def _header(me, linked):
    name = display_name(me, linked)
    role = me.get("role") or "customer"
    area = linked.get("area") if role in ("provider", "customer") else None
    since = _member_since(me.get("created_at"))

    with st.container(border=True):
        c1, c2 = st.columns([1, 4], vertical_alignment="center")
        with c1:
            # safe_image shows a placeholder instead of raising on bytes that aren't an
            # image, so an old bad photo can't lock anyone out of this page.
            if not (me.get("photo") and safe_image(me["photo"], width=110)):
                st.markdown(f"<div class='gs-mono'><span>{h(_initials(name))}</span></div>",
                            unsafe_allow_html=True)
        with c2:
            bits = [f"<span class='pill ok'>{h(ROLE_LABEL.get(role, role.title()))}</span>"]
            if role == "provider" and linked.get("verified"):
                bits.append("<span class='pill ok'>Verified</span>")
            elif role == "provider":
                bits.append("<span class='pill warn'>Verification pending</span>")
            where = []
            if area:
                where.append(f"📍 {h(area)}")
            elif role == "admin":
                where.append("GharSe team")
            if since:
                where.append(f"Member since {h(since)}")
            where.append(f"@{h(me.get('username'))}")
            st.markdown(
                f"<div class='gs-prof-name'>{h(name)}</div>"
                f"<div>{' '.join(bits)}</div>"
                f"<div class='gs-prof-line muted'>{' · '.join(where)}</div>",
                unsafe_allow_html=True,
            )
            if (me.get("bio") or "").strip():
                st.markdown(f"<div class='gs-prof-line'>{h(me['bio'].strip())}</div>",
                            unsafe_allow_html=True)
            else:
                st.markdown(f"<div class='gs-prof-line muted'>{h(ROLE_LINE.get(role, ''))}</div>",
                            unsafe_allow_html=True)


def _linked_summary(me, linked):
    role = me.get("role")
    if role not in ("provider", "customer"):
        return
    with st.container(border=True):
        if role == "provider":
            fs = fssai_summary(linked)
            kitchen = (linked.get("display_name") or "").strip()
            status_cls = "ok" if fs["can_sell"] else "warn"
            st.markdown("**Your kitchen**")
            st.markdown(
                (f"{h(kitchen)}" if kitchen else "<span class='muted'>No kitchen name yet</span>")
                + f" &nbsp; <span class='muted'>FSSAI:</span> "
                  f"<span class='pill {status_cls}'>{h(fs['status'])}</span>",
                unsafe_allow_html=True,
            )
            if fs["can_sell"]:
                st.markdown("<span class='pill ok'>Can sell food</span>", unsafe_allow_html=True)
            elif fs["can_sell"] is False:
                st.markdown("<span class='pill warn'>Can't sell food yet</span> "
                            f"<span class='muted'>{h(fs['reason'] or '')}</span>",
                            unsafe_allow_html=True)
            else:
                st.markdown("<span class='muted'>Whether you can sell food couldn't be checked "
                            f"just now — open {h(PROVIDER_PAGE)} to see it.</span>",
                            unsafe_allow_html=True)
            extra = []
            if (linked.get("fssai_no") or "").strip():
                extra.append(f"Registration no. {h(linked['fssai_no'].strip())}")
            exp = _expiry_words(linked.get("fssai_expiry"))
            if exp:
                extra.append(h(exp))
            if extra:
                st.markdown(f"<span class='muted'>{' · '.join(extra)}</span>", unsafe_allow_html=True)
            st.caption(f"Your kitchen name, FSSAI certificate, UPI ID and verification live on "
                       f"**{PROVIDER_PAGE}** — edit them there.")
        else:
            diet = (linked.get("diet") or "").strip()
            pickup = (linked.get("pickup_point") or "").strip()
            st.markdown("**Your food preferences**")
            st.markdown(
                "<span class='muted'>Diet:</span> "
                + (f"<span class='pill'>{h(diet)}</span>" if diet else "<span class='muted'>not set</span>")
                + " &nbsp; <span class='muted'>Pickup point:</span> "
                + (h(pickup) if pickup else "<span class='muted'>not set</span>"),
                unsafe_allow_html=True,
            )
            st.caption(f"Diet, spice level, allergies and your pickup point live on **{CUSTOMER_PAGE}** "
                       f"— every kitchen you order from sees them.")
        other = _other_phone(me, linked)
        if other:
            st.warning(_other_phone_msg(me, other))


def _other_phone(me, linked):
    """The linked record's phone when it is set and differs from users.phone — that is
    the number order and request WhatsApps actually go to. Else None."""
    mine = normalise_phone(me.get("phone"))
    theirs = (linked.get("phone") or "").strip()
    if not mine or not theirs or normalise_phone(theirs) == mine:
        return None
    return theirs


def _other_phone_msg(me, other):
    page = PROVIDER_PAGE if me.get("role") == "provider" else CUSTOMER_PAGE
    # st.warning renders Markdown, so the stored numbers go through h() like any DB text.
    return (f"Order and request messages still go to {h(pretty_phone(other))}, the number on "
            f"{page} — not {h(pretty_phone(me.get('phone')))}. Change it on {page} if that's old.")


_LINKED_TABLE = {"provider": ("providers", "owner_name"), "customer": ("customers", "full_name")}


def _mirror_into_linked(me, linked, name, phone):
    """Keep the kitchen / preferences record in step with the details just saved.

    The name is filled only when blank. The phone (what notify_provider / notify_customer
    send WhatsApps to) is set when it was blank or still equal to the old users.phone, so
    changing your number here moves your messages too; a different number someone typed
    on the kitchen / preferences page is never overwritten. Returns that other number
    when it is left in place, so the caller can say where messages still go."""
    if not linked or not linked.get("id") or me.get("role") not in _LINKED_TABLE:
        return None
    table, name_col = _LINKED_TABLE[me["role"]]
    if name and not (linked.get(name_col) or "").strip():
        execute(f"UPDATE {table} SET {name_col}=? WHERE id=?", (name, linked["id"]))
    if not phone:
        return None
    current = (linked.get("phone") or "").strip()
    cur_norm = normalise_phone(current)
    if cur_norm == phone:
        return None
    old = normalise_phone(me.get("phone"))
    if not current or (old and cur_norm == old):
        execute(f"UPDATE {table} SET phone=? WHERE id=?", (phone, linked["id"]))
        return None
    return current


def _details_form(me, linked):
    role = me.get("role")
    st.markdown("<span class='muted'>Only you and the GharSe team see your phone and email. A neighbour sees "
                "them only after you accept their order, request or application.</span>",
                unsafe_allow_html=True)
    with st.form("profile_details"):
        c = st.columns(2)
        full_name = c[0].text_input("Full name", _name_prefill(me, linked), placeholder="As neighbours know you")
        phone = c[1].text_input("Mobile number", me.get("phone") or "", placeholder="10-digit mobile, e.g. 98450 12345")
        email = st.text_input("Email (optional)", me.get("email") or "", placeholder="you@example.com")
        bio = st.text_area("A line about you", me.get("bio") or "", max_chars=BIO_MAX, height=90,
                           placeholder={"provider": "Cooking Kerala meals for 20 years; I also stitch blouses.",
                                        "customer": "Working in HSR, missing home food.",
                                        }.get(role, "What you look after at GharSe."))
        c = st.columns([3, 1])
        shot = c[0].file_uploader(f"Profile photo (JPG or PNG, under {MAX_IMG_MB} MB)",
                                  type=["jpg", "jpeg", "png", "webp"], key="profile_photo")
        remove = c[1].checkbox("Remove my photo", value=False, disabled=not me.get("photo"))
        submitted = st.form_submit_button("Save my details", use_container_width=True)

    if not submitted:
        return
    name = (full_name or "").strip()
    norm = normalise_phone(phone)
    errors = []
    if norm is None:
        errors.append("That mobile number doesn't look right. Enter a 10-digit Indian mobile number "
                      "starting with 6, 7, 8 or 9.")
    if not valid_email(email):
        errors.append("That email address doesn't look right — it should be like name@example.com.")
    new_photo = None
    if shot is not None and shot.size > MAX_IMG_MB * 1024 * 1024:
        errors.append(f"That photo is too large. Please keep it under {MAX_IMG_MB} MB.")
    elif shot is not None:
        new_photo = process_image(shot)    # None for anything that isn't a readable image
        if not new_photo:
            errors.append("That file isn't a photo we can read. Please choose a JPG or PNG picture "
                          "taken with your phone's camera. Nothing was saved.")
    if len(name) > 80:
        errors.append("Please keep your name under 80 characters.")
    if errors:
        for e in errors:
            st.error(e)
        return

    em = (email or "").strip()
    about = (bio or "").strip()[:BIO_MAX]
    hit = execute("UPDATE users SET full_name=?, phone=?, email=?, bio=? WHERE id=? RETURNING id",
                  (name or None, norm or None, em or None, about or None, me["id"]))
    if hit is None:
        st.error("Your account couldn't be found any more, so nothing was saved. "
                 "Please sign out and sign in again.")
        return
    fields = dict(full_name=name or None, phone=norm or None, email=em or None, bio=about or None)
    if new_photo:
        execute("UPDATE users SET photo=? WHERE id=?", (new_photo, me["id"]))
        fields["photo"] = new_photo
    elif remove:
        execute("UPDATE users SET photo=NULL WHERE id=?", (me["id"],))
        fields["photo"] = None
    other = _mirror_into_linked(me, linked, name, norm)
    _update_session(**fields)
    st.session_state[_FLASH] = "Your details are saved."
    if other:
        st.session_state[_FLASH_WARN] = _other_phone_msg({**me, "phone": norm}, other)
    st.rerun()


def _theme_section(me):
    import themes
    current = me.get("theme") or themes.DEFAULT_THEME
    st.markdown(f"<span class='muted'>Now using</span> <b>{h(themes.get(current)['name'])}</b>. "
                "<span class='muted'>Pick any card below — it changes only how GharSe looks for you.</span>",
                unsafe_allow_html=True)
    chosen = themes.theme_picker(current, key_prefix="profile_theme")
    if chosen:
        execute("UPDATE users SET theme=? WHERE id=?", (chosen, me["id"]))
        _update_session(theme=chosen)
        st.session_state[_FLASH] = f"Theme changed to {themes.get(chosen)['name']}."
        st.rerun()


def _password_form(me):
    st.markdown("<span class='muted'>Use at least 8 characters. A short sentence you'll remember works well — "
                "and don't reuse your UPI or bank PIN.</span>", unsafe_allow_html=True)
    with st.form("profile_password", clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("New password again", type="password")
        submitted = st.form_submit_button("Change password", use_container_width=True)
    if not submitted:
        return
    tries = dict(st.session_state.get(_PW_TRIES) or {})
    if tries.get(me["id"], 0) >= MAX_PW_TRIES:
        st.error("Too many wrong current passwords, so this form is locked for now. "
                 "If you've forgotten it, ask the GharSe team to reset it.")
        return
    rows = q("SELECT pw_hash, username FROM users WHERE id=?", (me["id"],))
    if not rows or not verify_pw(current or "", rows[0]["pw_hash"]):
        tries[me["id"]] = tries.get(me["id"], 0) + 1
        st.session_state[_PW_TRIES] = tries
        st.error("Your current password is wrong, so nothing was changed. Try again — if you've forgotten "
                 "it, ask the GharSe team to reset it.")
        return
    tries.pop(me["id"], None)
    st.session_state[_PW_TRIES] = tries
    problem = password_problem(new, confirm, rows[0]["username"], current=current)
    if problem:
        st.error(problem)
        return
    new_hash = hash_pw(new)
    execute("UPDATE users SET pw_hash=? WHERE id=?", (new_hash, me["id"]))
    _update_session(pw_hash=new_hash)
    st.session_state[_FLASH] = "Password changed. Use the new one next time you sign in."
    st.rerun()


# --------------------------------------------------------------------------- page
def profile_page(user):
    """My Profile — the same page for admin, helper and customer."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(f"<div class='page-title'>{PROFILE_PAGE_TITLE}</div>"
                "<div class='page-sub'>How neighbours see you, how to reach you, and how GharSe looks "
                "on your phone.</div>", unsafe_allow_html=True)
    _flash()

    me = _fresh_user(user)
    if me is None:
        st.error("This account couldn't be found any more — it may have been removed. "
                 "Please sign out and sign in again, or ask the GharSe team.")
        return
    linked = _linked(me)

    _header(me, linked)
    _linked_summary(me, linked)

    t_details, t_theme, t_pw = st.tabs(["Your details", "App theme", "Password"])
    with t_details:
        _details_form(me, linked)
    with t_theme:
        _theme_section(me)
    with t_pw:
        _password_form(me)
