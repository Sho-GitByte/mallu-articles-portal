# ui.py — shared helpers for GharSe's pages: formatting, escaping, themed tables and
# bars, photo handling, WhatsApp delivery, and the per-user record lookups. Every page
# module imports from here; none of them imports app.py, which keeps the dependency
# graph one-way (app -> pages -> ui -> store).

import re
from datetime import datetime
from html import escape as esc

import pandas as pd
import streamlit as st

from store import execute, now_iso, q, setting, today_ist

MAX_IMG_MB = 5


def idx(lst, val, default=0):
    return lst.index(val) if val in lst else default


def csv_join(items):
    return ", ".join(items) if items else ""


def csv_split(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def inr(n):
    n = int(round(n or 0))
    s = str(abs(n))
    if len(s) > 3:
        last3, rest = s[-3:], s[:-3]
        rest = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", rest)
        s = rest + "," + last3
    return ("-" if n < 0 else "") + "₹" + s


def mask_phone(p):
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    if len(d) < 4:
        return "•••• (shared after order)"
    return d[:2] + "•" * (len(d) - 4) + d[-2:]


# Streamlit renders Markdown even inside unsafe_allow_html markup, so escaping HTML
# alone left a stranger's job title free to become a live link ([text](url)), a remote
# tracking image (![](url)), or a bare auto-linked URL. Every character that means
# something to either HTML or Markdown is emitted as a character reference: it displays
# identically and is never read as syntax. One pass, so nothing is escaped twice.
_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#x27;"}
_ESCAPES.update({c: f"&#{ord(c)};" for c in "[]()!*_`#~|\\:.{}+-"})
_ESCAPE_TABLE = str.maketrans(_ESCAPES)


def h(v):
    """Escape a database value for unsafe_allow_html markup — as HTML *and* as Markdown.
    A kitchen called "Amma's" must not break the page, a review with a script tag must
    never run, and a job title must not turn into a link to somewhere else."""
    return ("" if v is None else str(v)).translate(_ESCAPE_TABLE)

def df(rows):
    return pd.DataFrame([dict(r) for r in rows])


def short_dt(v):
    try:
        return datetime.fromisoformat(str(v)).strftime("%d %b, %H:%M")
    except (TypeError, ValueError):
        return str(v)


def table(rows, cols=None, money=(), empty="Nothing yet."):
    """Render as HTML so the table follows this app's theme, not Streamlit's own."""
    from html import escape
    data = [dict(r) for r in rows]
    if not data:
        st.caption(empty)
        return
    cols = cols or list(data[0].keys())
    head = "".join(f"<th>{escape(c.replace('_', ' '))}</th>" for c in cols)
    body = ""
    for d in data:
        cells = ""
        for c in cols:
            v = d.get(c)
            if v is None or v == "":
                cells += "<td class='muted'>—</td>"
            elif c in money:
                cells += f"<td class='num'>{inr(v)}</td>"
            elif c.endswith("_at"):
                cells += f"<td>{escape(short_dt(v))}</td>"
            elif isinstance(v, bool):
                cells += f"<td>{'yes' if v else 'no'}</td>"
            elif isinstance(v, (int, float)):
                cells += f"<td class='num'>{v:g}</td>"
            else:
                cells += f"<td>{escape(str(v))}</td>"
        body += f"<tr>{cells}</tr>"
    st.markdown(f"<div class='tbl-wrap'><table class='tbl'><thead><tr>{head}</tr></thead>"
                f"<tbody>{body}</tbody></table></div>", unsafe_allow_html=True)


def hbar(rows, label_key, value_key, empty="No data yet."):
    from html import escape
    data = [dict(r) for r in rows]
    if not data:
        st.caption(empty)
        return
    top = max((d[value_key] or 0) for d in data) or 1
    html = ""
    for d in data:
        v = d[value_key] or 0
        html += (f"<div class='hb'><div class='hb-l'>{escape(str(d[label_key]))}</div>"
                 f"<div class='hb-t'><div class='hb-f' style='width:{100 * v / top:.1f}%'></div></div>"
                 f"<div class='hb-v'>{v:g}</div></div>")
    st.markdown(html, unsafe_allow_html=True)


# Phone cameras are big; anything past this is a decompression bomb, not a photo.
MAX_IMAGE_PIXELS = 40_000_000


def process_image(uploaded, max_px=900):
    """Return the upload as a downscaled JPEG, or None if it is not a readable image.

    It used to fall back to the raw bytes, which meant any file renamed .jpg was stored
    as a photo — and then crashed st.image on every later page load, with no way for
    the owner to remove it. A file that will not decode is now refused, never stored."""
    if uploaded is None:
        return None
    raw = uploaded.getvalue() if hasattr(uploaded, "getvalue") else bytes(uploaded)
    if not raw:
        return None
    try:
        import io
        import warnings
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(raw))
            if probe.width * probe.height > MAX_IMAGE_PIXELS:
                return None
            probe.verify()                      # catches truncated and corrupt files
            im = Image.open(io.BytesIO(raw))    # verify() leaves the image unusable
            im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        return buf.getvalue()
    except Exception:
        return None


def safe_image(data, caption=None, **kwargs):
    """st.image that cannot take the page down. Rows written before uploads were
    validated may hold bytes that are not an image; show a placeholder instead."""
    if not data:
        return False
    try:
        st.image(bytes(data), caption=caption, **kwargs)
        return True
    except Exception:
        st.caption("Photo couldn't be shown — upload it again.")
        return False


def photo_of(row, fallback=None):
    """Listing photo, else the kitchen's own photo, else nothing."""
    for candidate in (row.get("photo") if isinstance(row, dict) else None, fallback):
        if candidate:
            return bytes(candidate)
    return None


def today_iso():
    return today_ist().isoformat()


def kpi(col, label, value):
    col.markdown(
        f"<div class='kpi'><div class='k-label'>{label}</div><div class='k-val'>{value}</div></div>",
        unsafe_allow_html=True,
    )


def provider_of(user):
    rows = q("SELECT * FROM providers WHERE id=?", (user["linked_id"],))
    return dict(rows[0]) if rows else {}


def customer_of(user):
    rows = q("SELECT * FROM customers WHERE id=?", (user["linked_id"],))
    return dict(rows[0]) if rows else {}


# ----------------------------------------------------------------------------- WHATSAPP
# Two delivery paths. With Cloud API credentials in the environment, messages send
# themselves. Without them — which is where a pilot starts — every message is queued
# and sent by one click from the Outbox, which needs no Meta account at all.
def wa_number(phone):
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(d) == 10:
        d = "91" + d
    return d if len(d) >= 11 else ""

def wa_link(phone, body):
    from urllib.parse import quote
    n = wa_number(phone)
    return f"https://wa.me/{n}?text={quote(body)}" if n else None

def wa_credentials():
    import os
    tok, phone_id = os.environ.get("WHATSAPP_TOKEN"), os.environ.get("WHATSAPP_PHONE_ID")
    if not (tok and phone_id):
        try:
            tok = tok or st.secrets.get("WHATSAPP_TOKEN")
            phone_id = phone_id or st.secrets.get("WHATSAPP_PHONE_ID")
        except Exception:
            pass
    return tok, phone_id

def send_whatsapp(phone, body):
    """Returns (sent, error). Business-initiated messages outside the 24-hour customer
    service window need an approved template — the API error is stored, not hidden."""
    tok, phone_id = wa_credentials()
    n = wa_number(phone)
    if not (tok and phone_id):
        return False, "no API credentials — send from the Outbox"
    if not n:
        return False, "no usable phone number"
    try:
        import requests
        r = requests.post(
            f"https://graph.facebook.com/v20.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {tok}"},
            json={"messaging_product": "whatsapp", "to": n, "type": "text", "text": {"body": body}},
            timeout=15,
        )
        if r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)[:200]

def notify(phone, name, role, kind, body, order_id=None):
    now = now_iso()
    sent, err = send_whatsapp(phone, body)
    execute(
        "INSERT INTO notifications (to_phone, to_name, to_role, kind, body, order_id, status, error, "
        "sent_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (phone, name, role, kind, body, order_id, "Sent" if sent else "Queued", err,
         now if sent else None, now),
    )

def notify_provider(provider_id, kind, body, order_id=None):
    rows = q("SELECT display_name, phone FROM providers WHERE id=?", (provider_id,))
    if rows:
        notify(rows[0]["phone"], rows[0]["display_name"], "provider", kind, body, order_id)

def notify_customer(customer_id, kind, body, order_id=None):
    rows = q("SELECT full_name, phone FROM customers WHERE id=?", (customer_id,))
    if rows:
        notify(rows[0]["phone"], rows[0]["full_name"], "customer", kind, body, order_id)


def split_on_top(amount):
    """GharSe Social pricing: the platform fee is added ON TOP of what the host or owner
    asks, so they keep every rupee of their price and the guest pays the 10%. (Food is
    the other way round — split_money in app.py takes it out of the cook's price.) Both
    rates come from the same settings, and both are always shown itemised.

    A ₹399 ticket: host keeps ₹399, platform ₹31.92, processing ₹7.98, guest pays ₹438.90.
    """
    base = round(float(amount), 2)
    platform = round(base * setting("platform_pct", float) / 100.0, 2)
    ops = round(base * setting("ops_pct", float) / 100.0, 2)
    return {"base": base, "platform_fee": platform, "ops_fee": ops,
            "customer_total": round(base + platform + ops, 2), "host_payout": base}
