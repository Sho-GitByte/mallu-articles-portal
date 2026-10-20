# Deploying GharSe

From an empty repository to a live URL. Follow it in order; step 4 is the one people skip
and regret.

---

## 1. Push the code

```bash
git remote add origin https://github.com/<you>/gharse.git
git push -u origin main
```

## 2. Create the app on Streamlit Community Cloud

1. Sign in at **https://share.streamlit.io** with the GitHub account that owns the repo.
2. **Create app** → **Deploy a public app from GitHub** (a private repo works too — authorise
   Streamlit for it when asked).
3. Fill in:
   - **Repository:** `<you>/gharse`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **Python version:** 3.11
4. Deploy. First build takes a few minutes while it installs `requirements.txt`.

At this point the app runs — on SQLite. Do not let anyone real use it yet.

## 3. Set your admin password

Open the app's **⋮ → Settings → Secrets** and paste:

```toml
[admin]
password = "something-long-that-is-not-admin@123"
```

Save. The app restarts. Until you do this, the sign-in page says out loud that it is running
on the demo password — that warning is there so a half-configured deployment cannot be
mistaken for a finished one.

The admin account is created **once**, on the first run against an empty database. If the
account already exists, changing this secret does nothing — sign in and change the password
under **Fees & Settings** instead.

## 4. Move to Postgres — before anyone real uses it

**Streamlit Community Cloud's filesystem is ephemeral.** When the app sleeps after inactivity,
or you push a commit, the container is rebuilt and `gharse.db` is gone: every kitchen, every
order, every payout record, every photo. On SQLite this app is a demo, not a business.

Supabase's free tier is enough to start:

1. **https://supabase.com** → **New project**. Pick a region near your users
   (Singapore or Mumbai for Bengaluru). Save the database password it asks you to set.
2. **Project Settings → Database → Connection string → URI**. Copy it.
3. Back in the Streamlit app's **Secrets**, add:

   ```toml
   [database]
   url = "postgresql://postgres:YOUR-PASSWORD@db.YOUR-PROJECT.supabase.co:5432/postgres?sslmode=require"
   ```

   Keep `?sslmode=require` — Supabase needs it.

   Supabase databases are UTF-8, which GharSe needs (names, "—", "₹", Malayalam and Kannada
   text). If you create a Postgres database yourself, create it with
   `CREATE DATABASE gharse ENCODING 'UTF8' TEMPLATE template0;` — a server initialised with the
   C locale defaults to SQL_ASCII and will refuse that text.
4. Save. The app restarts, creates its schema in Postgres and is now durable.

Nothing else changes: `store.py` speaks both dialects, so the same code runs on your laptop
against SQLite and in production against Postgres. `python test_sql_dialects.py` exists to keep
it that way.

> Moving to Postgres does **not** carry your SQLite data across. Do it before the pilot, not
> during it.

## 5. WhatsApp (optional, and not needed to start)

Without credentials, every message the app wants to send queues in **Admin → WhatsApp Outbox**
with a `wa.me` link — one tap each, no Meta account, which is the right way to run 25 kitchens.

When the volume justifies it, add a Meta WhatsApp Cloud API number and set:

```toml
WHATSAPP_TOKEN = "..."
WHATSAPP_PHONE_ID = "..."
```

Messages then send themselves. Note that business-initiated messages outside WhatsApp's
24-hour customer-service window need an approved template; the API's error shows in the Outbox
rather than failing silently.

## 6. SMS codes and identity checks

Phone verification sends a six-digit code by SMS. Locally, on SQLite, the code is shown on screen
in a red "demo mode" box so you can try it. **On Postgres no code is sent until SMS is set up** —
the page says so, and nobody can verify a phone, which also means nobody can take a care ask,
host a party or list a room. Set up one of these:

```toml
# MSG91 — the usual choice in India. Needs a DLT-registered OTP template.
[sms]
msg91_authkey = "..."
msg91_template_id = "..."

# or Twilio
# [sms]
# twilio_sid = "..."
# twilio_token = "..."
# twilio_from = "+1..."
```

Indian SMS must go through a DLT-registered sender and template (TRAI rules); MSG91 walks you
through that registration, and it takes a few days, so start it early.

`[kyc] demo_otp = true` turns the on-screen code back on for a Postgres copy you are only
testing. Never set it on the live app — it lets anyone "verify" any number.

Identity documents are checked **by people**, in **Admin → Social → KYC Queue**: the name, date of
birth, masked PAN and last four of Aadhaar against a live selfie. The selfie is deleted when the
team decides. `[kyc] vendor = "digilocker"` (or idfy, hyperverge, signzy, digio) records which
eKYC partner you have signed with, but no partner is wired into the code yet — the page says so
and the team keeps checking by hand until one is.

## 7. Things to know once it is live

- **The app sleeps.** After inactivity Community Cloud stops the container; the next visitor
  waits ~30 seconds for it to wake. With Postgres no data is lost — but a cook opening it at
  11:55 to check lunch orders should know it may take a moment.
- **Back up.** Supabase → Database → Backups. Take one before any change to the schema, and
  keep a copy off Supabase before the first real payout run.
- **One process.** Community Cloud runs a single container. That is fine for a neighbourhood
  pilot and will not survive a city.
- **Photos live in the database** as downscaled JPEGs. At 25 kitchens that is tens of MB and
  perfectly fine; at ten times that, move them to object storage.

## 8. Before the first real rupee — the part no code can do

- [ ] **FSSAI registration** applied for by every kitchen selling food. The app blocks a food
      listing without a number on file, deliberately. Sit with each woman and fill the form
      (see PILOT_KIT.md §3).
- [ ] **A business current account**, and its UPI ID in **Fees & Settings → Platform UPI**.
      Do not collect customer money into a personal account.
- [ ] **A CA's read on e-commerce-operator obligations** — TCS under section 52, and GST on
      restaurant service supplied through a platform, which can sit with the platform rather
      than the cook. Get this answered before volume, not after.
- [ ] **Each cook's UPI ID in her own name**, tested with a ₹1 transfer.
- [ ] **A refund rule you can say in one sentence**, visible before a customer's first order.
- [ ] **Someone's phone number** on the app for when something goes wrong at 1 PM.
- [ ] **A grievance officer** — a named person in India, with an email and phone — entered under
      **Fees & Settings**. The IT Rules 2021 require one; the app shows them on every Social page
      and to anyone whose account is on hold.
- [ ] **A lawyer's read on GharSe Social** before the first ticketed party: the disclaimer says the
      platform only connects people, but whether that holds for an event GharSe takes a fee on is
      a legal question, as are local rules on gatherings, noise and serving alcohol at home.
- [ ] **SMS set up** (§6) and its DLT template approved.
