"""Smoke test: render every page of home_economy_app.py for every role, with real data."""
import os, sys, tempfile, importlib.util
from datetime import date, datetime, timedelta

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "home_economy_app.py")
work = tempfile.mkdtemp()
os.chdir(work)

spec = importlib.util.spec_from_file_location("hea", APP)
hea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hea)
hea.init_db()

now = datetime.utcnow().isoformat()
pid = hea.execute(
    "INSERT INTO providers (display_name, owner_name, phone, area, kinds, categories, cuisines, languages, "
    "diet, fssai_no, kyc_done, hygiene_done, verified, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,1,1,?)",
    ("Lakshmi Kitchen", "Lakshmi", "9876543210", "HSR Layout", "Food,Product", "Daily Meal (Veg),Flowers & Garlands",
     "South Indian", "Kannada,Tamil", "Vegetarian", "12345678901234", now))
hea.execute("INSERT INTO users (username, pw_hash, role, linked_id, created_at) VALUES (?,?,?,?,?)",
            ("lakshmi", hea.hash_pw("x"), "provider", pid, now))
cid = hea.execute(
    "INSERT INTO customers (full_name, phone, area, stay_type, pickup_point, diet, spice, budget_per_meal, created_at) "
    "VALUES (?,?,?,?,?,?,?,?,?)",
    ("Shoyab", "9000000000", "HSR Layout", "PG / Hostel", "PG reception", "Vegetarian", "Medium", 120, now))
hea.execute("INSERT INTO users (username, pw_hash, role, linked_id, created_at) VALUES (?,?,?,?,?)",
            ("shoyab", hea.hash_pw("x"), "customer", cid, now))
lid = hea.execute(
    "INSERT INTO listings (provider_id, kind, category, title, description, cuisine, diet, price, unit, "
    "market_price, avail_date, slot, capacity, sold, active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,?)",
    (pid, "Food", "Daily Meal (Veg)", "Rice · Sambar · Rasam · Poriyal · Curd", "Full South Indian meal",
     "South Indian", "Vegetarian", 85, "per meal", 160, date.today().isoformat(), hea.SLOTS[1], 20, now))
plid = hea.execute(
    "INSERT INTO plans (provider_id, title, description, slot, days, price, diet, active, created_at) "
    "VALUES (?,?,?,?,?,?,?,1,?)", (pid, "PG Lunch Plan", "Lunch, 26 days", hea.SLOTS[1], 26, 2600, "Vegetarian", now))
# photos: a real 1400px PNG must come back smaller, as JPEG, and render in the cards
import io
from PIL import Image
big = io.BytesIO()
Image.effect_noise((1400, 1000), 70).convert("RGB").save(big, format="PNG")  # noisy, like a real photo
raw = big.getvalue()

class _Upload:
    def __init__(self, b): self._b = b; self.size = len(b)
    def getvalue(self): return self._b

small = hea.process_image(_Upload(raw))
assert len(small) < len(raw), (len(raw), len(small))
assert Image.open(io.BytesIO(small)).size == (900, 643), Image.open(io.BytesIO(small)).size
hea.execute("UPDATE listings SET photo=? WHERE id=?", (small, lid))
hea.execute("UPDATE providers SET photo=? WHERE id=?", (small, pid))
print(f"photo pipeline ... ok  {len(raw) // 1024}KB PNG -> {len(small) // 1024}KB JPEG")

s = hea.split_money(85 * 2, "Community pickup point")
assert s["customer_total"] == 175.0, s
assert (s["platform_fee"], s["ops_fee"]) == (13.6, 3.4), s
assert s["provider_payout"] == 153.0, s
assert round(s["platform_fee"] + s["ops_fee"] + s["provider_payout"], 2) == s["item_total"], s
oid = hea.execute(
    "INSERT INTO orders (listing_id, provider_id, customer_id, qty, item_total, delivery_fee, platform_fee, "
    "ops_fee, provider_payout, customer_total, delivery_mode, note, status, for_date, slot, created_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'Delivered', ?,?,?)",
    (lid, pid, cid, 2, s["item_total"], s["delivery_fee"], s["platform_fee"], s["ops_fee"], s["provider_payout"],
     s["customer_total"], "Community pickup point", "less oil", date.today().isoformat(), hea.SLOTS[1], now))
hea.execute("UPDATE listings SET sold=2 WHERE id=?", (lid,))
hea.execute("INSERT INTO subscriptions (plan_id, provider_id, customer_id, start_date, days, price, delivery_mode, "
            "prefs, paused, status, created_at) VALUES (?,?,?,?,?,?,?,?,0,'Active',?)",
            (plid, pid, cid, date.today().isoformat(), 26, 2600, "Community pickup point", "Veg · Medium", now))
rid = hea.execute("INSERT INTO requests (customer_id, kind, category, title, description, area, budget, needed_by, "
                  "status, created_at) VALUES (?,?,?,?,?,?,?,?, 'Open', ?)",
                  (cid, "Product", "Flowers & Garlands", "50 jasmine garlands", "For a wedding", "HSR Layout",
                   2000, (date.today() + timedelta(days=2)).isoformat(), now))
hea.execute("INSERT INTO bids (request_id, provider_id, price, eta, note, status, created_at) "
            "VALUES (?,?,?,?,?, 'Offered', ?)", (rid, pid, 1800, "tomorrow 6 AM", "fresh from market", now))

# payments: platform UPI configured, seeded order paid for
hea.set_setting("platform_upi", "gharse@okaxis")
hea.execute("UPDATE providers SET upi_id=? WHERE id=?", ("lakshmi@okhdfcbank", pid))
hea.execute("UPDATE orders SET payment_status='Paid', payment_ref='UTR11223344' WHERE id=?", (oid,))
link = hea.upi_link(175.0, "GharSe order 1")
assert link.startswith("upi://pay?pa=gharse%40okaxis") and "am=175.00" in link and "cu=INR" in link, link
due, cnt = hea.payout_due(pid)
assert (due, cnt) == (153.0, 1), (due, cnt)
print("upi link ......... ok ", link[:58] + "…")

# price policy: guidance band + her own cost, applied by the app instead of by phone
hea.execute("INSERT INTO price_bands (category, kind, min_price, max_price, note, updated_at) "
            "VALUES (?,?,?,?,?,?)", ("Daily Meal (Veg)", "Food", 70, 110, "PG lunch range", now))
assert hea.price_verdict("Daily Meal (Veg)", 85)[0] == "ok"
assert hea.price_verdict("Daily Meal (Veg)", 60)[0] == "warn"          # under the band
assert hea.price_verdict("Daily Meal (Veg)", 200)[0] == "warn"         # over the band
assert hea.price_verdict("Daily Meal (Veg)", 45, 52)[0] == "bad"       # under her own cost
assert hea.price_verdict("Daily Meal (Veg)", 55, 52)[0] == "warn"      # margin too thin
assert hea.price_verdict("Tuition", 500)[0] is None                    # no band set
hea.execute("UPDATE listings SET cost_price=52 WHERE id=?", (lid,))
print("price policy ..... ok  cost check beats band check, both advisory")

from streamlit.testing.v1 import AppTest

PAGES = {
    "admin": ["Dashboard", "Home Entrepreneurs", "Listings", "Price Guidance", "Orders", "Payouts", "Subscriptions",
              "Custom Requests", "WhatsApp Outbox", "Fees & Settings"],
    "provider": ["My Profile & Verification", "My Listings", "Orders Received",
                 "Meal Plans & Subscribers", "Open Requests", "My Earnings"],
    "customer": ["Discover Near Me", "My Orders", "My Subscriptions", "Post a Request", "My Preferences"],
}
USERS = {r: dict(hea.q("SELECT * FROM users WHERE role=?", (r,))[0]) for r in PAGES}

at = AppTest.from_file(APP, default_timeout=30)
at.run()
assert not at.exception, at.exception
print("login page ....... ok")

fails = 0
for role, pages in PAGES.items():
    for page in pages:
        at = AppTest.from_file(APP, default_timeout=30)
        at.session_state["user"] = USERS[role]
        at.run()
        at.sidebar.radio[0].set_value(page).run()
        if at.exception:
            fails += 1
            print(f"{role:9} {page:32} FAIL\n{at.exception}")
        else:
            print(f"{role:9} {page:32} ok")

# exercise one real interaction: customer places an order from Discover
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user"] = USERS["customer"]
at.run()
before = hea.q("SELECT COUNT(*) c FROM orders")[0]["c"]
at.button(key=f"ob{lid}").click().run()
assert not at.exception, at.exception
after = hea.q("SELECT COUNT(*) c FROM orders")[0]["c"]
assert after == before + 1, (before, after)
o = dict(hea.q("SELECT * FROM orders ORDER BY id DESC")[0])
assert o["status"] == "Placed" and o["customer_total"] == 100.0, o
assert (o["platform_fee"], o["ops_fee"], o["provider_payout"]) == (6.8, 1.7, 76.5), dict(o)
assert hea.q("SELECT sold FROM listings WHERE id=?", (lid,))[0]["sold"] == 3
print("place order ...... ok  ->", o["customer_total"], "paid,", o["provider_payout"], "to the cook,",
      o["platform_fee"], "platform +", o["ops_fee"], "ops")

# provider advances that order
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user"] = USERS["provider"]
at.run()
at.sidebar.radio[0].set_value("Orders Received").run()
at.button(key=f"adv{o['id']}").click().run()
assert not at.exception, at.exception
assert dict(hea.q("SELECT * FROM orders WHERE id=?", (o["id"],))[0])["status"] == "Accepted"
print("advance order .... ok")

# money path: customer claims payment -> admin confirms -> escrow releases -> payout recorded
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user"] = USERS["customer"]
at.run()
at.sidebar.radio[0].set_value("My Orders").run()
at.text_input(key=f"utr{o['id']}").set_value("UTR99887766").run()
at.button[[b.label for b in at.button].index("I've paid")].click().run()
assert not at.exception, at.exception
paid = dict(hea.q("SELECT * FROM orders WHERE id=?", (o["id"],))[0])
assert paid["payment_status"] == "Claimed" and paid["payment_ref"] == "UTR99887766", paid
print("claim payment .... ok")

at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user"] = USERS["admin"]
at.run()
at.sidebar.radio[0].set_value("Orders").run()
at.button(key=f"conf{o['id']}").click().run()
assert not at.exception, at.exception
assert dict(hea.q("SELECT * FROM orders WHERE id=?", (o["id"],))[0])["payment_status"] == "Paid"
# still not delivered, so escrow must NOT release it yet
assert hea.payout_due(pid)[0] == 153.0, hea.payout_due(pid)
hea.execute("UPDATE orders SET status='Delivered' WHERE id=?", (o["id"],))
assert hea.payout_due(pid) == (229.5, 2), hea.payout_due(pid)
print("escrow ........... ok  held until delivered, then ₹229.50 across 2 orders")

at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user"] = USERS["admin"]
at.run()
at.sidebar.radio[0].set_value("Payouts").run()
at.text_input(key=f"r{pid}").set_value("UPI-PAYOUT-4471").run()
at.button[[b.label for b in at.button].index("Record payout")].click().run()
assert not at.exception, at.exception
assert hea.payout_due(pid)[0] == 0.0, hea.payout_due(pid)
po = dict(hea.q("SELECT * FROM payouts ORDER BY id DESC")[0])
assert po["amount"] == 229.5 and po["ref"] == "UPI-PAYOUT-4471", po
print("payout ........... ok  ₹229.50 recorded, nothing outstanding")

# notifications: queued by real events, addressed to the right party, sendable by link
msgs = [dict(m) for m in hea.q("SELECT * FROM notifications ORDER BY id")]
kinds = [m["kind"] for m in msgs]
for expected in ("new_order", "order_placed", "paid", "status", "payout"):
    assert expected in kinds, (expected, kinds)
assert all(m["status"] == "Queued" for m in msgs), "no credentials in test env, so nothing should auto-send"
assert all(m["error"] for m in msgs), "queued messages must record why they didn't send"
order_msg = next(m for m in msgs if m["kind"] == "new_order")
assert order_msg["to_role"] == "provider" and order_msg["to_phone"] == "9876543210", order_msg
assert hea.wa_number("9876543210") == "919876543210"
assert hea.wa_number("+91 98765 43210") == "919876543210"
assert hea.wa_number("123") == ""
assert hea.wa_link("9876543210", "hi there").startswith("https://wa.me/919876543210?text=hi%20there")
assert hea.wa_link("bad", "x") is None
sent_ok, err = hea.send_whatsapp("9876543210", "x")
assert sent_ok is False and "credentials" in err, err
print(f"whatsapp ......... ok  {len(msgs)} messages queued: {', '.join(sorted(set(kinds)))}")

at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user"] = USERS["admin"]
at.run()
at.sidebar.radio[0].set_value("WhatsApp Outbox").run()
assert not at.exception, at.exception
at.button(key=f"ms{msgs[0]['id']}").click().run()
assert dict(hea.q("SELECT * FROM notifications WHERE id=?", (msgs[0]["id"],))[0])["status"] == "Sent"
print("outbox ........... ok  marked one sent")

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
