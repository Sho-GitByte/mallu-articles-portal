# PraxisDesk

**CA / CMA articleship and early-career placement.**

A placement bridge for articleship and early-career roles in accounting, audit and finance. The
admin team sits in the middle as the broker: firms post openings, candidates post profiles,
contacts stay masked on both sides, and introductions flow through admin — which is what earns
the referral bonus.

Started as a Bengaluru CA/CMA articleship board and is widening: open to every candidate and
firm, not one community, and built to grow past a single city.

## Roles & pages

| Role | Pages |
|------|-------|
| **Admin** (broker) | Dashboard (live KPIs + charts) · CA Firms · Candidates · Vacancies · Referrals & Placements (bonus tracker) · Advertisements |
| **Firm** (recruiter) | My Firm Profile · My Vacancies · Search Candidates |
| **Candidate** (student) | My Profile & Resume · Search Firms & Openings |

- **Masking:** firms never see unmasked candidate contacts and vice-versa — only admin does.
- **No fake data:** the app starts empty and fills as real firms and candidates register. The
  only seeded row is the admin login, needed to bootstrap.
- **Ad slot:** a tall, slim rectangle pinned top-right on every page. Admin uploads the poster
  under *Advertisements*.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501.

**First login:** username `admin` · password `admin@123` — change it after signing in.
Firms and candidates self-register from the **Register** tab.

## Deploy free on Streamlit Community Cloud

1. Push this repo to GitHub.
2. Go to https://share.streamlit.io → **New app**.
3. Point it at your repo and `app.py`.

## ⚠️ Data persistence caveat

The MVP uses **SQLite** (`praxisdesk.db`). On Streamlit Community Cloud the filesystem is
**ephemeral** — the database (and any uploaded resumes or ad images) resets when the app sleeps
or redeploys. That's fine for a demo and an early pilot.

The moment data needs to stick for real firms and candidates, swap SQLite for hosted
**Postgres (Supabase)**. All DB access goes through a thin `q()` / `execute()` layer in `app.py`,
so that swap is contained rather than a rewrite.

> Renamed from `mallu_articles.db`: if you have an existing local database worth keeping, rename
> the file to `praxisdesk.db` before starting the app, or it will create a fresh empty one.

## Tech

- Single-file **Streamlit** app (`app.py`)
- **SQLite** for storage (MVP)
- Role-based login (admin / firm / candidate), SHA-256 password hashing
- Deep navy/black theme with an electric-blue accent

---

*PraxisDesk is unrelated to GharSe, the home-economy marketplace previously developed in this
repository — that is a separate product moving to its own repository.*
