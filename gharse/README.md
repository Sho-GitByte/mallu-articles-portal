# GharSe

**Earn from home. Buy from home.**

A hyperlocal marketplace where verified women home entrepreneurs sell home-made food, handmade
products and home-based services to their own neighbourhood. Food is the wedge, because food is
daily.

Built for a first pilot in HSR Layout, Bengaluru: PG residents and office workers who want
home-cooked lunch, and women a kilometre away who already know how to cook it.

---

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens on http://localhost:8501 against a local SQLite file. First sign-in is `admin` with the
demo password — the page tells you so, and keeps telling you until you set a real one
(see [DEPLOY.md](DEPLOY.md)). Home entrepreneurs and customers register themselves.

The app starts **empty**. The only rows it creates are the admin login and the fee defaults;
there is no sample content, because a marketplace that looks busy when it isn't teaches you
nothing.

### Tests

```bash
python smoke_test.py         # every page, every role, and the whole money path
python test_sql_dialects.py  # the SQL is valid for both SQLite and Postgres
```

`smoke_test.py` renders all 21 pages for all three roles against seeded data, then walks an
order through place → pay → confirm → deliver → payout and asserts the escrow rule holds.
It prints `FAILURES: 0` when it is happy. CI runs both on every push.

---

## Roles and pages

| Role | Pages |
|------|-------|
| **Admin** (the platform) | Dashboard · Home Entrepreneurs · Listings · Price Guidance · Orders · Payouts · Subscriptions · Custom Requests · WhatsApp Outbox · Fees & Settings |
| **Provider** (a home entrepreneur) | My Profile & Verification · My Listings · Orders Received · Meal Plans & Subscribers · Open Requests · My Earnings |
| **Customer** | Discover Near Me · My Orders · My Subscriptions · Post a Request · My Preferences |

---

## How it works

**The money.** Commission is 10%, itemised on every order as **8% platform** (onboarding,
verification, the app) plus **2% processing & support** (gateway charges, coordination). Both
lines are shown to both sides, always. On an ₹85 meal to a pickup point the customer pays ₹90,
the cook receives ₹76.50, the platform ₹8.50, delivery ₹5. Rates are editable under
*Fees & Settings* and computed in exactly one function, `split_money`.

A home cook prices at roughly 1.6× her cost, so a 30% commission would take about three
quarters of her profit and she would — correctly — quit. Ten percent is not generosity; it is
the rate at which supply survives.

**Payments, without a gateway account.** The customer pays the platform UPI ID and submits the
reference number; admin confirms it under *Orders*; the cook's share is released under
*Payouts*. Swap in a real gateway later without touching the split or the ledger.

**The escrow rule.** A cook's payout is releasable only when the order is **both delivered and
paid for**. One function, `payout_due`, and the smoke test fails if it ever loosens.

**FSSAI gate.** A food listing cannot go live unless the provider is admin-verified *and* has an
FSSAI number on file. Products and services publish immediately. Helping each woman through that
registration is the onboarding pitch, not overhead.

**Price guidance, not haggling.** Admin sets a fair range per category; a seller sees it while
she is typing her price and is warned when she goes outside it — or, more importantly, below her
own cost. Nothing is blocked. Quoting stays where price is genuinely bespoke: custom, bulk and
festival orders.

**WhatsApp.** Nine events message someone — new order, payment confirmed, every status change,
cancellation, new quote, quote accepted, new subscription, payout sent. With Cloud API
credentials they send themselves; without them they queue in the Outbox behind a `wa.me` link,
one tap each.

---

## Code

Two files, on purpose.

- **`app.py`** — the whole UI. Streamlit, role-based, one function per page.
- **`store.py`** — everything underneath: schema, migrations, connection handling, password
  hashing, India-correct time. It speaks **SQLite locally and Postgres in production**, chosen
  at runtime by whether a database URL is configured, so the same code runs in both places.

The app writes plain SQL with `?` placeholders and never imports a database driver.
`test_sql_dialects.py` parses every statement the project can emit under both dialects, so a
sqlite-only construct cannot quietly break production.

Passwords are salted PBKDF2-SHA256. Hashes from the first release are still accepted and are
upgraded the next time their owner signs in.

Times are Asia/Kolkata throughout — "available today" has to mean today in Bengaluru, not in UTC.

---

## Deploying

[DEPLOY.md](DEPLOY.md) — empty repo to live URL, and why SQLite is not acceptable for real use
on Streamlit Community Cloud.

## The thinking behind it

- [BLUEPRINT.md](BLUEPRINT.md) — the business case: three-phase rollout, unit economics, the
  delivery arithmetic that decides whether this works at all, revenue streams, risks.
- [PILOT_KIT.md](PILOT_KIT.md) — how to run the first neighbourhood: cook screening and
  onboarding, the FSSAI walkthrough, the pricing sheet, hygiene basics, the PG pitch, the daily
  WhatsApp/UPI rhythm, and the one metric that decides it (meals per customer per month).
