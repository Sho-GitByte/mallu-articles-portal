# fssai.py — everything FSSAI on GharSe.
#
# The rule, from the founder: a home kitchen that sells food here must hold an FSSAI
# registration or licence. This module owns that rule end to end:
#   fssai_guide(user)              the help page anyone can open — what FSSAI is, which
#                                  tier applies, documents, steps on FoSCoS, official links
#   can_sell_food(provider)        the single gate every food listing / meal plan checks
#   food_block_reason(provider)    one plain sentence on what is still missing
#   provider_fssai_section(user)   the helper's own status card and details form
#   admin_fssai_panel(provider)    the admin's check: certificate, expiry, approve / send back
#   expiring_soon(days)            certificates that need renewing
#
# About the facts on the guide page. The official sites could not be opened when this
# was written (the network refused them), so nothing below was re-checked against them.
# The tier figures, documents and steps are the last published ones we know of and are
# labelled on the page as "check on the portal". When someone confirms them on
# foscos.fssai.gov.in, update FACTS and set FACTS_CHECKED_ON to that date: the page
# then stops asking people to double-check.

import re
from datetime import date, timedelta

import streamlit as st

from store import execute, now_iso, q, today_ist
from ui import MAX_IMG_MB, h, notify, notify_provider, process_image, provider_of, table

# ----------------------------------------------------------------------------- official links
# Hostnames of the official FSSAI sites. Deep links inside FoSCoS could not be confirmed,
# so every button opens a site's front page and the hint says which button to press there.
FOSCOS_HOME = "https://foscos.fssai.gov.in/"
FSSAI_HOME = "https://www.fssai.gov.in/"
FOSTAC_HOME = "https://fostac.fssai.gov.in/"

FACTS_CHECKED_ON = None   # ISO date the figures below were last confirmed on the official site

OFFICIAL_LINKS = [
    # (button label, url, what to do once it opens)
    ("Open FoSCoS", FOSCOS_HOME,
     "FoSCoS is FSSAI's online system for food licences and registrations."),
    ("Log in", FOSCOS_HOME,
     "Press Login at the top of the FoSCoS page and sign in with the mobile number or email you registered."),
    ("Start a new application", FOSCOS_HOME,
     "On FoSCoS, choose Apply for Licence / Registration, then pick Karnataka as your state."),
    ("Verify an FSSAI number", FOSCOS_HOME,
     "On FoSCoS, use the licence / registration search (FBO search) and enter the 14-digit number."),
    ("Food safety training (FoSTaC)", FOSTAC_HOME,
     "FoSTaC is FSSAI's food-safety training programme. Courses are listed on this site."),
]

# ----------------------------------------------------------------------------- facts
TIER_LABELS = {"basic": "Basic registration", "state": "State licence", "central": "Central licence"}
STATUS_LABELS = {
    "none": "I don't have one yet",
    "applied": "I've applied and I'm waiting",
    "registered": "I have my certificate",
}

FACTS = {
    "tiers": [
        {"Tier": "Basic registration",
         "Who it is for": "Petty food businesses: home kitchens, tiffin and snack makers, small vendors",
         "Yearly turnover": "Up to ₹12 lakh",
         "Form": "Form A",
         "Fee": "₹100 a year",
         "Validity": "1 to 5 years, you choose (fee is per year)"},
        {"Tier": "State licence",
         "Who it is for": "Medium businesses: bigger caterers, restaurants, small manufacturers",
         "Yearly turnover": "₹12 lakh to ₹20 crore",
         "Form": "Form B",
         "Fee": "₹2,000 to ₹5,000 a year, by type of business",
         "Validity": "1 to 5 years, you choose (fee is per year)"},
        {"Tier": "Central licence",
         "Who it is for": "Large businesses: big manufacturers, importers / exporters, multi-state operators",
         "Yearly turnover": "Above ₹20 crore",
         "Form": "Form B",
         "Fee": "₹7,500 a year",
         "Validity": "1 to 5 years, you choose (fee is per year)"},
    ],
    # Turnover limits used by the little "which one is mine?" helper, in rupees a year.
    "basic_max": 12_00_000,
    "state_max": 20_00_00_000,
    "documents_basic": [
        "A recent passport-size photo of you",
        "Photo ID: Aadhaar, PAN, voter ID, passport or driving licence",
        "Proof of the address you cook at: Aadhaar with that address, an electricity bill, "
        "or a rent agreement (for a rented home, some offices also ask for the owner's NOC)",
        "The list of foods you will make and sell (for example: meals, snacks, sweets, pickles)",
        "A mobile number and email you can receive an OTP on",
        "A way to pay the fee online: UPI, debit card or net banking",
    ],
    "steps_basic": [
        "Open **FoSCoS** (button above) on a phone or computer.",
        "Press **Apply for Licence / Registration**. Choose **Karnataka** as your state and enter "
        "your kitchen's address and pincode.",
        "Answer the questions about your kind of business. For cooking at home and selling to "
        "neighbours, pick the option for petty / home-based food business and enter your expected "
        "yearly sales. If it is under ₹12 lakh, FoSCoS should offer **Registration (Form A)**.",
        "**Sign up**: give your name, mobile number and email, and type in the OTPs sent to them. "
        "Note down the user ID and password you set.",
        "**Log in** and open the registration form. Fill in your name, the kitchen address, and the "
        "foods you will make.",
        "Choose how many years you want it for (1 to 5).",
        "**Upload** your photo, photo ID and address proof (see the checklist).",
        "**Pay the fee** online. Keep the receipt.",
        "Write down the **Application Reference Number (ARN)** shown at the end. You need it to "
        "track the application, and GharSe asks for it while you wait.",
        "Check back on FoSCoS using **Track application** with your ARN. A food safety officer may "
        "call or visit before approving.",
        "When it is approved, **download the certificate** from your FoSCoS dashboard, then add the "
        "number, expiry date and certificate under **My Profile → FSSAI** on GharSe.",
    ],
    "number_format": "A 14-digit number, printed on your certificate",
    "renewal": (
        "Apply for renewal on FoSCoS before the expiry date printed on your certificate: log in, open "
        "your registration and choose Renew. If it lapses you may have to pay a late fee or apply "
        "afresh. GharSe reminds you 30 days before the date and pauses food listings once it has "
        "passed, until you upload the renewed certificate."
    ),
}

FSSAI_NO_RE = re.compile(r"^\d{14}$")
ARN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-]{4,39}$")
_MAX_BYTES = MAX_IMG_MB * 1024 * 1024
_IMG_TYPES = ("jpg", "jpeg", "png", "webp")


# ----------------------------------------------------------------------------- small helpers
def normalize_fssai_no(raw):
    """People type the number with spaces or dashes, the way it is printed. Store digits."""
    return re.sub(r"[\s\-]", "", (raw or "").strip())


def fssai_no_ok(raw):
    return bool(FSSAI_NO_RE.match(normalize_fssai_no(raw)))


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


def _status_pill(p):
    status = (p or {}).get("fssai_status") or "none"
    exp = _expiry(p)
    if status == "registered" and exp and exp < today_ist():
        return "<span class='pill warn'>Certificate expired</span>"
    if can_sell_food(p):
        return "<span class='pill ok'>FSSAI approved ✓</span>"
    if status == "registered":
        if food_block_reason({**p, "verified": 1}) is None:
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
    cert = p.get("fssai_cert")
    if not cert:
        st.caption("No certificate uploaded.")
        return
    data = bytes(cert)
    mime = p.get("fssai_cert_mime") or ""
    if mime == "application/pdf":
        st.download_button("Download certificate (PDF)", data,
                           file_name=f"fssai-{normalize_fssai_no(p.get('fssai_no')) or p.get('id')}.pdf",
                           mime="application/pdf", key=key)
    else:
        st.image(data, caption="FSSAI certificate", width="stretch")


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
    if not FSSAI_NO_RE.match(number):
        return "Your FSSAI number doesn't look right: it should be exactly 14 digits."
    if not p.get("fssai_cert"):
        return "Upload a photo or PDF of your FSSAI certificate."
    exp = _expiry(p)
    if not exp:
        return "Add the expiry date printed on your FSSAI certificate."
    if exp < today_ist():
        return (f"Your FSSAI certificate expired on {exp:%d %b %Y}: renew it on FoSCoS and "
                "upload the new one.")
    if not p.get("verified"):
        return "GharSe hasn't checked your FSSAI certificate yet: an admin will verify it soon."
    return None


def can_sell_food(provider):
    """True only for an admin-verified kitchen with a registered, well-formed, uploaded,
    in-date FSSAI certificate. Replaces the older number-on-file rule in app.py."""
    return bool(provider) and food_block_reason(provider) is None


def expiring_soon(days=30):
    """Providers whose certificate runs out within `days` from today — including ones that
    have already run out, since those are the most urgent to chase. Soonest first."""
    today = today_ist()
    limit = (today + timedelta(days=int(days))).isoformat()
    rows = q("SELECT * FROM providers WHERE fssai_expiry IS NOT NULL AND fssai_expiry <> '' "
             "AND fssai_expiry <= ? ORDER BY fssai_expiry", (limit,))
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
        "Here is what it is, which one you need, what to keep ready, and exactly where to click.</div>",
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
            "Fees, limits and documents below are the last published figures we know of. Rules change, "
            "so please confirm each one on the FoSCoS portal before you pay. Where this page says "
            "\"check on the portal\", the portal's word is final."
        )

    # ---- what and why
    st.markdown("#### What is FSSAI?")
    st.markdown(
        "The **Food Safety and Standards Authority of India (FSSAI)** is the government body that looks "
        "after food safety under the Food Safety and Standards Act, 2006. Anyone who makes food to sell "
        "is a *food business* in its eyes, and that includes a kitchen at home that cooks for neighbours.\n\n"
        "Registering gives you a number that tells customers your kitchen is known to the food safety "
        "department. Selling food without one can attract penalties under the Act. For a small home "
        "kitchen it is usually the simplest kind, **Basic registration**, done online."
    )
    st.markdown("#### Why GharSe asks for it")
    st.markdown(
        "Customers are trusting food from a home they have never seen. Your FSSAI certificate, checked "
        "by our team, is what lets them order without worry. Before your food goes live on GharSe we need:\n"
        "1. **Registered** status with a valid 14-digit FSSAI number\n"
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
        "There are three kinds. Which one applies depends mostly on how much you sell in a year. "
        "<span class='muted'>(Figures: last published, check on the portal.)</span>",
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
                f"At that level you would usually need <b>{h(row['Tier'])}</b> ({h(row['Form'])}, "
                f"{h(row['Fee'])}). <span class='muted'>FoSCoS decides for certain when you answer its "
                f"questions, so check on the portal.</span>",
                unsafe_allow_html=True,
            )
            if tier == "basic":
                st.caption("This is the one almost every home kitchen on GharSe starts with.")
        else:
            st.caption("Type a number to see which one fits. Most home kitchens sell well under ₹12 lakh a year.")

    # ---- documents
    st.markdown("#### Keep these ready (Basic registration)")
    st.caption("Tick them off as you collect them. Nothing here is saved; it is just for you. "
               "The exact list is on the portal, so check there too.")
    for i, doc in enumerate(FACTS["documents_basic"]):
        st.checkbox(doc, key=f"fg_doc{i}")

    # ---- steps
    st.markdown("#### Applying on FoSCoS, click by click")
    st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(FACTS["steps_basic"], 1)))
    st.caption("Button names on FoSCoS change from time to time. If one doesn't match, look for the "
               "closest wording, or ask us to sit with you.")

    # ---- number, renewal, training
    c = st.columns(2)
    with c[0]:
        with st.container(border=True):
            st.markdown("**What the number looks like**")
            st.markdown(
                f"{h(FACTS['number_format'])}, for example <b>1 2 3 4 5 6 7 8 9 0 1 2 3 4</b> "
                "(that one is made up). <span class='muted'>Check the format on the portal.</span> "
                "Packaged food usually carries it on the label next to the FSSAI logo. You can confirm any "
                "number with the <i>Verify an FSSAI number</i> button above.",
                unsafe_allow_html=True,
            )
    with c[1]:
        with st.container(border=True):
            st.markdown("**Renewal**")
            st.markdown(f"{h(FACTS['renewal'])} <span class='muted'>Check renewal timings and fees on the "
                        "portal.</span>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown("**Food safety training (FoSTaC)**")
        st.markdown(
            "FSSAI runs short food-safety courses under FoSTaC: clean water, storage, handling, allergens. "
            "Whether your registration requires a trained person is something to check on the portal; "
            "either way a basic course is worth an afternoon, and it is what customers mean by \"clean\"."
        )

    # ---- support offer
    st.markdown("#### GharSe will sit with you and fill it")
    with st.container(border=True):
        st.markdown(
            "Never filled a government form online? You don't have to do it alone. Someone from GharSe "
            "will sit with you (at your home, on a call, or at our help desk), open FoSCoS on your phone, "
            "and fill it in with you, step by step. You pay only the government fee, directly on the "
            "portal. Our help is free and part of being on GharSe.\n\n"
            "Bring: your Aadhaar or other photo ID, a photo of yourself, an electricity bill or rent "
            "agreement for your address, and your phone (for the OTP)."
        )
        if user and user.get("role") == "provider" and user.get("linked_id"):
            _help_button(user)
        else:
            st.caption("Sign in as a helper to ask for this, or tell the GharSe team when you join.")
        _flash("fg_flash")

    st.caption(f"Source: the Food Safety and Standards Authority of India, {FSSAI_HOME} and the FoSCoS "
               f"portal, {FOSCOS_HOME}. GharSe is not part of FSSAI; where this page and the portal "
               "disagree, follow the portal.")


def _help_button(user):
    p = provider_of(user)
    if not p:
        return
    tag = f"(helper #{p['id']})"
    pending = q("SELECT 1 FROM notifications WHERE kind=? AND status='Queued' AND body LIKE ?",
                ("FSSAI help", f"%{tag}%"))
    if pending:
        st.markdown("<span class='pill ok'>Asked ✓</span> <span class='muted'>The GharSe team has your "
                    "request and will call you to fix a time.</span>", unsafe_allow_html=True)
        return
    if st.button("Ask GharSe to help me apply", type="primary", key="fg_help"):
        body = (f"FSSAI help requested by {p.get('owner_name') or p.get('display_name') or 'a helper'} "
                f"{tag}, {p.get('display_name') or ''}, {p.get('area') or 'area not set'}, "
                f"phone {p.get('phone') or 'not on file'}. Please call and sit with her to fill the "
                f"FoSCoS application.")
        notify("", "GharSe team", "admin", "FSSAI help", body)
        st.session_state["fg_flash"] = ("Done. The GharSe team will call you on the number in your "
                                        "profile to fix a time.")
        st.rerun()


# ----------------------------------------------------------------------------- provider section
_NEXT_STEP = {
    "none": "Start with the FSSAI guide: most home kitchens need Basic registration, about ₹100 a year, "
            "applied for online. Or ask GharSe to sit with you and fill it.",
    "applied": "Waiting for FSSAI. Keep your ARN handy; when the certificate comes, choose "
               "\"I have my certificate\" below and upload it.",
}


def provider_fssai_section(user):
    p = provider_of(user)
    if not p:
        st.warning("We couldn't find your helper profile. Please sign out and sign in again.")
        return
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
                           "the new one here so your listings don't pause.")
        else:
            st.markdown(f"**What's next:** {h(_NEXT_STEP.get(status) or reason)}", unsafe_allow_html=True)
        if p.get("verify_note") and not p.get("verified"):
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
        expiry = c[1].date_input("Valid until (expiry date on the certificate)", value=exp_now,
                                 min_value=date(2000, 1, 1), max_value=date(2100, 12, 31),
                                 format="DD/MM/YYYY", key="fssai_expiry")
        upload = st.file_uploader(
            f"Certificate: a clear photo or the PDF from FoSCoS (up to {MAX_IMG_MB} MB)"
            + (" — leave empty to keep the one you uploaded" if p.get("fssai_cert") else ""),
            type=list(_IMG_TYPES) + ["pdf"], key="fssai_upload")
        if p.get("verified"):
            st.warning("You're verified. If you change your FSSAI number or upload a new certificate, "
                       "GharSe has to check it again: your verification pauses and food listings go "
                       "offline until an admin approves it, usually within a day.")
        submitted = st.form_submit_button("Save FSSAI details", type="primary", width="stretch")

    if submitted:
        _save_provider(p, new_status, kind, arn, number, expiry, upload)


def _read_upload(upload):
    """(bytes, mime, error)."""
    if upload is None:
        return None, None, None
    if upload.size > _MAX_BYTES:
        return None, None, f"The certificate file is too big. Please keep it under {MAX_IMG_MB} MB."
    name = (upload.name or "").lower()
    raw = upload.getvalue()
    if name.endswith(".pdf") or upload.type == "application/pdf":
        if not raw.startswith(b"%PDF"):
            return None, None, "That file doesn't look like a PDF. Please upload the PDF from FoSCoS or a photo."
        return raw, "application/pdf", None
    data = process_image(upload)
    if not data:
        return None, None, "That image couldn't be read. Please try another photo."
    mime = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else (upload.type or "image/png")
    return data, mime, None


def _save_provider(p, status, kind, arn, number, expiry, upload):
    errors = []
    number = normalize_fssai_no(number)
    arn = (arn or "").strip()
    if number and not FSSAI_NO_RE.match(number):
        errors.append("The FSSAI number should be exactly 14 digits (spaces are fine, letters are not).")
    if expiry is not None and expiry < today_ist():
        errors.append(f"That expiry date, {expiry:%d %b %Y}, has already passed. Renew the certificate on "
                      "FoSCoS first, then enter the new date.")
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
        if data is None and not p.get("fssai_cert"):
            errors.append("Upload a photo or PDF of your certificate.")
    if errors:
        for e in errors:
            st.error(e)
        return

    number_changed = number != normalize_fssai_no(p.get("fssai_no"))
    cert_changed = data is not None
    recheck = bool(p.get("verified")) and (number_changed or cert_changed)

    sets = ["fssai_status=?", "fssai_kind=?", "fssai_arn=?", "fssai_no=?", "fssai_expiry=?"]
    params = [status, kind, arn or None, number or None, expiry.isoformat() if expiry else None]
    if cert_changed:
        sets += ["fssai_cert=?", "fssai_cert_mime=?"]
        params += [data, mime]
    if recheck:
        sets += ["verified=?", "verify_note=?"]
        params += [0, f"FSSAI details changed on {today_ist():%d %b %Y}; waiting for GharSe to re-check."]
    execute(f"UPDATE providers SET {', '.join(sets)} WHERE id=?", params + [p["id"]])

    if recheck:
        msg = ("Saved. Because your FSSAI details changed, GharSe will check them again; food listings "
               "are paused until then.")
    elif status == "registered" and not p.get("verified"):
        msg = "Saved. GharSe will check your certificate and let you know on WhatsApp."
    else:
        msg = "Saved."
    st.session_state["fssai_flash"] = msg
    st.rerun()


# ----------------------------------------------------------------------------- admin panel
def admin_fssai_panel(provider):
    p = dict(provider or {})
    if not p.get("id"):
        return
    pid = p["id"]
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
    if num and not FSSAI_NO_RE.match(num):
        st.warning("This number is not 14 digits. Ask her to check the certificate.")

    exp = _expiry(p)
    if exp:
        days = (exp - today_ist()).days
        if days < 0:
            st.error(f"Certificate expired {-days} days ago. Food listings are blocked until a renewed one is uploaded.")
        elif days <= 30:
            st.warning(f"Certificate expires in {days} days ({exp:%d %b %Y}). Remind her to renew on FoSCoS.")

    c = st.columns([2, 1])
    with c[0]:
        _show_certificate(p, key=f"fssai_dl{pid}")
    with c[1]:
        st.link_button("Verify number on FoSCoS", FOSCOS_HOME, width="stretch",
                       key=f"fssai_vfy{pid}")
        st.caption("Use the licence / registration (FBO) search there and compare the name and address "
                   "with the certificate.")

    missing = food_block_reason({**p, "verified": 1})
    if missing:
        st.caption(f"Still missing for food: {missing}")
    if p.get("verify_note"):
        st.caption(f"Last note: {p['verify_note']}")

    with st.form(f"fssai_adm{pid}"):
        reason = st.text_input("Reason, if sending back (she will see this)", key=f"fssai_reason{pid}",
                               placeholder="e.g. The name on the certificate doesn't match your profile")
        b = st.columns(2)
        approve = b[0].form_submit_button("Approve", type="primary", width="stretch")
        send_back = b[1].form_submit_button("Send back", width="stretch")

    if approve:
        note = f"FSSAI approved on {today_ist():%d %b %Y}."
        execute("UPDATE providers SET verified=1, verify_note=? WHERE id=?", (note, pid))
        fresh = dict(q("SELECT * FROM providers WHERE id=?", (pid,))[0])
        left = food_block_reason(fresh)
        if left is None:
            body = ("Good news: GharSe has checked your FSSAI certificate and approved it. "
                    "Your food listings and meal plans can go live now.")
        else:
            body = f"GharSe has verified your kitchen. To start selling food: {left}"
        notify_provider(pid, "FSSAI approved", body)
        st.session_state[f"fssai_adm_flash{pid}"] = "Approved. She has been notified."
        st.rerun()
    if send_back:
        reason = (reason or "").strip()
        if not reason:
            st.error("Write a reason so she knows what to fix.")
            return
        execute("UPDATE providers SET verified=0, fssai_status='applied', verify_note=? WHERE id=?",
                (reason, pid))
        notify_provider(pid, "FSSAI sent back",
                        f"GharSe couldn't approve your FSSAI details yet: {reason} "
                        "Please open My Profile → FSSAI, fix it and save; we'll check again.")
        st.session_state[f"fssai_adm_flash{pid}"] = "Sent back. She has been notified with your reason."
        st.rerun()
