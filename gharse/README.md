# GharSe

**Earn from home. Buy from home.**

A hyperlocal marketplace where verified women home entrepreneurs sell home-made food, handmade
products and home-based services to their own neighbourhood. Food is the wedge, because food is
daily.

Built for a first pilot in HSR Layout, Bengaluru: PG residents and office workers who want
home-cooked lunch, and women a kilometre away who already know how to cook it.

Beside the marketplace sits **GharSe Social**: house parties, meetups, trips and rooms to rent,
between the same identity-checked neighbours, with the same login.

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

`smoke_test.py` renders every page for all three roles in both spaces (the list is read from
the app's own navigation, so it can never drift), then walks an order through place → pay →
confirm → deliver → payout and asserts the escrow rule holds, plus escaping, cancellation, the
free pickup default, one account switching between customer and helper, and a suspended account
being held at the door. It prints `FAILURES: 0` when it is happy. CI runs both on every push.

---

## Roles and pages

The sidebar opens on the **GharSe** marketplace; a switch at its top moves to **Social**.

| Role | GharSe (marketplace) | Social |
|------|----------------------|--------|
| **Admin** (the platform) | Dashboard · Home Entrepreneurs · Listings · Price Guidance · Orders · Payouts · Subscriptions · Asks · Learn & Earn · WhatsApp Outbox · FSSAI Guide · Fees & Settings · My Profile | KYC Queue · Reports · Events Admin · Rentals Admin · My Profile |
| **Helper** (a home entrepreneur) | My Kitchen · FSSAI Certificate · My Listings · Orders Received · Meal Plans & Subscribers · Neighbourhood Asks · Learn & Earn · My Earnings · FSSAI Guide · Verify Identity · My Profile | Events & Meetups · Rooms & PGs · Verify Identity · My Profile |
| **Customer** | Discover Near Me · My Orders · My Subscriptions · Ask the Neighbourhood · Learn & Earn · My Preferences · Verify Identity · My Profile | Events & Meetups · Rooms & PGs · Verify Identity · My Profile |

**One account can be both.** A customer can become a helper, and a helper can order from
neighbours, from a button in the sidebar — same login, same identity checks, same theme. The
sidebar shows which side you are using and switches between them.

---

## How it works

**The money.** Commission is 10%, itemised on every order as **8% platform** (onboarding,
verification, the app) plus **2% processing & support** (gateway charges, coordination). Both
lines are shown to both sides, always. On an ₹85 meal the customer pays ₹85, the cook receives
₹76.50 and the platform ₹8.50. Rates are editable under *Fees & Settings* and computed in exactly
one function, `split_money`.

**No delivery fee.** The helper lives a few minutes away, so by default the customer collects from
her home, free. That removes the one cost that made a home meal dearer than it had to be — a
rider. A pickup point (a PG reception, an office gate) and "she brings it to you" remain as
options, both free unless admin sets a fee. Services skip the question: nothing travels.

A home cook prices at roughly 1.6× her cost, so a 30% commission would take about three
quarters of her profit and she would — correctly — quit. Ten percent is not generosity; it is
the rate at which supply survives.

**Payments, without a gateway account.** The customer pays the platform UPI ID and submits the
reference number; admin confirms it under *Orders*; the cook's share is released under
*Payouts*. Swap in a real gateway later without touching the split or the ledger.

**The escrow rule.** A cook's payout is releasable only when the order is **both delivered and
paid for**. One function, `payout_due`, and the smoke test fails if it ever loosens.

**FSSAI gate.** A kitchen sells food only with an FSSAI certificate that admin has checked: a
valid 14-digit number, the certificate itself uploaded, and an expiry date still ahead. Changing
any of those sends it back for a re-check, and the moment a certificate lapses her food disappears
from search — including food listed while it was valid. The **FSSAI Guide** explains tiers, fees,
documents and the steps, with buttons to the official FoSCoS portal; the facts on it are marked
"confirm on the portal" until someone with network access checks them. Products and services
publish immediately.

**Price guidance, not haggling.** Admin sets a fair range per category; a seller sees it while
she is typing her price and is warned when she goes outside it — or, more importantly, below her
own cost. Nothing is blocked. Quoting stays where price is genuinely bespoke: custom, bulk and
festival orders.

**Ask the Neighbourhood.** Anyone can ask for anything a neighbour might do — nine groups and
62 kinds of ask, from tiffin and jasmine stringing to saree embroidery, tuition, babysitting, elder
care and pet care. Neighbours settle it with a fixed price, by offers, by exchange, or as free help.
Care asks carry a safety note the asker must acknowledge, and only a helper the team has verified
*and* whose identity and phone are verified can take them.

**Learn & Earn.** Neighbourhood jobs anyone can post or apply to, and skills to learn — each with
YouTube tutorials, videos the team vouches for, and the women nearby who teach it, your own area
first. Contact details are shared only once both sides accept, and teaching in someone's home
needs a verified identity.

**My Profile and 20 themes.** Everyone has a profile — details, photo, password — and chooses from
20 themes: five dark, five light, a rainbow Holi theme, four two-tone, four flower themes with
stickers (jasmine, marigold, lotus, hibiscus) and a black-and-white one. Every theme is tested for
readable contrast, light ones included.

**Identity verification.** *Verify Identity* has three steps: prove the phone number with a
one-time code, enter the details as on the ID, and take a live selfie that the team compares by
hand in the **KYC Queue**. It asks for consent first (DPDP Act 2023) and keeps as little as it
can: the **last four digits of Aadhaar only** — never the full number or a card image, which
UIDAI rules forbid a platform like this to hold — the PAN stored masked, and the selfie deleted
once the team has decided. Codes are stored hashed, expire in five minutes and are rate-limited;
they go by SMS through MSG91 or Twilio once configured. A licensed eKYC partner (DigiLocker,
IDfy, HyperVerge, Signzy, Digio) can be named in settings, but none is wired in yet and the app
says so rather than inventing a result. Verification is required to take care asks, teach in a
home, host or book a house party or trip, and list a room.

**GharSe Social.** Verified members host house parties, friends meets, plan & play, hangouts and
trips. A house party is ticketed — say ₹399 a seat, up to 50 guests, four hours. GharSe's 10% is
added **on top** for the guest (8% platform + 2% processing), so the host keeps her whole price:
a full ₹399 × 50 party pays her ₹19,950 and GharSe ₹1,995. Guests pay the platform UPI and claim
with the reference; only then do they see the address and the host's phone. Hosts are paid only
for confirmed seats, after the event has ended. Seat counts and payouts are single conditional
statements, so two people racing for the last seat cannot both get it and a host cannot be paid
twice. Each event states its refund rule; cancelling refunds by that rule, and a host calling an
event off refunds everyone.

**Rooms & PGs.** Advertise a room, a PG bed, a flat share or a whole flat. A card shows the rent,
the owner's first name and badge — never the address or a phone. Requesting a visit is free;
contact details and the exact address are shared only when the owner accepts. An optional booking
amount works like a ticket: fees on top for the guest, the owner receives what she asked.

**Safety.** Every Social page carries the platform's position in plain words: all members are
identity-verified, GharSe only connects people, and personal safety rests with the individuals.
Anyone can report or block anyone. Reports from three different members put an account on hold
until the team reviews it under **Reports**; a held account sees why, and who to write to. The
grievance officer required by the IT Rules 2021 is set under *Fees & Settings* and shown on every
disclaimer. No sale of alcohol, no money changing hands outside the app.

**WhatsApp.** Nine events message someone — new order, payment confirmed, every status change,
cancellation, new quote, quote accepted, new subscription, payout sent. With Cloud API
credentials they send themselves; without them they queue in the Outbox behind a `wa.me` link,
one tap each.

---

## Code

Small modules with a one-way dependency graph: `app → pages → ui → store`.

- **`app.py`** — the marketplace pages, navigation and the money (`split_money`, `payout_due`).
- **`fssai.py`, `ask.py`, `learn.py`, `profile.py`** — one feature each. None imports `app.py`.
- **`kyc.py`, `safety.py`** — identity verification, OTP and SMS; reports, blocks, holds and the
  disclaimers.
- **`social.py`, `rentals.py`** — events and rooms, the Social space.
- **`themes.py`** — all styling: 20 themes as token sets, one stylesheet template.
- **`ui.py`** — shared helpers: escaping, rupees, themed tables, photo handling, WhatsApp.
- **`catalog.py`** — every fixed list: areas, categories, the ask groups, skills, event kinds.
- **`store.py`** — everything underneath: schema, migrations, connection handling, password
  hashing, India-correct time. It speaks **SQLite locally and Postgres in production**, chosen
  at runtime by whether a database URL is configured, so the same code runs in both places.

Everything a stranger types is escaped for HTML **and** Markdown before it reaches a page, and
auto-linking is broken with an invisible character, so no name, review or job title can become
markup, a link or a tracking image. Photos are re-encoded on upload; a file that is not really an
image is refused rather than stored.

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
