# Mallu Articles Bengaluru — CA/CMA Articleship Portal

A placement/referral bridge for **CA & CMA articleship in Bangalore**. The admin team sits in
the middle as the broker: firms post openings, candidates post profiles, contacts stay masked
on both sides, and introductions flow through admin — which is what earns the referral bonus.

## Roles & pages

| Role | Pages |
|------|-------|
| **Admin** (broker) | Dashboard (live KPIs + charts) · CA Firms · Candidates · Vacancies · Referrals & Placements (bonus tracker) · Advertisements |
| **Firm** (recruiter) | My Firm Profile · My Vacancies · Search Candidates |
| **Candidate** (student) | My Profile & Resume · Search Firms & Openings |

- **Masking:** firms never see unmasked candidate contacts and vice-versa — only admin does.
- **No fake data:** the app starts empty and fills as real firms/students register. The only
  seeded row is the admin login (needed to bootstrap).
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

1. Push this folder to a GitHub repo.
2. Go to https://share.streamlit.io → **New app**.
3. Point it at your repo and `app.py`.

## ⚠️ Data persistence caveat

The MVP uses **SQLite** (`mallu_articles.db`). On Streamlit Community Cloud the filesystem is
**ephemeral** — the database (and any uploaded resumes/ads) resets when the app sleeps or
redeploys. That's fine for demo and an early pilot.

The moment data needs to stick for real firms and students, swap SQLite for a free hosted
**Postgres (Supabase)**. All DB access goes through a thin `q()` / `execute()` layer in
`app.py`, so that swap is a small, contained change — not a rewrite.

## Tech

- Single-file **Streamlit** app (`app.py`)
- **SQLite** for storage (MVP)
- Role-based login (admin / firm / candidate), SHA-256 password hashing
- Deep navy/black Tessa-style theme with electric-blue accent
