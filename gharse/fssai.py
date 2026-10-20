# fssai.py — everything FSSAI on GharSe.
#
# The rule, from the founder: a home kitchen that sells food here must hold an FSSAI
# registration or licence. This module owns that rule end to end:
#   fssai_guide(user)              the help page anyone can open — what FSSAI is, which
#                                  tier applies, documents, steps on FoSCoS, official links
#   can_sell_food(provider)        the single gate every food listing / meal plan checks
#   food_block_reason(provider)    one plain sentence on what is still missing
#   FOOD_OK_SQL, food_ok_params()  the same gate as SQL, for the customer browse query
#   KITCHEN_OK_SQL, kitchen_ok_params()  the kitchen half alone, for the meal-plan query
#   provider_fssai_section(user)   the helper's own status card and details form
#   admin_fssai_panel(provider, admin_user=None)
#                                  the admin's check: certificate, expiry, approve / send back
#   expiring_soon(days)            registered certificates that need renewing
#
# FSSAI approval lives in providers.fssai_verified (+ fssai_verified_at / _by). It is
# separate from providers.verified, which is GharSe's general helper verification and
# is never read or written here.
#
# About the facts on the guide page: see FACTS_CHECKED. Every FSSAI figure the page
# shows comes from the one FACTS dict below, so confirming them later means editing
# one place.

import hashlib
import io
import re
from datetime import date, timedelta

import streamlit as st

from store import execute, now_iso, q, today_ist
from ui import MAX_IMG_MB, h, notify, notify_provider, process_image, provider_of, safe_image, table

# ----------------------------------------------------------------------------- facts
FACTS_CHECKED = (
    "NOT re-checked. On 2026-10-20 the official FSSAI sites could not be reached from the build "
    "sandbox (network policy), so nothing in FACTS was verified against them that day. Every "
    "figure is shown to users as 'as last published: confirm on FoSCoS before you rely on it'. "
    "When someone confirms them on https://foscos.fssai.gov.in/, update FACTS and set "
    "FACTS_CHECKED_ON to that ISO date; the page then drops its 'please confirm' banner."
)
FACTS_CHECKED_ON = None   # ISO date the figures in FACTS were last confirmed on the official site

FACTS = {
    "links": {
        # Front pages only. Registration, licences, renewals and the FBO / licence-number
        # search all start on FoSCoS; deeper paths are deliberately not linked.
        "foscos": "https://foscos.fssai.gov.in/",
        "fostac": "https://fostac.fssai.gov.in/",
        "fssai": "https://www.fssai.gov.in/",
    },
    "foscos_name": "FoSCoS (Food Safety Compliance System)",
    "tiers": [
        {"Tier": "Basic registration",
         "Who it is for": "Petty food businesses: this is what nearly every home kitchen needs",
         "Yearly turnover": "Up to ₹12 lakh",
         "Form": "Form A",
         "Fee": "About ₹100 a year",
         "Validity": "Issued for 1 to 5 years"},
        {"Tier": "State licence",
         "Who it is for": "Food businesses above the basic limit",
         "Yearly turnover": "Above ₹12 lakh, up to ₹20 crore",
         "Form": "Form B",
         "Fee": "Roughly ₹2,000 to ₹5,000 a year, depending on the business",
         "Validity": "Confirm on FoSCoS"},
        {"Tier": "Central licence",
         "Who it is for": "Large businesses, and importers and some other categories",
         "Yearly turnover": "Above ₹20 crore",
         "Form": "Confirm on FoSCoS",
         "Fee": "Confirm on FoSCoS",
         "Validity": "Confirm on FoSCoS"},
    ],
    # Turnover limits used by the little "which one is mine?" helper, in rupees a year.
    "basic_max": 12_00_000,
    "state_max": 20_00_00_000,
    "basic_max_years": 5,
    "number_digits": 14,
    "documents_basic": [
        "A recent photograph of you",
        "Identity proof",
        "Proof of address / possession of the premises you cook in",
        "The list of food items you will make and sell",
    ],
    "documents_note": "FoSCoS shows the exact current list when you apply.",
    "steps_basic": [
        "Open **FoSCoS** (button above) on a phone or computer.",
        "Choose your state: **Karnataka**.",
        "Choose the **kind of business**. A petty food business with yearly turnover up to ₹12 lakh, "
        "like a home kitchen, comes under **Basic registration (Form A)**.",
        "Fill in **Form A**. Basic registration is issued for 1 to 5 years.",
        "**Upload** the documents (see the checklist).",
        "**Pay** the fee online.",
        "Write down the **application reference number** FoSCoS gives you. GharSe asks for it "
        "while you wait.",
        "When it is issued, **download the certificate** from FoSCoS, then add its number, expiry "
        "date and the certificate under **My Profile → FSSAI** on GharSe.",
    ],
    "renewal": "Renew before the expiry date, on the same portal, FoSCoS.",
}

FOSCOS_HOME = FACTS["links"]["foscos"]
FSSAI_HOME = FACTS["links"]["fssai"]
FOSTAC_HOME = FACTS["links"]["fostac"]

OFFICIAL_LINKS = [
    # (button label, url, what to do once it opens). Every button opens a site's front
    # page; the hint says what to look for there, without naming deeper pages.
    ("Open FoSCoS", FOSCOS_HOME,
     "FoSCoS, the Food Safety Compliance System, is FSSAI's portal for registrations, licences "
     "and renewals."),
    ("Log in", FOSCOS_HOME, "Sign in on FoSCoS with the account you applied with."),
    ("Start a new application", FOSCOS_HOME,
     "On FoSCoS, start a new registration and choose Karnataka as your state."),
    ("Verify an FSSAI number", FOSCOS_HOME,
     "Searching an FBO / licence number also starts on FoSCoS: enter the 14-digit number there."),
    ("Food safety training (FoSTaC)", FOSTAC_HOME, "FoSTaC is FSSAI's food safety training site."),
]

_CONFIRM = "as last published: confirm on FoSCoS before you rely on it"

TIER_LABELS = {"basic": "Basic registration", "state": "State licence", "central": "Central licence"}
STATUS_LABELS = {
    "none": "I don't have one yet",
    "applied": "I've applied and I'm waiting",
    "registered": "I have my certificate",
}

FSSAI_NO_RE = re.compile(r"^\d{%d}$" % FACTS["number_digits"])
ARN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-]{4,39}$")
_MAX_BYTES = MAX_IMG_MB * 1024 * 1024
_IMG_TYPES = ("jpg", "jpeg", "png", "webp")
_PDF_MIME = "application/pdf"

# ----------------------------------------------------------------------------- the gate, as SQL
# A portable boolean over listings alias l and providers alias p: "this listing is not
# food, OR its kitchen can sell food right now". AND it into the customer queries so a
# lapsed, sent-back or re-check-pending kitchen's food stops being orderable at once.
# ISO date text compares correctly as text on SQLite and Postgres.
KITCHEN_OK_SQL = ("(p.fssai_verified = 1 AND p.fssai_status = ? AND p.fssai_cert IS NOT NULL "
                  "AND p.fssai_expiry IS NOT NULL AND p.fssai_expiry >= ?)")
FOOD_OK_SQL = "(COALESCE(l.kind, '') <> ? OR " + KITCHEN_OK_SQL + ")"


def kitchen_ok_params():
    """Params for KITCHEN_OK_SQL, in order. Call it per query: it carries today's IST date."""
    return ["registered", today_ist().isoformat()]


def food_ok_params():
    """Params for FOOD_OK_SQL, in order. Call it per query: it carries today's IST date."""
    return ["Food"] + kitchen_ok_params()


# ----------------------------------------------------------------------------- small helpers
def normalize_fssai_no(raw):
    """People type the number with spaces or dashes, the way it is printed. Store digits."""
    return re.sub(r"[\s\-]", "", (raw or "").strip())


def _number_problem(number):
    """None if the (normalized) number is well formed, else a plain sentence."""
    if not FSSAI_NO_RE.match(number or ""):
        return "The FSSAI number should be exactly 14 digits (spaces are fine, letters are not)."
    if len(set(number)) == 1:
        return "That FSSAI number is the same digit repeated. Please copy it from your certificate."
    return None


def fssai_no_ok(raw):
    return _number_problem(normalize_fssai_no(raw)) is None


def _expiry(p):
    v = (p or {}).get("fssai_expiry")
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _flash(key):
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


def _is_pdf(data):
    return bool(data) and data[:5] == b"%PDF-" and b"%%EOF" in data


def _cert_kind(data):
    """'pdf', 'image' or None for bytes that are neither (checked, not trusted)."""
    if not data:
        return None
    data = bytes(data)
    if _is_pdf(data):
        return "pdf"
    try:
        from PIL import Image
        Image.open(io.BytesIO(data)).verify()
        return "image"
    except Exception:
        return None


def _sha1(data):
    return hashlib.sha1(bytes(data)).hexdigest() if data else ""


def _fingerprint(p):
    """What the admin is looking at: number, expiry, tier, status and the certificate."""
    parts = [normalize_fssai_no(p.get("fssai_no")), str(p.get("fssai_expiry") or ""),
             str(p.get("fssai_kind") or ""), str(p.get("fssai_status") or ""), _sha1(p.get("fssai_cert"))]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _sent_back(p):
    """A note from GharSe is waiting on an unapproved kitchen (set only by Send back)."""
    return bool(p.get("verify_note")) and not p.get("fssai_verified")


def _status_pill(p):
    status = (p or {}).get("fssai_status") or "none"
    exp = _expiry(p)
    if status == "registered" and exp and exp < today_ist():
        return "<span class='pill warn'>Certificate expired</span>"
    if can_sell_food(p):
        return "<span class='pill ok'>FSSAI approved ✓</span>"
    if status == "registered":
        if food_block_reason({**p, "fssai_verified": 1}) is None:
            if _sent_back(p):
                return "<span class='pill warn'>Sent back · needs a fix</span>"
            return "<span class='pill warn'>Registered · waiting for GharSe check</span>"
        return "<span class='pill warn'>Registered · details missing</span>"
    if status == "applied":
        return "<span class='pill warn'>Applied · waiting</span>"
    return "<span class='pill'>No FSSAI yet</span>"


def _expiry_line(p):
    exp = _expiry(p)
    if not exp:
        return ""
    days = (exp - today_ist()).days
    if days < 0:
        return f"Expired on {exp:%d %b %Y} ({-days} days ago)."
    if days == 0:
        return f"Expires today, {exp:%d %b %Y}."
    return f"Valid until {exp:%d %b %Y} ({days} days left)."


def _show_certificate(p, key):
    """Image inline, PDF as a download. Bad bytes from older rows never raise."""
    cert = p.get("fssai_cert")
    if not cert:
        st.caption("No certificate uploaded.")
        return
    data = bytes(cert)
    if data[:5] == b"%PDF-" or (p.get("fssai_cert_mime") or "") == _PDF_MIME:
        st.download_button("Download certificate (PDF)", data,
                           file_name=f"fssai-{normalize_fssai_no(p.get('fssai_no')) or p.get('id')}.pdf",
                           mime=_PDF_MIME, key=key)
        if not _is_pdf(data):
            st.warning("This PDF looks damaged. Ask her to upload the certificate again.")
        return
    if not safe_image(data, caption="FSSAI certificate", width="stretch"):
        st.warning("This certificate file couldn't be shown. Ask her to upload it again.")


# ----------------------------------------------------------------------------- the rule
def food_block_reason(provider):
    """One plain sentence on exactly what stands between this kitchen and selling food,
    or None when nothing does. The helper's own steps come first; GharSe's check last."""
    p = provider or {}
    status = p.get("fssai_status") or "none"
    if status == "applied":
        return ("Your FSSAI application is still in progress: once the certificate arrives, "
                "add its number, expiry date and a photo of it.")
    if status != "registered":
        return "Add your FSSAI registration: food can only be sold on GharSe by a registered kitchen."
    number = normalize_fssai_no(p.get("fssai_no"))
    if not number:
        return "Add the 14-digit FSSAI number printed on your certificate."
    if _number_problem(number):
        return "Your FSSAI number doesn't look right: it should be the 14 digits on your certificate."
    if not p.get("fssai_cert"):
        return "Upload a photo or PDF of your FSSAI certificate."
    exp = _expiry(p)
    if not exp:
        return "Add the expiry date printed on your FSSAI certificate."
    if exp < today_ist():
        return (f"Your FSSAI certificate expired on {exp:%d %b %Y}: renew it on FoSCoS and "
                "upload the new one.")
    if not p.get("fssai_verified"):
        return "GharSe hasn't checked your FSSAI certificate yet: an admin will verify it soon."
    return None


def can_sell_food(provider):
    """True only for a kitchen whose FSSAI certificate an admin approved (fssai_verified),
    still registered, well formed, uploaded and in date. providers.verified (general
    helper verification) plays no part."""
    return bool(provider) and food_block_reason(provider) is None


def expiring_soon(days=30):
    """Registered kitchens whose certificate runs out within `days` from today — including
    ones that have already run out, since those are the most urgent to chase. Soonest first.
    Kitchens that have stopped selling food or are mid-renewal ('none' / 'applied') are left out."""
    today = today_ist()
    limit = (today + timedelta(days=int(days))).isoformat()
    rows = q("SELECT * FROM providers WHERE fssai_status = ? AND fssai_expiry IS NOT NULL "
             "AND fssai_expiry <> '' AND fssai_expiry <= ? ORDER BY fssai_expiry", ("registered", limit))
    out = []
    for r in rows:
        d = dict(r)
        if _expiry(d) is not None:
            out.append(d)
    return out


# ----------------------------------------------------------------------------- guide page
def _tier_for(turnover):
    if turnover <= FACTS["basic_max"]:
        return "basic"
    if turnover <= FACTS["state_max"]:
        return "state"
    return "central"


def fssai_guide(user=None):
    st.markdown(
        "<div class='page-title'>FSSAI, step by step</div>"
        "<div class='page-sub'>Every kitchen that sells food on GharSe holds an FSSAI registration. "
        "Here is which one you need, what to keep ready, and where to apply.</div>",
        unsafe_allow_html=True,
    )

    if user and user.get("role") == "provider" and user.get("linked_id"):
        p = provider_of(user)
        if p:
            reason = food_block_reason(p)
            with st.container(border=True):
                st.markdown(f"**Your kitchen** {_status_pill(p)}", unsafe_allow_html=True)
                st.markdown(f"<span class='muted'>{h(reason or 'You are all set to sell food on GharSe.')}"
                            "</span>", unsafe_allow_html=True)
                st.caption("Update your details under My Profile → FSSAI.")

    if not FACTS_CHECKED_ON:
        st.info(
            "Every FSSAI figure on this page (limits, fees, forms, documents, steps) is shown "
            f"{_CONFIRM}. Rules change, and where this page and the portal disagree, the portal is right."
        )

    # ---- what and why
    st.markdown("#### What is FSSAI?")
    st.markdown(
        "**FSSAI**, the Food Safety and Standards Authority of India, registers and licenses food "
        f"businesses. Registration, licences and renewals all start on its portal, {FACTS['foscos_name']}.\n\n"
        "For a home kitchen, the one you need is almost always **Basic registration**, applied for online."
    )
    st.markdown("#### Why GharSe asks for it")
    st.markdown(
        "Customers are trusting food from a home they have never seen. Your FSSAI certificate, checked "
        "by our team, is what lets them order without worry. Before your food goes live on GharSe we need:\n"
        "1. **Registered** status with your 14-digit FSSAI number\n"
        "2. A **photo or PDF of the certificate**\n"
        "3. An **expiry date** that hasn't passed\n"
        "4. **GharSe's check**: an admin compares your certificate with the official record\n\n"
        "Handmade products and home services (tailoring, garlands, mehendi and the rest) don't need "
        "FSSAI and can be listed straight away."
    )

    # ---- official links
    st.markdown("#### Official FSSAI websites")
    cols = st.columns(len(OFFICIAL_LINKS))
    for i, (label, url, _) in enumerate(OFFICIAL_LINKS):
        cols[i].link_button(label, url, width="stretch", key=f"fg_link{i}")
    st.markdown("\n".join(f"- **{label}**: {hint}" for label, _, hint in OFFICIAL_LINKS))
    st.caption(f"FoSCoS: {FOSCOS_HOME} · FSSAI: {FSSAI_HOME} · FoSTaC: {FOSTAC_HOME}. "
               "These are government websites; GharSe has no account on them and never asks for your "
               "FoSCoS password.")

    # ---- tiers
    st.markdown("#### Which one do I need?")
    st.markdown(
        "There are three kinds, and which one applies depends mostly on how much you sell in a year. "
        f"<span class='muted'>(Figures {_CONFIRM}.)</span>",
        unsafe_allow_html=True,
    )
    table(FACTS["tiers"])
    with st.container(border=True):
        turnover = st.number_input("Roughly how much do you expect to sell in a year? (₹)",
                                   min_value=0, max_value=10_000_000_000, value=0, step=10_000,
                                   key="fg_turnover")
        if turnover:
            tier = _tier_for(turnover)
            row = next(r for r in FACTS["tiers"] if r["Tier"] == TIER_LABELS[tier])
            st.markdown(
                f"At that level you would usually need <b>{h(row['Tier'])}</b> "
                f"(form: {h(row['Form'])}; fee: {h(row['Fee'])}). <span class='muted'>FoSCoS decides for "
                f"certain when you answer its questions. Figures {_CONFIRM}.</span>",
                unsafe_allow_html=True,
            )
            if tier == "basic":
                st.caption("This is what nearly every home kitchen needs.")
        else:
            st.caption("Type a number to see which one fits. Basic registration covers yearly turnover "
                       "up to ₹12 lakh.")

    # ---- documents
    st.markdown("#### Keep these ready (Basic registration)")
    st.caption(f"Tick them off as you collect them. Nothing here is saved; it is just for you. "
               f"Typical list, {_CONFIRM}. {FACTS['documents_note']}")
    for i, doc in enumerate(FACTS["documents_basic"]):
        st.checkbox(doc, key=f"fg_doc{i}")

    # ---- steps
    st.markdown("#### Applying on FoSCoS")
    st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(FACTS["steps_basic"], 1)))
    st.caption(f"Steps {_CONFIRM}. Button names on the portal change from time to time; if one doesn't "
               "match, look for the closest wording, or ask us to sit with you.")

    # ---- number, renewal, training
    c = st.columns(2)
    with c[0]:
        with st.container(border=True):
            st.markdown("**What the number looks like**")
            st.markdown(
                f"Your registration or licence number is <b>{FACTS['number_digits']} digits</b>, printed "
                f"on your certificate. <span class='muted'>({_CONFIRM}.)</span> You can look any number "
                "up on FoSCoS with the <i>Verify an FSSAI number</i> button above.",
                unsafe_allow_html=True,
            )
    with c[1]:
        with st.container(border=True):
            st.markdown("**Renewal**")
            st.markdown(
                f"{h(FACTS['renewal'])} <span class='muted'>({_CONFIRM}.)</span><br>"
                "On GharSe, your profile warns you 30 days before the date. Once it passes, your food "
                "stops being shown to customers until you upload the renewed certificate and GharSe "
                "checks it.",
                unsafe_allow_html=True,
            )
    with st.container(border=True):
        st.markdown("**Food safety training (FoSTaC)**")
        st.markdown(
            f"FSSAI's food safety training, FoSTaC, has its own site: {FOSTAC_HOME}. Whether your "
            "registration needs a trained person is something to confirm on FoSCoS."
        )

    # ---- support offer
    st.markdown("#### GharSe will sit with you and fill it")
    with st.container(border=True):
        st.markdown(
            "Never filled a government form online? You don't have to do it alone. Someone from GharSe "
            "will sit with you (at your home, on a call, or at our help desk), open FoSCoS on your phone, "
            "and fill it in with you, step by step. You pay only the government fee, directly on the "
            "portal. Our help is free and part of being on GharSe.\n\n"
            "Bring the documents on the checklist above and your phone."
        )
        if user and user.get("role") == "provider" and user.get("linked_id"):
            _help_button(user)
        else:
            st.caption("Sign in as a helper to ask for this, or tell the GharSe team when you join.")
        _flash("fg_flash")

    st.caption(f"Sources: the Food Safety and Standards Authority of India, {FSSAI_HOME}, and the FoSCoS "
               f"portal, {FOSCOS_HOME}. GharSe is not part of FSSAI; where this page and the portal "
               "disagree, follow the portal.")


def _help_button(user):
    p = provider_of(user)
    if not p:
        return
    tag = f"(helper #{p['id']})"
    # Keyed on the provider id, not on text in the body: the body carries names other
    # helpers choose, so a substring match let one helper hide another's button.
    # notifications.order_id is the table's generic reference column.
    pending = q("SELECT 1 FROM notifications WHERE kind=? AND status='Queued' AND order_id=?",
                ("FSSAI help", p["id"]))
    if pending:
        st.markdown("<span class='pill ok'>Asked ✓</span> <span class='muted'>The GharSe team has your "
                    "request and will call you to fix a time.</span>", unsafe_allow_html=True)
        return
    if st.button("Ask GharSe to help me apply", type="primary", key="fg_help"):
        body = (f"FSSAI help requested by {p.get('owner_name') or p.get('display_name') or 'a helper'} "
                f"{tag}, {p.get('display_name') or ''}, {p.get('area') or 'area not set'}, "
                f"phone {p.get('phone') or 'not on file'}. Please call and sit with her to fill the "
                f"FoSCoS application.")
        notify("", "GharSe team", "admin", "FSSAI help", body, order_id=p["id"])
        st.session_state["fg_flash"] = ("Done. The GharSe team will call you on the number in your "
                                        "profile to fix a time.")
        st.rerun()


# ----------------------------------------------------------------------------- provider section
_NEXT_STEP = {
    "none": "Start with the FSSAI guide: most home kitchens need Basic registration, applied for online "
            "on FoSCoS. Or ask GharSe to sit with you and fill it.",
    "applied": "Waiting for FSSAI. Keep your application reference number handy; when the certificate "
               "comes, choose \"I have my certificate\" below and upload it.",
}


def provider_fssai_section(user):
    p = provider_of(user)
    if not p:
        st.warning("We couldn't find your helper profile. Please sign out and sign in again.")
        return
    p = dict(p)
    st.markdown("#### FSSAI registration")
    _flash("fssai_flash")

    status = p.get("fssai_status") or "none"
    reason = food_block_reason(p)
    with st.container(border=True):
        st.markdown(f"**Food selling status** {_status_pill(p)}", unsafe_allow_html=True)
        bits = []
        if p.get("fssai_kind"):
            bits.append(TIER_LABELS.get(p["fssai_kind"], p["fssai_kind"]))
        if p.get("fssai_no"):
            bits.append(f"No. {p['fssai_no']}")
        if status == "applied" and p.get("fssai_arn"):
            bits.append(f"ARN {p['fssai_arn']}")
        if _expiry_line(p):
            bits.append(_expiry_line(p))
        if bits:
            st.markdown("<span class='muted'>" + " · ".join(h(b) for b in bits) + "</span>",
                        unsafe_allow_html=True)
        if reason is None:
            st.markdown("You're all set: your food listings and meal plans can go live.")
            exp = _expiry(p)
            if exp and (exp - today_ist()).days <= 30:
                st.warning("Your certificate runs out within 30 days. Renew it on FoSCoS now and upload "
                           "the new one here so your food doesn't stop showing to customers.")
        else:
            if _sent_back(p) and status == "registered":
                nxt = "GharSe sent your details back (see the note). Fix them below and save; we'll check again."
            else:
                nxt = _NEXT_STEP.get(status) or reason
            st.markdown(f"**What's next:** {h(nxt)}", unsafe_allow_html=True)
        if _sent_back(p):
            st.markdown(f"<span class='pill warn'>Note from GharSe</span> {h(p['verify_note'])}",
                        unsafe_allow_html=True)
        if p.get("fssai_cert"):
            with st.expander("Your uploaded certificate"):
                _show_certificate(p, key=f"fssai_mycert{p['id']}")

    statuses = list(STATUS_LABELS)
    tiers = list(TIER_LABELS)
    exp_now = _expiry(p)
    with st.form("fssai_form"):
        new_status = st.selectbox("Where are you with FSSAI?", statuses,
                                  index=statuses.index(status) if status in statuses else 0,
                                  format_func=STATUS_LABELS.get, key="fssai_status")
        c = st.columns(2)
        kind = c[0].selectbox("Kind", tiers,
                              index=tiers.index(p["fssai_kind"]) if p.get("fssai_kind") in tiers else 0,
                              format_func=TIER_LABELS.get, key="fssai_kind",
                              help="Almost every home kitchen has Basic registration.")
        arn = c[1].text_input("Application reference number (ARN), while you wait",
                              p.get("fssai_arn") or "", key="fssai_arn",
                              help="Shown on FoSCoS after you pay. Needed only while you're waiting.")
        c = st.columns(2)
        number = c[0].text_input("FSSAI number (14 digits, from the certificate)",
                                 p.get("fssai_no") or "", key="fssai_no", max_chars=20)
        # A stored date outside the picker's range used to crash this whole page, with no way
        # for her to fix it. Clamp what we show; validation below still judges what she saves.
        if exp_now is not None:
            exp_now = min(max(exp_now, date(2000, 1, 1)), date(2100, 12, 31))
        expiry = c[1].date_input("Valid until (expiry date on the certificate)", value=exp_now,
                                 min_value=date(2000, 1, 1), max_value=date(2100, 12, 31),
                                 format="DD/MM/YYYY", key="fssai_expiry",
                                 help="A new date needs the new certificate uploaded in the same save. "
                                      "Only used when you have your certificate.")
        upload = st.file_uploader(
            f"Certificate: a clear photo or the PDF from FoSCoS (up to {MAX_IMG_MB} MB)"
            + (" — leave empty to keep the one you uploaded" if p.get("fssai_cert") else ""),
            type=list(_IMG_TYPES) + ["pdf"], key="fssai_upload")
        if p.get("fssai_verified"):
            st.warning("Your FSSAI certificate is approved. If you change your status, kind, number, expiry "
                       "date or certificate, GharSe has to check it again: your food stops showing to "
                       "customers until an admin approves it, usually within a day.")
        submitted = st.form_submit_button("Save FSSAI details", type="primary", width="stretch")

    if submitted:
        _save_provider(p, new_status, kind, arn, number, expiry, upload)


def _read_upload(upload):
    """(bytes, mime, error). The file's own name and declared type are never trusted:
    PDFs are recognised by their bytes, images must decode, mime is what we validated."""
    if upload is None:
        return None, None, None
    raw = upload.getvalue() or b""
    if not raw:
        return None, None, "That file is empty. Please upload the PDF from FoSCoS or a photo."
    if len(raw) > _MAX_BYTES or (getattr(upload, "size", 0) or 0) > _MAX_BYTES:
        return None, None, f"The certificate file is too big. Please keep it under {MAX_IMG_MB} MB."
    name = (getattr(upload, "name", "") or "").lower()
    if raw[:5] == b"%PDF-":
        if b"%%EOF" not in raw:
            return None, None, "That PDF looks incomplete. Please download it from FoSCoS again and upload it."
        return raw, _PDF_MIME, None
    data = process_image(upload)
    if not data:
        if name.endswith(".pdf") or getattr(upload, "type", None) == _PDF_MIME:
            return None, None, "That file doesn't look like a PDF. Please upload the PDF from FoSCoS or a photo."
        return None, None, "That image couldn't be read. Please try another photo or the PDF from FoSCoS."
    return data, "image/jpeg", None   # process_image always re-encodes to JPEG


def _save_provider(p, status, kind, arn, number, expiry, upload):
    errors = []
    old_status = p.get("fssai_status") or "none"
    old_no = normalize_fssai_no(p.get("fssai_no"))
    old_exp = _expiry(p)
    number = normalize_fssai_no(number)
    arn = (arn or "").strip()
    if number:
        prob = _number_problem(number)
        if prob:
            errors.append(prob)
    if arn and not ARN_RE.match(arn):
        errors.append("The ARN should be letters and numbers, as shown on FoSCoS.")
    data, mime, err = _read_upload(upload)
    if err:
        errors.append(err)
    if status == "applied" and not arn:
        errors.append("Add the application reference number (ARN) FoSCoS gave you, so we can follow it up.")
    if status == "registered":
        if not number:
            errors.append("Add the 14-digit FSSAI number from your certificate.")
        if expiry is None:
            errors.append("Add the expiry date printed on your certificate.")
        elif expiry < today_ist():
            errors.append(f"That expiry date, {expiry:%d %b %Y}, has already passed. Renew the certificate on "
                          "FoSCoS first, then enter the new date and upload the new certificate.")
        elif kind == "basic" and expiry > today_ist() + timedelta(days=366 * FACTS["basic_max_years"] + 31):
            errors.append(f"That expiry date, {expiry:%d %b %Y}, is more than {FACTS['basic_max_years']} "
                          "years away, but Basic registration is issued for 1 to 5 years. Please check the "
                          "date on your certificate.")
        if data is None and not p.get("fssai_cert"):
            errors.append("Upload a photo or PDF of your certificate.")
        elif expiry is not None and old_exp is not None and expiry != old_exp and data is None:
            errors.append("A new expiry date has to come from a new certificate: upload the renewed "
                          "certificate in the same save.")
    else:
        # Not registered: the date field does not apply. Keep whatever is on file rather
        # than refusing her over a stale date she can't change yet.
        expiry = old_exp
    if errors:
        for e in errors:
            st.error(e)
        return

    changed = (number != old_no or data is not None or expiry != old_exp
               or (kind or None) != (p.get("fssai_kind") or None) or status != old_status)
    was_ok = bool(p.get("fssai_verified"))
    recheck = was_ok and changed

    sets = ["fssai_status=?", "fssai_kind=?", "fssai_arn=?", "fssai_no=?", "fssai_expiry=?"]
    params = [status, kind, arn or None, number or None, expiry.isoformat() if expiry else None]
    if data is not None:
        sets += ["fssai_cert=?", "fssai_cert_mime=?"]
        params += [data, mime]
    if changed:
        # Any change means an admin has to look again. fssai_verified_at / _by stay as the
        # record of the last approval; the admin panel shows it as "approved before".
        sets += ["fssai_verified=?"]
        params += [0]
    if changed or not was_ok:
        # A send-back note has been answered by this save.
        sets += ["verify_note=?"]
        params += [None]
    execute(f"UPDATE providers SET {', '.join(sets)} WHERE id=?", params + [p["id"]])

    if recheck:
        msg = ("Saved. Because your FSSAI details changed, GharSe will check them again; your food is "
               "hidden from customers until then.")
    elif status == "registered" and not was_ok:
        msg = "Saved. GharSe will check your certificate and let you know on WhatsApp."
    else:
        msg = "Saved."
    st.session_state["fssai_flash"] = msg
    st.rerun()


# ----------------------------------------------------------------------------- admin panel
def admin_fssai_panel(provider, admin_user=None):
    p = dict(provider or {})
    if not p.get("id"):
        return
    pid = p["id"]
    # What this admin saw last time the card rendered, then what they see now.
    fp_key = f"fssai_fp{pid}"
    shown_fp = st.session_state.get(fp_key)
    st.session_state[fp_key] = _fingerprint(p)

    _flash(f"fssai_adm_flash{pid}")
    status = p.get("fssai_status") or "none"
    st.markdown(f"**FSSAI** {_status_pill(p)}", unsafe_allow_html=True)
    rows = [
        ("Status", STATUS_LABELS.get(status, status)),
        ("Kind", TIER_LABELS.get(p.get("fssai_kind") or "", p.get("fssai_kind") or "")),
        ("Number", p.get("fssai_no") or ""),
        ("ARN", p.get("fssai_arn") or ""),
        ("Expiry", _expiry_line(p) or (p.get("fssai_expiry") or "")),
    ]
    dash = "<span class='muted'>—</span>"
    st.markdown("<br>".join(f"<span class='muted'>{k}:</span> {h(v) if v else dash}" for k, v in rows),
                unsafe_allow_html=True)
    num = normalize_fssai_no(p.get("fssai_no"))
    if num and _number_problem(num):
        st.warning("This number doesn't look right (not 14 digits, or one digit repeated). "
                   "Ask her to check the certificate.")

    exp = _expiry(p)
    if exp:
        days = (exp - today_ist()).days
        if days < 0:
            st.error(f"Certificate expired {-days} days ago. Food listings are blocked until a renewed one "
                     "is uploaded and approved.")
        elif days <= 30:
            st.warning(f"Certificate expires in {days} days ({exp:%d %b %Y}). Remind her to renew on FoSCoS.")

    c = st.columns([2, 1])
    with c[0]:
        _show_certificate(p, key=f"fssai_dl{pid}")
    with c[1]:
        st.link_button("Verify number on FoSCoS", FOSCOS_HOME, width="stretch",
                       key=f"fssai_vfy{pid}")
        st.caption("Search the licence / registration (FBO) number there and compare the name and address "
                   "with the certificate.")

    missing = food_block_reason({**p, "fssai_verified": 1})
    if missing:
        st.caption(f"Still missing for food: {h(missing)}")
    if not p.get("fssai_verified") and p.get("fssai_verified_at") and not missing:
        st.caption(f"Approved before on {h(str(p['fssai_verified_at'])[:10])}; her details have changed "
                   "since, so please check them again.")
    if p.get("verify_note"):
        st.caption(f"Last note: {h(p['verify_note'])}")

    with st.form(f"fssai_adm{pid}"):
        reason = st.text_input("Reason, if sending back (she will see this)", key=f"fssai_reason{pid}",
                               placeholder="e.g. The name on the certificate doesn't match your profile")
        b = st.columns(2)
        approve = b[0].form_submit_button("Approve", type="primary", width="stretch")
        send_back = b[1].form_submit_button("Send back", width="stretch")

    if approve:
        _approve(pid, shown_fp, admin_user)
    if send_back:
        reason = (reason or "").strip()
        if not reason:
            st.error("Write a reason so she knows what to fix.")
            return
        # Status stays as it is: a registered kitchen sent back over a typo still holds
        # her certificate. Only the FSSAI approval is withdrawn.
        execute("UPDATE providers SET fssai_verified=0, fssai_verified_at=NULL, fssai_verified_by=NULL, "
                "verify_note=? WHERE id=?", (reason, pid))
        notify_provider(pid, "FSSAI sent back",
                        f"GharSe couldn't approve your FSSAI details yet: {reason} "
                        "Please open My Profile → FSSAI, fix it and save; we'll check again.")
        st.session_state[f"fssai_adm_flash{pid}"] = "Sent back. She has been notified with your reason."
        st.rerun()


def _approve(pid, shown_fp, admin_user):
    rows = q("SELECT * FROM providers WHERE id=?", (pid,))
    if not rows:
        st.error("This helper no longer exists.")
        return
    fresh = dict(rows[0])
    if not shown_fp or shown_fp != _fingerprint(fresh):
        st.warning("Her FSSAI details changed while you were looking. The card above now shows the latest; "
                   "please review it again before approving.")
        return
    missing = food_block_reason({**fresh, "fssai_verified": 1})
    if missing:
        st.error(f"Can't approve yet. {missing}")
        return
    if _cert_kind(fresh.get("fssai_cert")) is None:
        st.error("Can't approve: the certificate file can't be opened. Send it back and ask her to upload "
                 "it again.")
        return
    admin_id = (admin_user or {}).get("id") if isinstance(admin_user, dict) else None
    note = f"FSSAI approved on {today_ist():%d %b %Y}."
    # Guarded write: refuses if the row's details moved between the check above and now.
    done = execute(
        "UPDATE providers SET fssai_verified=1, fssai_verified_at=?, fssai_verified_by=?, verify_note=? "
        "WHERE id=? AND COALESCE(fssai_no, '')=? AND COALESCE(fssai_expiry, '')=? "
        "AND COALESCE(fssai_status, '')=? AND COALESCE(fssai_kind, '')=? RETURNING id",
        (now_iso(), admin_id, note, pid, fresh.get("fssai_no") or "", fresh.get("fssai_expiry") or "",
         fresh.get("fssai_status") or "", fresh.get("fssai_kind") or ""),
    )
    if done is None:
        st.warning("Her FSSAI details changed while you were looking. Please review them again.")
        return
    notify_provider(pid, "FSSAI approved",
                    "Good news: GharSe has checked your FSSAI certificate and approved it. "
                    "Your food listings and meal plans can go live now.")
    st.session_state[f"fssai_adm_flash{pid}"] = "Approved. She has been notified."
    st.rerun()
