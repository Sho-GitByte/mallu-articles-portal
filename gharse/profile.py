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
# Tables: users (read/write), providers and customers (read; blank name/phone filled).

import re
from datetime import datetime

import streamlit as st

from store import execute, hash_pw, q, verify_pw
from ui import MAX_IMG_MB, customer_of, h, process_image, provider_of

PROFILE_PAGE_TITLE = "My Profile"


# This file's name is also the name of a standard-library module, and Streamlit puts
# the app folder first on sys.path, so `import cProfile` (which does `import profile`)
# would land here. Re-export the three names cProfile borrows from the real one so a
# profiler or a library that imports cProfile keeps working.
def _stdlib_profile():
    import importlib.util
    import os
    import sysconfig
    path = os.path.join(sysconfig.get_paths()["stdlib"], "profile.py")
    spec = importlib.util.spec_from_file_location("_stdlib_profile", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _std = _stdlib_profile()
    run, runctx, Profile = _std.run, _std.runctx, _std.Profile
except Exception:  # pragma: no cover - a trimmed Python without the stdlib source
    pass

ROLE_LABEL = {"provider": "Helper", "customer": "Customer", "admin": "Admin"}
ROLE_LINE = {
    "provider": "You cook, stitch or care for neighbours from your own home.",
    "customer": "You buy home-made and book help from women in your neighbourhood.",
    "admin": "You keep GharSe running for everyone in it.",
}

# Where the role-specific record is edited, so this page can point at it.
PROVIDER_PAGE = "My Profile & Verification"
CUSTOMER_PAGE = "My Preferences"

BIO_MAX = 300
MIN_PASSWORD = 8

_FLASH = "profile_flash"
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
    """The users row as it is now — the session copy can be a sign-in old."""
    rows = q("SELECT * FROM users WHERE id=?", (user["id"],))
    return dict(rows[0]) if rows else dict(user)


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


def fssai_summary(p):
    """(label, pill-class) for a helper's FSSAI position, from whatever is on file."""
    status = (p.get("fssai_status") or "").strip()
    number = (p.get("fssai_no") or "").strip()
    arn = (p.get("fssai_arn") or "").strip()
    if status:
        low = status.lower()
        cls = "ok" if any(w in low for w in ("verified", "approved", "active", "valid", "issued")) else "warn"
        if any(w in low for w in ("expired", "rejected", "not")):
            cls = "warn"
        return status, cls
    if number:
        return "Number on file", "ok"
    if arn:
        return "Applied — waiting for the certificate", "warn"
    return "Not added yet", "warn"


# --------------------------------------------------------------------------- sections
def _header(me, linked):
    name = display_name(me, linked)
    role = me.get("role") or "customer"
    area = linked.get("area") if role in ("provider", "customer") else None
    since = _member_since(me.get("created_at"))

    with st.container(border=True):
        c1, c2 = st.columns([1, 4], vertical_alignment="center")
        with c1:
            if me.get("photo"):
                st.image(bytes(me["photo"]), width=110)
            else:
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
            label, cls = fssai_summary(linked)
            kitchen = (linked.get("display_name") or "").strip()
            st.markdown("**Your kitchen**")
            st.markdown(
                (f"{h(kitchen)}" if kitchen else "<span class='muted'>No kitchen name yet</span>")
                + f" &nbsp; <span class='muted'>FSSAI:</span> <span class='pill {cls}'>{h(label)}</span>",
                unsafe_allow_html=True,
            )
            extra = []
            if (linked.get("fssai_no") or "").strip():
                extra.append(f"Registration no. {h(linked['fssai_no'].strip())}")
            if (linked.get("fssai_expiry") or "").strip():
                extra.append(f"valid till {h(linked['fssai_expiry'].strip())}")
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


def _mirror_into_linked(me, linked, name, phone):
    """Fill the kitchen / preferences record's blank name and phone, never overwrite."""
    if not linked or not linked.get("id"):
        return
    if me["role"] == "provider":
        if name and not (linked.get("owner_name") or "").strip():
            execute("UPDATE providers SET owner_name=? WHERE id=?", (name, linked["id"]))
        if phone and not (linked.get("phone") or "").strip():
            execute("UPDATE providers SET phone=? WHERE id=?", (phone, linked["id"]))
    elif me["role"] == "customer":
        if name and not (linked.get("full_name") or "").strip():
            execute("UPDATE customers SET full_name=? WHERE id=?", (name, linked["id"]))
        if phone and not (linked.get("phone") or "").strip():
            execute("UPDATE customers SET phone=? WHERE id=?", (phone, linked["id"]))


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
    if shot is not None and shot.size > MAX_IMG_MB * 1024 * 1024:
        errors.append(f"That photo is too large. Please keep it under {MAX_IMG_MB} MB.")
    if len(name) > 80:
        errors.append("Please keep your name under 80 characters.")
    if errors:
        for e in errors:
            st.error(e)
        return

    em = (email or "").strip()
    about = (bio or "").strip()[:BIO_MAX]
    execute("UPDATE users SET full_name=?, phone=?, email=?, bio=? WHERE id=?",
            (name or None, norm or None, em or None, about or None, me["id"]))
    fields = dict(full_name=name or None, phone=norm or None, email=em or None, bio=about or None)
    new_photo = process_image(shot) if shot is not None else None
    if new_photo:
        execute("UPDATE users SET photo=? WHERE id=?", (new_photo, me["id"]))
        fields["photo"] = new_photo
    elif remove:
        execute("UPDATE users SET photo=NULL WHERE id=?", (me["id"],))
        fields["photo"] = None
    _mirror_into_linked(me, linked, name, norm)
    _update_session(**fields)
    st.session_state[_FLASH] = "Your details are saved."
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
        st.success("Theme changed")
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
    rows = q("SELECT pw_hash, username FROM users WHERE id=?", (me["id"],))
    if not rows or not verify_pw(current or "", rows[0]["pw_hash"]):
        st.error("Your current password is wrong, so nothing was changed. Try again — if you've forgotten "
                 "it, ask the GharSe team to reset it.")
        return
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
