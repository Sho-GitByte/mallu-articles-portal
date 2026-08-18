# GharSe — Neighbourhood Home Economy Marketplace

> Earn from home. Buy from home.

A hyperlocal marketplace where **verified women home entrepreneurs** sell home-made food, handmade
products and home-based services to their own neighbourhood. Food is the wedge, because food is
daily.

**This is a separate product from the Mallu Articles articleship portal.** It shares nothing with
it — its own app, its own database, its own dependencies, its own docs. It sits in this repository
only until it gets a repository of its own.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

First login: `admin` / `admin@123` — change it under **Fees & Settings**. Home entrepreneurs and
customers self-register. The app starts empty by design; the only seeded row is the admin login.

Run the tests:

```bash
python smoke_test.py
```

Renders all 21 pages for all three roles against seeded data, then walks an order through
place → pay → confirm → deliver → payout and asserts the escrow rule.

## Roles & pages

| Role | Pages |
|------|-------|
| **Admin** (platform) | Dashboard · Home Entrepreneurs · Listings · Price Guidance · Orders · Payouts · Subscriptions · Custom Requests · WhatsApp Outbox · Fees & Settings |
| **Provider** (home entrepreneur) | My Profile & Verification · My Listings · Orders Received · Meal Plans & Subscribers · Open Requests · My Earnings |
| **Customer** | Discover Near Me · My Orders · My Subscriptions · Post a Request · My Preferences |

## How it works

- **Food is gated.** A food listing cannot go live without admin verification *and* an FSSAI number
  on file. Products and services publish immediately.
- **Commission is 10%**, itemised on every order as 8% platform + 2% processing & support, and
  editable under *Fees & Settings*. Both lines are shown to both sides on every order.
- **Payments run on UPI with no gateway account.** The customer pays the platform UPI ID and
  submits the reference; admin confirms it under *Orders*; the cook's share is released under
  *Payouts* once the order is delivered. Escrow rule: payable only when delivered **and** paid.
- **WhatsApp messages queue in the Outbox** and send with one tap via `wa.me` links. Set
  `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_ID` (environment or `.streamlit/secrets.toml`) to send
  automatically through the Cloud API instead.
- **Price guidance, not haggling.** Admin sets a fair range per category; sellers see it while
  pricing and are warned when they go outside it — or below their own cost. Nothing is blocked.
- **Delivery modes are priced apart** so community pickup points are the cheap path, which is what
  makes the delivery arithmetic work at low density.

## Docs

- [`BLUEPRINT.md`](BLUEPRINT.md) — the business case: three-phase rollout, unit economics, the
  delivery math, revenue streams, the trust moat, the Bengaluru pilot and the risks.
- [`PILOT_KIT.md`](PILOT_KIT.md) — how to run the first neighbourhood: cook screening and
  onboarding, the FSSAI walkthrough, the pricing sheet, hygiene basics, the PG pitch, the daily
  WhatsApp/UPI rhythm, and what to measure.

## Tech

- Single-file **Streamlit** app (`app.py`), **SQLite** storage (`gharse.db`)
- Role-based login, SHA-256 password hashing
- Photos downscaled to 900px JPEG before storage (Pillow)
- Warm dark theme; tables and charts rendered as themed HTML

## ⚠️ Data persistence

SQLite on an ephemeral filesystem resets when the host sleeps or redeploys — fine for demos and an
early pilot. Move to hosted Postgres before real money and real kitchens depend on it; all DB
access goes through a thin `q()` / `execute()` layer, so that swap is contained.
