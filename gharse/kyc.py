# kyc.py — "Verify Identity": how GharSe knows the person behind an account is real.
#
# People meet in each other's homes and at events through this app, so the founder
# wants nobody to be able to cheat. This page does what an app like this can honestly
# and lawfully do today:
#
#   1. Phone OTP     — a 6-digit code from the secrets module, sent by SMS (MSG91 or
#                      Twilio over plain HTTPS). Only a salted PBKDF2 hash of it is stored
#                      (checked in constant time by store.verify_pw); it
#                      lasts 5 minutes, allows 5 tries, and a person can ask for at most
#                      5 codes an hour, one every 30 seconds.
#   2. Consent       — a plain notice under the Digital Personal Data Protection Act, 2023,
#                      and a tick that is recorded.
#   3. Details       — name as on the ID, date of birth (18+), the LAST FOUR digits of
#                      Aadhaar only, and PAN stored MASKED only ("AB*****34F"). The full
#                      Aadhaar number, a photo of the card, and the full PAN are never
#                      asked for in a way that is kept: Aadhaar rules forbid an app like
#                      this from holding them, and we don't need them.
#   4. Selfie        — taken live with the camera, kept only until the team decides.
#   5. Submit        — the GharSe team checks it by hand ("Phone + photo verified").
#
# "ID verified" is reserved for a licensed verification vendor (DigiLocker, IDfy,
# HyperVerge, Signzy, Digio). None is integrated yet; start_vendor_kyc() says so plainly
# and never fakes a result.
#
# The KYC Queue (admin) approves or rejects — atomically, only from "pending" — and the
# selfie is deleted the moment a decision is made.
#
# Privacy: no KYC field of one person is ever shown to another non-admin person. Other
# pages only ever get is_verified(), badge() and age_years() — and age_years() only
# counts a date of birth once the identity is verified.
#
# Phone binding: users.phone can be edited outside this module. A proof row in otp_codes
# records which number passed OTP, and reconcile_phones() switches phone_verified off
# when the account's number no longer matches it (see PROOF_PURPOSE).
#
# Tables: users (read/write), otp_codes (read/write), notifications (via ui.notify).

import re
import secrets
from datetime import date, datetime, timedelta

import streamlit as st

import store
from store import execute, hash_pw, now_iso, now_ist, q, today_ist, verify_pw
from ui import MAX_IMG_MB, h, notify, process_image, safe_image, short_dt, table

KYC_PAGE_TITLE = "Verify Identity"
ADMIN_PAGE_TITLE = "KYC Queue"

LEVEL_MANUAL = "Phone + photo verified"
LEVEL_VENDOR = "ID verified"
LEVEL_NONE = "Not verified"

MIN_AGE = 18

# OTP rules.
OTP_PURPOSE = "phone"
OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
OTP_MAX_PER_HOUR = 5                  # per account
OTP_MAX_PER_DAY = 10                  # per account
# Per number. One account can only use part of a number's allowance, so a stranger
# can't lock the real owner out (or flood them with SMS) from a single account.
PHONE_MAX_PER_HOUR = 6
PHONE_MAX_PER_DAY = 12
PAIR_MAX_PER_HOUR = 3                 # one account asking for one number
PAIR_MAX_PER_DAY = 5
OTP_RESEND_GAP = timedelta(seconds=30)
OTP_KEEP = timedelta(days=1)          # hashes older than this are of no use to anyone
# When a number is proved by OTP we keep a small "proof" row in otp_codes (purpose
# phone_proof, no code in it) that records WHICH number was proved. users.phone can be
# edited elsewhere (My Profile); a phone_verified flag whose number no longer matches
# its proof is switched off by reconcile_phones(), so a verified badge can never vouch
# for a number nobody proved.
PROOF_PURPOSE = "phone_proof"
_RECONCILE_EVERY = 10                 # seconds; how often is_verified() re-checks
# A 6-digit code has only a million values, so the hash is bound to the account and the
# number and made slow; with 5 tries and 5 minutes it is never the weak point.
OTP_HASH_ITERATIONS = 60_000

VENDORS = {"digilocker": "DigiLocker", "idfy": "IDfy", "hyperverge": "HyperVerge",
           "signzy": "Signzy", "digio": "Digio"}

_PAN_RE = re.compile(r"^[A-Z]{3}P[A-Z][0-9]{4}[A-Z]$")
_PAN_ANY_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,79}$")


# ----------------------------------------------------------------------------- pure helpers
def normalize_phone(raw):
    """A 10-digit Indian mobile number, or None. Accepts +91, 91, a leading 0, spaces
    and dashes; mobile numbers start with 6, 7, 8 or 9."""
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if re.fullmatch(r"[6-9][0-9]{9}", d) else None


def clean_pan(raw):
    return re.sub(r"[\s\-]", "", str(raw or "")).upper()


def pan_problem(raw):
    """None if this is a valid individual's PAN, else what is wrong with it."""
    pan = clean_pan(raw)
    if not pan:
        return "Enter your PAN."
    if not _PAN_ANY_RE.match(pan):
        return "A PAN is 10 characters: 5 letters, 4 digits, then 1 letter (like ABCPE1234F)."
    if not _PAN_RE.match(pan):
        return ("The 4th letter of a personal PAN is P. This one belongs to a company, firm or trust — "
                "please use your own PAN.")
    return None


def mask_pan(raw):
    """'ABCPE1234F' -> 'AB*****34F'. The only form of a PAN this app ever stores."""
    pan = clean_pan(raw)
    return pan[:2] + "*" * 5 + pan[-3:]


def _as_date(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


DOB_MIN = date(1920, 1, 1)


def _plausible_dob(v):
    """A stored date of birth only if it is a real date between 1920 and today."""
    d = _as_date(v)
    return d if d and DOB_MIN <= d <= today_ist() else None


def _years_between(born, today):
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _get(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def age_years(user_or_row, verified_only=True):
    """Age from users.dob, or None when unknown.

    By default a date of birth only counts once the identity is verified: before that it
    is just something typed in step 3 that nobody has checked, and an age gate must not
    trust it. KYC's own screens pass verified_only=False."""
    if verified_only and not is_verified(user_or_row):
        return None
    born = _plausible_dob(_get(user_or_row, "dob"))
    if not born:
        return None
    return _years_between(born, today_ist())


_last_reconcile = [0.0]


def _maybe_reconcile():
    import time
    if time.monotonic() - _last_reconcile[0] < _RECONCILE_EVERY:
        return
    _last_reconcile[0] = time.monotonic()
    try:
        reconcile_phones()
    except Exception:  # noqa: BLE001 — a badge must never break a page
        pass


def is_verified(user_or_row):
    """Verified means the team (or, later, a vendor) has approved the identity AND the
    phone number has been proved by OTP."""
    _maybe_reconcile()
    return (_get(user_or_row, "kyc_status") == "verified"
            and bool(_get(user_or_row, "phone_verified")))


def level(user_or_row):
    if not is_verified(user_or_row):
        return LEVEL_NONE
    method = (_get(user_or_row, "kyc_method") or "manual").lower()
    return LEVEL_MANUAL if method == "manual" else LEVEL_VENDOR


def badge(user_or_row):
    lv = level(user_or_row)
    cls = "pill flat" if lv == LEVEL_NONE else "pill ok"
    return f"<span class='{cls}'>{h(lv)}</span>"


def _name_words(v):
    return [w for w in re.split(r"[^a-z]+", str(v or "").lower()) if w]


def name_matches(full_name, kyc_name):
    """Does the display name still fit the name the team checked on the ID? Every word of
    the display name must be a word (or the initial of a word) of the ID name. An empty
    display name matches; the app then shows the username instead."""
    shown, on_id = _name_words(full_name), _name_words(kyc_name)
    if not shown:
        return not str(full_name or "").strip()   # a name in another script can't be compared
    if not on_id:
        return False
    initials = {w[0] for w in on_id}
    return all(w in on_id or (len(w) == 1 and w in initials) for w in shown)


def _fresh(user_id):
    rows = q("SELECT * FROM users WHERE id=?", (user_id,))
    return dict(rows[0]) if rows else None


def require(user, purpose):
    """True if this person is verified. Otherwise explains what they need, for what,
    and where to do it — and returns False. Reads the database, not the session, so a
    decision the team made a minute ago counts."""
    reconcile_phones()
    me = _fresh(_get(user, "id")) if _get(user, "id") is not None else None
    if me and is_verified(me):
        return True
    what = purpose or "do this"
    if me and me.get("kyc_status") == "pending":
        st.info(f"To {what} you need a verified identity. Yours is with the GharSe team now — "
                f"you'll get a message as soon as they've checked it.")
    else:
        st.info(f"To {what} you need to verify your identity first — a phone OTP, your details "
                f"and a selfie, checked by the GharSe team. Open **{KYC_PAGE_TITLE}** from the menu; "
                f"it takes about five minutes.")
    return False


def _parse_ts(v):
    try:
        t = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=store.IST)
    return t


def _sync_session(user_id, **fields):
    """Keep the signed-in session's copy in step with what was just written."""
    me = st.session_state.get("user")
    if isinstance(me, dict) and me.get("id") == user_id:
        me.update(fields)


# ----------------------------------------------------------------------------- SMS
def sms_provider():
    if store.secret("sms", "msg91_authkey") and store.secret("sms", "msg91_template_id"):
        return "msg91"
    if (store.secret("sms", "twilio_sid") and store.secret("sms", "twilio_token")
            and store.secret("sms", "twilio_from")):
        return "twilio"
    return None


def demo_allowed():
    return store.KIND == "sqlite" or bool(store.secret("kyc", "demo_otp"))


def _scrub(info, secret_text):
    info = str(info or "")[:200]
    return info.replace(secret_text, "******") if secret_text else info


def send_sms(phone, text, otp=None):
    """Send one SMS. Returns (sent, info). `info` never contains the code.

    MSG91: the DLT-registered OTP template is used, with our own code as its OTP
    variable (we keep checking it ourselves). Twilio: a plain message.
    Without either, nothing is sent: (False, "demo") when demo mode is allowed,
    otherwise (False, "not configured")."""
    num = normalize_phone(phone)
    if not num:
        return False, "not a valid Indian mobile number"
    provider = sms_provider()
    hide = otp or ""
    try:
        if provider == "msg91":
            import requests
            authkey = str(store.secret("sms", "msg91_authkey"))
            template = str(store.secret("sms", "msg91_template_id"))
            if otp:
                r = requests.post("https://control.msg91.com/api/v5/otp",
                                  params={"template_id": template, "mobile": "91" + num, "otp": otp},
                                  headers={"authkey": authkey, "accept": "application/json"},
                                  timeout=15)
            else:
                r = requests.post("https://control.msg91.com/api/v5/flow",
                                  json={"template_id": template, "short_url": "0",
                                        "recipients": [{"mobiles": "91" + num, "text": text}]},
                                  headers={"authkey": authkey, "accept": "application/json"},
                                  timeout=15)
            body = {}
            try:
                body = r.json()
            except ValueError:
                pass
            if r.status_code < 300 and str(body.get("type", "success")).lower() != "error":
                return True, "msg91"
            return False, _scrub(f"MSG91 HTTP {r.status_code}: {body.get('message') or r.text}", hide)
        if provider == "twilio":
            import requests
            sid = str(store.secret("sms", "twilio_sid"))
            r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                              data={"To": "+91" + num, "From": str(store.secret("sms", "twilio_from")),
                                    "Body": text},
                              auth=(sid, str(store.secret("sms", "twilio_token"))), timeout=15)
            if r.status_code < 300:
                return True, "twilio"
            msg = ""
            try:
                msg = r.json().get("message", "")
            except ValueError:
                msg = r.text
            return False, _scrub(f"Twilio HTTP {r.status_code}: {msg}", hide)
    except Exception as e:  # noqa: BLE001 — network down, bad credentials format …
        return False, _scrub(f"SMS failed: {type(e).__name__}", hide)
    return False, ("demo" if demo_allowed() else "not configured")


# ----------------------------------------------------------------------------- OTP
def _otp_material(user_id, phone, code):
    return f"{user_id}:{phone}:{code}"


def _recent_codes(user_id=None, phone=None, window=timedelta(hours=1)):
    since = now_ist() - window
    if user_id is not None:
        rows = q("SELECT * FROM otp_codes WHERE user_id=? AND purpose=? ORDER BY id DESC LIMIT 200",
                 (user_id, OTP_PURPOSE))
    else:
        rows = q("SELECT * FROM otp_codes WHERE phone=? AND purpose=? ORDER BY id DESC LIMIT 200",
                 (phone, OTP_PURPOSE))
    out = []
    for r in rows:
        t = _parse_ts(r["created_at"])
        if t and t >= since:
            out.append(dict(r))
    return out


def reconcile_phones():
    """Switch off phone_verified wherever the number on the account is no longer the
    number that was proved by OTP (for example it was edited in My Profile). Cheap: two
    statements over users. Safe to call on every page."""
    execute(
        "UPDATE users SET phone_verified=0, phone_verified_at=NULL WHERE phone_verified=1 AND "
        "EXISTS (SELECT 1 FROM otp_codes o WHERE o.user_id=users.id AND o.purpose=?) AND "
        "NOT EXISTS (SELECT 1 FROM otp_codes o WHERE o.user_id=users.id AND o.purpose=? AND o.phone=users.phone)",
        (PROOF_PURPOSE, PROOF_PURPOSE))
    # Accounts verified before proofs existed: record the number they have now, once.
    # (Every path in this module that removes a proof also switches the flag off, so
    # this never re-blesses an account whose proof was taken away.)
    execute(
        "INSERT INTO otp_codes (user_id, phone, code_hash, purpose, expires_at, attempts, used, created_at) "
        "SELECT id, phone, '', ?, NULL, 0, 1, ? FROM users WHERE phone_verified=1 AND phone IS NOT NULL AND "
        "NOT EXISTS (SELECT 1 FROM otp_codes o WHERE o.user_id=users.id AND o.purpose=?)",
        (PROOF_PURPOSE, now_iso(), PROOF_PURPOSE))
    _last_reconcile[0] = __import__("time").monotonic()


def _holds_proof(user_id, phone):
    return bool(q("SELECT 1 FROM otp_codes WHERE user_id=? AND purpose=? AND phone=?",
                  (user_id, PROOF_PURPOSE, phone)))


def active_code(user_id):
    """The code this person can type right now, or None."""
    rows = q("SELECT * FROM otp_codes WHERE user_id=? AND purpose=? AND used=0 ORDER BY id DESC LIMIT 1",
             (user_id, OTP_PURPOSE))
    if not rows:
        return None
    r = dict(rows[0])
    exp = _parse_ts(r["expires_at"])
    if not exp or exp <= now_ist() or (r["attempts"] or 0) >= OTP_MAX_ATTEMPTS:
        return None
    return r


def request_otp(user_id, raw_phone):
    """Make, store (hashed) and send a code. Returns a dict:
    ok, message, otp_id, demo_code (only in demo mode, to be shown on screen)."""
    phone = normalize_phone(raw_phone)
    if not phone:
        return {"ok": False, "message": "That doesn't look like a 10-digit Indian mobile number."}
    provider = sms_provider()
    if not provider and not demo_allowed():
        return {"ok": False, "message": "Phone verification is unavailable until the GharSe team sets up SMS."}
    # No "is this number already on another account?" answer here: that would tell any
    # stranger which numbers belong to GharSe members. verify_otp() enforces it, and only
    # someone who received the code on that number ever sees that message.
    me = _fresh(user_id) or {}
    if ((me.get("kyc_status") or "none") == "pending" and me.get("phone_verified")
            and normalize_phone(me.get("phone")) != phone):
        return {"ok": False, "message": "Your details are with the GharSe team, checked against your current "
                                        "number. You can change it once they've decided."}
    mine = _recent_codes(user_id=user_id, window=OTP_KEEP)
    mine_hour = _recent_codes(user_id=user_id)
    if mine_hour:
        last = _parse_ts(mine_hour[0]["created_at"])
        wait = (last + OTP_RESEND_GAP - now_ist()).total_seconds() if last else 0
        if wait > 0:
            return {"ok": False, "message": f"Please wait {int(wait) + 1} seconds before asking for another code."}
    if len(mine_hour) >= OTP_MAX_PER_HOUR:
        return {"ok": False, "message": f"That's {OTP_MAX_PER_HOUR} codes in the last hour — the most we send. "
                                        "Please try again in a little while."}
    if len(mine) >= OTP_MAX_PER_DAY:
        return {"ok": False, "message": f"That's {OTP_MAX_PER_DAY} codes today — the most we send to one account. "
                                        "Please try again tomorrow."}
    hour_ago = now_ist() - timedelta(hours=1)
    pair = [r for r in mine if r["phone"] == phone]
    pair_hour = [r for r in pair if (_parse_ts(r["created_at"]) or hour_ago) > hour_ago]
    if len(pair_hour) >= PAIR_MAX_PER_HOUR or len(pair) >= PAIR_MAX_PER_DAY:
        return {"ok": False, "message": "We've sent several codes to this number from your account already. "
                                        "Please wait a while before asking again."}
    # The account that last proved this number is never locked out of it by others.
    if not _holds_proof(user_id, phone):
        to_phone = _recent_codes(phone=phone, window=OTP_KEEP)
        to_phone_hour = [r for r in to_phone if (_parse_ts(r["created_at"]) or hour_ago) > hour_ago]
        if len(to_phone_hour) >= PHONE_MAX_PER_HOUR or len(to_phone) >= PHONE_MAX_PER_DAY:
            return {"ok": False, "message": "This number has been sent as many codes as we allow for now. "
                                            "Please try again later."}

    now = now_ist()
    # Old hashes are useless; earlier unused codes stop working the moment a new one is made.
    execute("DELETE FROM otp_codes WHERE purpose=? AND created_at < ?",
            (OTP_PURPOSE, (now - OTP_KEEP).isoformat(timespec="seconds")))
    execute("UPDATE otp_codes SET used=1 WHERE user_id=? AND purpose=? AND used=0", (user_id, OTP_PURPOSE))

    code = f"{secrets.randbelow(10 ** 6):06d}"
    code_hash = hash_pw(_otp_material(user_id, phone, code), iterations=OTP_HASH_ITERATIONS)
    otp_id = execute(
        "INSERT INTO otp_codes (user_id, phone, code_hash, purpose, expires_at, attempts, used, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (user_id, phone, code_hash, OTP_PURPOSE, (now + OTP_TTL).isoformat(timespec="seconds"), 0, 0,
         now.isoformat(timespec="seconds")),
    )
    text = f"{code} is your GharSe verification code. It expires in 5 minutes. Never share it with anyone."
    sent, info = send_sms(phone, text, otp=code)
    if sent:
        return {"ok": True, "message": f"Code sent to {phone}. It works for 5 minutes.", "otp_id": otp_id}
    if info == "demo":
        return {"ok": True, "message": "Demo code created.", "otp_id": otp_id, "demo_code": code}
    execute("UPDATE otp_codes SET used=1 WHERE id=?", (otp_id,))
    if info == "not configured":
        return {"ok": False, "message": "Phone verification is unavailable until the GharSe team sets up SMS."}
    return {"ok": False, "message": f"We couldn't send the SMS ({info}). Please try again in a minute."}


def verify_otp(user_id, raw_code):
    """Check a typed code. Returns (ok, message). On success the number is saved as the
    account's verified phone."""
    code = "".join(ch for ch in str(raw_code or "") if ch.isdigit())
    row = active_code(user_id)
    if not row:
        return False, "There's no live code for you — it may have expired. Ask for a new one."
    if len(code) != 6:
        return False, "The code is 6 digits."
    # Count the try first, atomically: two quick submits can't get a sixth guess.
    counted = execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=? AND used=0 AND attempts<? "
                      "RETURNING id", (row["id"], OTP_MAX_ATTEMPTS))
    if counted is None:
        return False, "This code is locked after 5 wrong tries. Ask for a new one."
    if not verify_pw(_otp_material(user_id, row["phone"], code), row["code_hash"]):
        left = OTP_MAX_ATTEMPTS - (row["attempts"] or 0) - 1
        if left <= 0:
            return False, "That code isn't right, and it's now locked. Ask for a new one."
        return False, f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left."
    if execute("UPDATE otp_codes SET used=1 WHERE id=? AND used=0 RETURNING id", (row["id"],)) is None:
        return False, "That code has already been used. Ask for a new one if you need to."
    phone, now = row["phone"], now_iso()
    reconcile_phones()
    before = _fresh(user_id) or {}
    # Another account "holds" this number only when it is their current phone, flagged
    # verified, AND matches their proof row. Our proof goes in first, so a reconcile
    # running in between never sees a verified number without one; it only counts once
    # users.phone matches it.
    proof_id = execute(
        "INSERT INTO otp_codes (user_id, phone, code_hash, purpose, expires_at, attempts, used, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)", (user_id, phone, "", PROOF_PURPOSE, None, 0, 1, now))
    done = execute(
        "UPDATE users SET phone=?, phone_verified=1, phone_verified_at=? WHERE id=? "
        # while the team is reviewing, the number they are looking at can't be swapped
        "AND (COALESCE(kyc_status, 'none') <> 'pending' OR phone_verified=0 OR phone=?) "
        "AND NOT EXISTS (SELECT 1 FROM (SELECT u2.id FROM users u2 WHERE u2.phone=? AND u2.phone_verified=1 "
        "AND u2.id<>? AND EXISTS (SELECT 1 FROM otp_codes o WHERE o.user_id=u2.id AND o.purpose=? "
        "AND o.phone=u2.phone)) taken) RETURNING id",
        (phone, now, user_id, phone, phone, user_id, PROOF_PURPOSE))
    if done is None:
        execute("DELETE FROM otp_codes WHERE id=?", (proof_id,))
        now_row = _fresh(user_id) or {}
        if (now_row.get("kyc_status") == "pending" and now_row.get("phone_verified")
                and normalize_phone(now_row.get("phone")) != phone):
            return False, ("Your details are with the GharSe team, checked against your current number. "
                           "You can change it once they've decided.")
        return False, ("This number is already verified on another GharSe account. If it's yours, write to "
                       "the team and we'll sort it out.")
    execute("DELETE FROM otp_codes WHERE user_id=? AND purpose=? AND id<>?", (user_id, PROOF_PURPOSE, proof_id))
    # Whoever else still had a proof for this number had moved off it (or it would have
    # blocked us above). This person has just shown they hold it, so those go.
    execute("UPDATE users SET phone_verified=0, phone_verified_at=NULL WHERE id<>? AND id IN "
            "(SELECT user_id FROM otp_codes WHERE purpose=? AND phone=?)", (user_id, PROOF_PURPOSE, phone))
    execute("DELETE FROM otp_codes WHERE purpose=? AND phone=? AND user_id<>?", (PROOF_PURPOSE, phone, user_id))
    old = normalize_phone(before.get("phone")) if before.get("phone_verified") else None
    if before.get("kyc_status") == "verified" and old and old != phone:
        line = f"Phone changed from {old} to {phone} on {now[:10]} (proved by OTP)."
        prev = (before.get("kyc_note") or "").strip()
        execute("UPDATE users SET kyc_note=? WHERE id=?", ((prev + " · " if prev else "") + line, user_id))
    _sync_session(user_id, phone=phone, phone_verified=1, phone_verified_at=now)
    return True, f"Your number {phone} is verified."


# ----------------------------------------------------------------------------- vendor
def start_vendor_kyc(user):
    """Hook for a licensed eKYC vendor. None is integrated yet, so this never returns a
    result — it says so instead of pretending."""
    vendor = str(store.secret("kyc", "vendor", default="") or "").strip()
    if not vendor:
        return
    name = VENDORS.get(vendor.lower().replace(" ", ""), vendor)
    if name in VENDORS.values():
        st.info(f"{name} is named as GharSe's verification partner, but it isn't connected to the app yet. "
                f"Until it is, the team checks your details by hand below, and your level is "
                f"\"{LEVEL_MANUAL}\".")
    else:
        st.info(f"The verification partner set up for GharSe (\"{name}\") isn't one this app knows how to use "
                f"yet. The team checks your details by hand below.")
    return


# ----------------------------------------------------------------------------- page bits
def _pill(text, kind=""):
    cls = f"pill {kind}".strip()
    return f"<span class='{cls}'>{h(text)}</span>"


def _flash(key):
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)


def _locked(me):
    return (me.get("kyc_status") or "none") in ("pending", "verified")


def _details_done(me):
    return bool(me.get("kyc_name") and me.get("dob") and me.get("aadhaar_last4") and me.get("pan_masked"))


def _steps(me):
    status = me.get("kyc_status") or "none"
    decided = status in ("verified",)
    submit = {"pending": ("With the team", "warn"), "verified": ("Verified", "ok"),
              "rejected": ("Sent back — fix and resend", "warn")}.get(status, ("To do", "flat"))
    return [
        ("1", "Phone number (OTP)", ("Done", "ok") if me.get("phone_verified") else ("To do", "flat")),
        ("2", "Your consent", ("Done", "ok") if me.get("kyc_consent_at") else ("To do", "flat")),
        ("3", "Details as on your ID", ("Done", "ok") if _details_done(me) else ("To do", "flat")),
        ("4", "A live selfie", ("Done", "ok") if (me.get("kyc_selfie") or decided or status == "pending")
         else ("To do", "flat")),
        ("5", "Team review", submit),
    ]


def _checklist(me):
    lines = "".join(
        f"<div style='display:flex;justify-content:space-between;gap:12px;padding:6px 0'>"
        f"<span><b>{n}.</b> {h(label)}</span>{_pill(text, kind)}</div>"
        for n, label, (text, kind) in _steps(me))
    with st.container(border=True):
        st.markdown(lines, unsafe_allow_html=True)


def _level_card(me):
    lv = level(me)
    with st.container(border=True):
        st.markdown(f"**Your level** &nbsp; {badge(me)}", unsafe_allow_html=True)
        if lv != LEVEL_NONE and not name_matches(me.get("full_name"), me.get("kyc_name")):
            st.warning("Your profile name has changed since the team verified you, so it no longer matches the "
                       "name they checked. They may get in touch; if it was a mistake, change it back in your "
                       "profile.")
        if lv == LEVEL_MANUAL:
            st.caption("Your phone is proved by OTP and the GharSe team has checked your details and a live "
                       "selfie by hand. You can host events, list rooms and do work in people's homes.")
        elif lv == LEVEL_VENDOR:
            st.caption("Your identity was confirmed through a licensed verification partner.")
        else:
            st.caption(f"\"{LEVEL_MANUAL}\" — phone OTP plus your details and a selfie checked by the team — "
                       f"is what GharSe offers today. \"{LEVEL_VENDOR}\" is reserved for checks through a "
                       f"licensed partner such as DigiLocker, which isn't connected yet.")


# ---- step 1
def _step_phone(me):
    with st.container(border=True):
        st.markdown("#### 1 · Your phone number")
        _flash("kyc_phone_flash")
        provider = sms_provider()
        if not provider and not demo_allowed():
            if me.get("phone_verified"):
                st.markdown(f"Verified: **{h(me.get('phone'))}** {_pill('Done', 'ok')}", unsafe_allow_html=True)
            st.warning("Phone verification is unavailable right now: the GharSe team hasn't set up SMS yet. "
                       "(In India, OTP messages can only be sent through a DLT-registered sender and template, "
                       "which the team is arranging.) Please check back soon.")
            return

        if me.get("phone_verified"):
            st.markdown(f"Verified: **{h(me.get('phone'))}** {_pill('Done', 'ok')}", unsafe_allow_html=True)
            if me.get("phone_verified_at"):
                st.caption(f"Proved by OTP on {h(short_dt(me['phone_verified_at']))}.")
            if (me.get("kyc_status") or "none") == "pending":
                st.caption("The team is checking your details against this number, so it can't be changed "
                           "until they've decided.")
            else:
                with st.expander("Use a different number"):
                    if me.get("kyc_status") == "verified":
                        st.caption("The new number is proved by OTP too, and the change is noted on your "
                                   "verification record for the team.")
                    _otp_flow(me, prefill="")
        else:
            st.caption("We send a 6-digit code to your mobile. It proves the number is really yours.")
            _otp_flow(me, prefill=normalize_phone(me.get("phone")) or "")
        if not provider and demo_allowed():
            st.caption("SMS is not configured on this copy of GharSe, so codes are shown on screen (demo mode).")


def _otp_flow(me, prefill):
    uid = me["id"]
    live = active_code(uid)
    with st.form("kyc_send_form"):
        raw = st.text_input("Mobile number", value=prefill, max_chars=16, placeholder="98XXXXXXXX",
                            key="kyc_phone_in")
        send = st.form_submit_button("Send code" if not live else "Send a new code", width="stretch")
    if send:
        res = request_otp(uid, raw)
        if res["ok"]:
            if res.get("demo_code"):
                st.session_state["kyc_demo_otp"] = {"id": res["otp_id"], "code": res["demo_code"]}
            else:
                st.session_state.pop("kyc_demo_otp", None)
                st.session_state["kyc_phone_flash"] = res["message"]
            st.rerun()
        else:
            st.error(res["message"])

    if not live:
        return
    demo = st.session_state.get("kyc_demo_otp")
    if demo and demo.get("id") == live["id"]:
        st.error(f"DEMO MODE — SMS IS NOT CONFIGURED. Your code is **{demo['code']}**. "
                 "Codes are being shown on screen because no SMS provider is set up. "
                 "This must never be used in production: anyone looking at this screen could verify "
                 "any number.")
    exp = _parse_ts(live["expires_at"])
    mins = max(0, int((exp - now_ist()).total_seconds() // 60)) if exp else 0
    st.caption(f"Code sent to {h(live['phone'])}. It expires in about {mins + 1} minute(s); "
               f"{OTP_MAX_ATTEMPTS - (live['attempts'] or 0)} tries left.")
    with st.form("kyc_verify_form", clear_on_submit=True):
        code = st.text_input("6-digit code", max_chars=6, key="kyc_code_in")
        go = st.form_submit_button("Verify", type="primary", width="stretch")
    if go:
        ok, msg = verify_otp(uid, code)
        if ok:
            st.session_state.pop("kyc_demo_otp", None)
            st.session_state["kyc_phone_flash"] = msg
            st.rerun()
        st.error(msg)


# ---- step 2
CONSENT_TEXT = """
**What we collect:** your phone number, your name as it appears on your ID, your date of birth,
the last 4 digits of your Aadhaar, your PAN in masked form only (like AB\\*\\*\\*\\*\\*34F), and a selfie.

**Why:** people meet in each other's homes and at events through GharSe. Knowing that every host,
helper and room owner is a real, adult person keeps everyone safer.

**How long:** the selfie is deleted as soon as the team has made a decision. The rest is kept
while your account is active.

**Who sees it:** only the GharSe team. Other members only ever see a badge — never your details.

**Your rights:** you can withdraw this consent and delete your verification data at any time, from
the bottom of this page. This is your right under the Digital Personal Data Protection Act, 2023.
"""


def _step_consent(me):
    with st.container(border=True):
        st.markdown("#### 2 · Your consent")
        st.markdown(CONSENT_TEXT)
        if me.get("kyc_consent_at"):
            st.markdown(f"{_pill('Given', 'ok')} <span class='muted'>on {h(short_dt(me['kyc_consent_at']))}</span>",
                        unsafe_allow_html=True)
            return
        with st.form("kyc_consent_form"):
            ok = st.checkbox("I have read this and I agree to GharSe processing these details to verify me.",
                             key="kyc_consent_tick")
            go = st.form_submit_button("Give consent", width="stretch")
        if go:
            if not ok:
                st.error("Please tick the box to give your consent — we can't continue without it.")
                return
            now = now_iso()
            execute("UPDATE users SET kyc_consent_at=? WHERE id=?", (now, me["id"]))
            _sync_session(me["id"], kyc_consent_at=now)
            st.session_state["kyc_flash"] = "Thank you — consent recorded."
            st.rerun()


# ---- step 3
def _step_details(me):
    with st.container(border=True):
        st.markdown("#### 3 · Details as on your ID")
        editing = st.session_state.get("kyc_edit") is True and not _locked(me)
        if _details_done(me) and not editing:
            rows = [("Name", me.get("kyc_name")), ("Date of birth", me.get("dob")),
                    ("Aadhaar", f"XXXX XXXX {me.get('aadhaar_last4')}"), ("PAN", me.get("pan_masked"))]
            st.markdown("<br>".join(f"<span class='muted'>{k}:</span> {h(v)}" for k, v in rows),
                        unsafe_allow_html=True)
            if not _locked(me) and st.button("Change these details", key="kyc_edit_btn"):
                st.session_state["kyc_edit"] = True
                st.rerun()
            return
        if _locked(me):
            return
        if not me.get("kyc_consent_at"):
            st.caption("Give your consent in step 2 first.")
            return
        st.info("GharSe never asks for — and never stores — your full Aadhaar number or a photo of your "
                "Aadhaar card. The law does not allow an app like this to keep them, and we don't need them. "
                "Only the last 4 digits. Your PAN is checked and then kept masked; the full PAN is not saved "
                "anywhere.")
        draft_name = st.session_state.get("kyc_draft_name") or me.get("kyc_name") or ""
        # Only a date inside the picker's range: a bad stored value (imported data, a clock
        # mistake) must not crash the page and lock the person out of deleting their data.
        draft_dob = _plausible_dob(st.session_state.get("kyc_draft_dob")) or _plausible_dob(me.get("dob"))
        today = today_ist()
        # clear_on_submit: the PAN and Aadhaar digits typed here do not linger in the session.
        with st.form("kyc_details_form", clear_on_submit=True):
            name = st.text_input("Full name exactly as on your ID", value=draft_name, max_chars=80,
                                 key="kyc_name_in")
            dob = st.date_input("Date of birth", value=draft_dob, min_value=DOB_MIN, max_value=today,
                                format="DD/MM/YYYY", key="kyc_dob_in")
            st.caption(f"You must be {MIN_AGE} or older to verify.")
            last4 = st.text_input("Last 4 digits of your Aadhaar", max_chars=4, type="password",
                                  key="kyc_aadhaar_in", placeholder="Only the last four")
            pan = st.text_input("PAN", max_chars=12, type="password", key="kyc_pan_in",
                                placeholder="ABCPE1234F")
            go = st.form_submit_button("Save details", type="primary", width="stretch")
        if not go:
            return
        name = re.sub(r"\s+", " ", (name or "").strip())
        st.session_state["kyc_draft_name"] = name
        st.session_state["kyc_draft_dob"] = dob.isoformat() if isinstance(dob, date) else None
        problems = []
        if not _NAME_RE.match(name):
            problems.append("Write your name in English letters as it appears on your ID.")
        if not isinstance(dob, date):
            problems.append("Enter your date of birth.")
        else:
            if not (DOB_MIN <= dob <= today):
                problems.append("Please check your date of birth.")
            elif _years_between(dob, today) < MIN_AGE:
                problems.append(f"You need to be {MIN_AGE} or older to verify on GharSe.")
        digits = "".join(ch for ch in (last4 or "") if ch.isdigit())
        if len(digits) != 4 or digits != (last4 or "").strip():
            problems.append("Enter only the last 4 digits of your Aadhaar — numbers only.")
        p = pan_problem(pan)
        if p:
            problems.append(p)
        if problems:
            for msg in problems:
                st.error(msg)
            st.caption("For your safety the Aadhaar and PAN boxes were cleared — please type them again.")
            return
        masked = mask_pan(pan)
        pan = None  # the full PAN goes no further than this line
        done = execute(
            "UPDATE users SET kyc_name=?, dob=?, aadhaar_last4=?, pan_masked=? WHERE id=? "
            "AND COALESCE(kyc_status, 'none') IN ('none', 'rejected') RETURNING id",
            (name, dob.isoformat(), digits, masked, me["id"]))
        if done is None:
            st.warning("Your details are with the team now, so they can't be changed until they've decided.")
            return
        _sync_session(me["id"], kyc_name=name, dob=dob.isoformat(), aadhaar_last4=digits, pan_masked=masked)
        for k in ("kyc_draft_name", "kyc_draft_dob", "kyc_edit"):
            st.session_state.pop(k, None)
        st.session_state["kyc_flash"] = "Details saved. Your PAN is stored only as " + masked + "."
        st.rerun()


# ---- step 4
def _step_selfie(me):
    with st.container(border=True):
        st.markdown("#### 4 · A live selfie")
        status = me.get("kyc_status") or "none"
        if status == "verified":
            st.caption("Your selfie was deleted after the team approved you.")
            return
        if status == "pending":
            st.caption("Your selfie is with the team. It will be deleted as soon as they decide.")
            return
        if not me.get("kyc_consent_at"):
            st.caption("Give your consent in step 2 first.")
            return
        st.caption("Face the camera in good light, without sunglasses or a mask. Only the GharSe team sees "
                   "this, and only to match it with you; it's deleted once they decide. Please don't "
                   "photograph your Aadhaar or PAN card — we don't want it.")
        if me.get("kyc_selfie"):
            safe_image(me["kyc_selfie"], caption="Your saved selfie", width=180)
            st.caption("Take a new one below if you'd like to replace it.")
        shot = st.camera_input("Take a selfie", key="kyc_cam")
        if shot is None:
            return
        raw = shot.getvalue() if hasattr(shot, "getvalue") else b""
        if len(raw) > MAX_IMG_MB * 1024 * 1024:
            st.error(f"That photo is larger than {MAX_IMG_MB} MB. Please take it again.")
            return
        img = process_image(shot)
        if img is None:
            st.error("That didn't come through as a photo. Please take it again.")
            return
        if st.button("Use this selfie", type="primary", key="kyc_use_selfie"):
            done = execute("UPDATE users SET kyc_selfie=? WHERE id=? "
                           "AND COALESCE(kyc_status, 'none') IN ('none', 'rejected') RETURNING id",
                           (img, me["id"]))
            if done is None:
                st.warning("Your verification is with the team now, so the selfie can't be changed.")
                return
            _sync_session(me["id"], kyc_selfie=img)
            st.session_state["kyc_flash"] = "Selfie saved."
            st.rerun()


# ---- step 5
def missing_for_submit(me):
    out = []
    if not me.get("phone_verified"):
        out.append("verify your phone number (step 1)")
    if not me.get("kyc_consent_at"):
        out.append("give your consent (step 2)")
    if not _details_done(me):
        out.append("save your details (step 3)")
    else:
        a = age_years(me, verified_only=False)
        if a is None:
            out.append("check your date of birth (step 3)")
        elif a < MIN_AGE:
            out.append(f"be {MIN_AGE} or older")
    if not me.get("kyc_selfie"):
        out.append("take a selfie (step 4)")
    return out


def submit(me):
    """Send for review. Returns (ok, message)."""
    missing = missing_for_submit(me)
    if missing:
        return False, "Before sending, please " + ", ".join(missing) + "."
    now = now_iso()
    done = execute(
        "UPDATE users SET kyc_status='pending', kyc_method='manual', kyc_submitted_at=?, kyc_note=NULL, "
        "kyc_reviewed_at=NULL, kyc_reviewed_by=NULL WHERE id=? AND COALESCE(kyc_status, 'none') IN "
        "('none', 'rejected') AND phone_verified=1 AND kyc_selfie IS NOT NULL AND kyc_consent_at IS NOT NULL "
        "RETURNING id", (now, me["id"]))
    if done is None:
        return False, "This was already sent, or something changed — the page has the latest."
    _sync_session(me["id"], kyc_status="pending", kyc_method="manual", kyc_submitted_at=now, kyc_note=None,
                  kyc_reviewed_at=None, kyc_reviewed_by=None)
    notify("", "GharSe team", "admin", "KYC submitted",
           f"Account #{me['id']} (@{me.get('username')}) has sent identity details for review. "
           f"Open {ADMIN_PAGE_TITLE} to check them.")
    return True, "Sent to the GharSe team. You'll get a message when they've checked it — usually within a day."


def _step_submit(me):
    with st.container(border=True):
        st.markdown("#### 5 · Team review")
        status = me.get("kyc_status") or "none"
        if status == "pending":
            st.markdown(f"{_pill('With the team', 'warn')} <span class='muted'>sent "
                        f"{h(short_dt(me.get('kyc_submitted_at')))}</span>", unsafe_allow_html=True)
            st.caption("A person on the GharSe team compares your selfie and details. You'll get a message "
                       "when they've decided.")
            return
        if status == "verified":
            st.markdown(f"{_pill('Verified', 'ok')} <span class='muted'>on "
                        f"{h(short_dt(me.get('kyc_reviewed_at')))}</span>", unsafe_allow_html=True)
            return
        if status == "rejected":
            st.warning(f"The team couldn't verify you yet. Their reason: {h(me.get('kyc_note') or '—')}  \n"
                       "Fix what they mention, take a fresh selfie, and send it again.")
        missing = missing_for_submit(me)
        if missing:
            st.caption("Still to do: " + ", ".join(missing) + ".")
        if st.button("Send for review", type="primary", key="kyc_submit", disabled=bool(missing),
                     width="stretch"):
            ok, msg = submit(_fresh(me["id"]) or me)
            if ok:
                st.session_state["kyc_flash"] = msg
                st.rerun()
            st.error(msg)


# ---- delete
def delete_verification_data(user_id):
    execute(
        "UPDATE users SET kyc_selfie=NULL, pan_masked=NULL, aadhaar_last4=NULL, kyc_name=NULL, dob=NULL, "
        "kyc_consent_at=NULL, kyc_status='none', kyc_method=NULL, kyc_ref=NULL, kyc_submitted_at=NULL, "
        "kyc_reviewed_at=NULL, kyc_reviewed_by=NULL, kyc_note=NULL WHERE id=?", (user_id,))
    _sync_session(user_id, kyc_selfie=None, pan_masked=None, aadhaar_last4=None, kyc_name=None, dob=None,
                  kyc_consent_at=None, kyc_status="none", kyc_method=None, kyc_ref=None, kyc_submitted_at=None,
                  kyc_reviewed_at=None, kyc_reviewed_by=None, kyc_note=None)


def _has_kyc_data(me):
    return any(me.get(k) for k in ("kyc_selfie", "pan_masked", "aadhaar_last4", "kyc_name", "dob",
                                   "kyc_consent_at")) or (me.get("kyc_status") or "none") != "none"


def _delete_section(me):
    if not _has_kyc_data(me):
        return
    with st.expander("Delete my verification data"):
        st.markdown("This deletes your selfie, your name as on ID, date of birth, Aadhaar last 4 digits, "
                    "masked PAN and your consent, and withdraws any request with the team. Your phone "
                    "verification stays.")
        if is_verified(me):
            st.warning("You will lose your verified badge, and anything that needs it — hosting events, "
                       "listing rooms, working in people's homes — until you verify again.")
        sure = st.checkbox("Yes, delete my verification data", key="kyc_del_ok")
        if st.button("Delete now", key="kyc_del_btn", disabled=not sure):
            delete_verification_data(me["id"])
            for k in ("kyc_draft_name", "kyc_draft_dob", "kyc_edit", "kyc_del_ok"):
                st.session_state.pop(k, None)
            st.session_state["kyc_flash"] = "Your verification data has been deleted."
            st.rerun()


# ----------------------------------------------------------------------------- page
def kyc_page(user):
    st.markdown(f"<div class='page-title'>{KYC_PAGE_TITLE}</div>"
                "<div class='page-sub'>A few honest checks, so everyone who meets through GharSe knows "
                "the person is real. About five minutes.</div>", unsafe_allow_html=True)
    reconcile_phones()
    me = _fresh(_get(user, "id")) if _get(user, "id") is not None else None
    if not me:
        st.error("Please sign in again.")
        return
    _sync_session(me["id"], phone=me.get("phone"), phone_verified=me.get("phone_verified"),
                  phone_verified_at=me.get("phone_verified_at"), kyc_status=me.get("kyc_status"))
    _flash("kyc_flash")
    _level_card(me)
    start_vendor_kyc(me)
    _checklist(me)
    _step_phone(me)
    _step_consent(me)
    _step_details(me)
    _step_selfie(me)
    _step_submit(me)
    _delete_section(me)


# ----------------------------------------------------------------------------- admin
def _account_age(created):
    t = _parse_ts(created)
    if not t:
        return "unknown"
    days = (now_ist() - t).days
    if days < 1:
        return "joined today"
    if days < 60:
        return f"{days} day{'s' if days != 1 else ''} old"
    return f"{days // 30} months old"


def approve(user_id, submitted_at, admin_user, note="", phone=None):
    """Atomic: only from 'pending', only the submission the admin looked at, and — when
    `phone` is given — only while the account still has that OTP-verified number."""
    now = now_iso()
    sql = ("UPDATE users SET kyc_status='verified', kyc_reviewed_at=?, kyc_reviewed_by=?, kyc_note=?, "
           "kyc_selfie=NULL WHERE id=? AND kyc_status='pending' AND kyc_submitted_at=? AND phone_verified=1")
    params = [now, _get(admin_user, "id"), (note or "").strip() or None, user_id, submitted_at]
    if phone is not None:
        sql += " AND phone=?"
        params.append(phone)
    return execute(sql + " RETURNING id", tuple(params)) is not None


def reject(user_id, submitted_at, admin_user, reason):
    now = now_iso()
    done = execute(
        "UPDATE users SET kyc_status='rejected', kyc_reviewed_at=?, kyc_reviewed_by=?, kyc_note=?, "
        "kyc_selfie=NULL WHERE id=? AND kyc_status='pending' AND kyc_submitted_at=? RETURNING id",
        (now, _get(admin_user, "id"), reason.strip(), user_id, submitted_at))
    return done is not None


def _person_notify(row, kind, body):
    notify(row.get("phone") or "", row.get("full_name") or row.get("username"), row.get("role") or "customer",
           kind, body)


def _review_card(r, admin_user):
    uid = r["id"]
    with st.container(border=True):
        c = st.columns([1, 2])
        with c[0]:
            if not safe_image(r.get("kyc_selfie"), caption="Live selfie", width="stretch"):
                st.caption("No selfie on file.")
            if r.get("photo"):
                safe_image(r["photo"], caption="Profile photo", width="stretch")
        with c[1]:
            age = age_years(r, verified_only=False)
            phone_line = (f"{h(r.get('phone'))} {_pill('OTP verified', 'ok')}" if r.get("phone_verified")
                          else f"{h(r.get('phone') or '—')} {_pill('Not verified', 'warn')}")
            rows = [
                ("Account", f"#{h(uid)} · @{h(r.get('username'))} · {h(_account_age(r.get('created_at')))}"),
                ("Profile name", h(r.get("full_name") or "—")),
                ("Name on ID", f"<b>{h(r.get('kyc_name'))}</b>"),
                ("Date of birth", f"{h(r.get('dob'))} ({h(age) if age is not None else '?'} years)"),
                ("PAN (masked)", h(r.get("pan_masked"))),
                ("Aadhaar last 4", h(r.get("aadhaar_last4"))),
                ("Phone", phone_line),
                ("Sent", h(short_dt(r.get("kyc_submitted_at")))),
            ]
            st.markdown("<br>".join(f"<span class='muted'>{k}:</span> {v}" for k, v in rows),
                        unsafe_allow_html=True)
            if age is not None and age < MIN_AGE:
                st.error(f"Under {MIN_AGE} by this date of birth. Do not approve.")
            if not r.get("phone_verified"):
                st.warning("The phone number isn't OTP-verified, so this can't be approved yet.")
            if not name_matches(r.get("full_name"), r.get("kyc_name")):
                st.warning("The profile name doesn't match the name on ID. Ask about it before approving.")
        st.caption("Check: the selfie is a real, live face; it matches the profile photo if there is one; the "
                   "name on ID looks real and matches the profile name; the age is 18+.")
        with st.form(f"kyc_rev{uid}"):
            note = st.text_input("Note for the record (optional)", key=f"kyc_note{uid}",
                                 placeholder="e.g. Met in person at HSR on 12 Oct")
            reason = st.text_input("Reason, if rejecting (they will see this)", key=f"kyc_reason{uid}",
                                   placeholder="e.g. The selfie is blurry — please retake it in daylight")
            b = st.columns(2)
            ok = b[0].form_submit_button("Approve", type="primary", width="stretch")
            no = b[1].form_submit_button("Reject", width="stretch")
        if ok:
            age_ok = age is not None and age >= MIN_AGE
            if not age_ok:
                st.error(f"Can't approve: the date of birth makes them under {MIN_AGE} or is missing.")
            elif approve(uid, r.get("kyc_submitted_at"), admin_user, note, phone=r.get("phone")):
                _person_notify(r, "KYC approved",
                               "Good news: GharSe has verified your identity. Your profile now shows "
                               f"\"{LEVEL_MANUAL}\". Your selfie has been deleted, as promised.")
                st.session_state["kyc_adm_flash"] = f"Approved @{h(r.get('username'))}. Selfie deleted."
                st.rerun()
            else:
                st.warning("This submission changed or was already decided. The list has the latest.")
        if no:
            reason = (reason or "").strip()
            if not reason:
                st.error("Write a reason so they know what to fix.")
            elif reject(uid, r.get("kyc_submitted_at"), admin_user, reason):
                _person_notify(r, "KYC sent back",
                               f"GharSe couldn't verify your identity yet: {reason} "
                               f"You can fix this and send it again from {KYC_PAGE_TITLE}. "
                               "Your selfie has been deleted.")
                st.session_state["kyc_adm_flash"] = f"Rejected @{h(r.get('username'))}. They've been told why."
                st.rerun()
            else:
                st.warning("This submission changed or was already decided. The list has the latest.")


def admin_kyc(admin_user):
    st.markdown(f"<div class='page-title'>{ADMIN_PAGE_TITLE}</div>"
                "<div class='page-sub'>Identity checks waiting for a person to look at them. Selfies are "
                "deleted the moment you decide.</div>", unsafe_allow_html=True)
    if _get(admin_user, "role") != "admin":
        st.error("Only the GharSe team can open this page.")
        return
    reconcile_phones()
    _flash("kyc_adm_flash")
    view = st.radio("Show", ["Pending", "Verified", "Rejected"], horizontal=True, key="kyc_adm_view")
    if view == "Pending":
        rows = [dict(r) for r in q(
            "SELECT * FROM users WHERE kyc_status='pending' ORDER BY kyc_submitted_at, id")]
        if not rows:
            st.info("Nothing waiting. New submissions appear here, oldest first.")
            return
        st.caption(f"{len(rows)} waiting, oldest first.")
        for r in rows:
            _review_card(r, admin_user)
        return
    status = "verified" if view == "Verified" else "rejected"
    rows = q(
        "SELECT u.id, u.username, u.kyc_name AS name_on_id, u.full_name AS profile_name, "
        "u.kyc_method AS method, u.phone_verified, "
        "u.kyc_reviewed_at AS reviewed_at, r.username AS reviewed_by, u.kyc_note AS note "
        "FROM users u LEFT JOIN users r ON r.id = u.kyc_reviewed_by "
        "WHERE u.kyc_status=? ORDER BY u.kyc_reviewed_at DESC, u.id DESC", (status,))
    out = []
    for r in rows:
        d = dict(r)
        d["phone_verified"] = bool(d["phone_verified"])
        d["name_check"] = ("matches ID" if name_matches(d.get("profile_name"), d.get("name_on_id"))
                           else "changed since check")
        out.append(d)
    flagged = sum(1 for d in out if d["name_check"] != "matches ID")
    if status == "verified" and flagged:
        st.warning(f"{flagged} verified account{'s' if flagged != 1 else ''} now show a profile name that "
                   "doesn't match the name on ID. Their badge still shows — check them.")
    table(out, cols=["id", "username", "name_on_id", "profile_name", "name_check", "method", "phone_verified",
                     "reviewed_at", "reviewed_by", "note"], empty=f"No {status} accounts yet.")

