# CampusBite

CampusBite is a Flask canteen ordering website with student/staff login, menu browsing, cart checkout, cash-on-delivery orders, optional Razorpay online payment, and an admin dashboard for orders and menu items.

## Features

- User registration and login
- Menu categories for breakfast, tea/coffee, and cold drinks
- Cart, checkout, and order history
- Cash on delivery checkout works without third-party credentials
- Optional Razorpay UPI/online payment flow
- Admin dashboard with order status updates
- Admin menu add/edit/delete/availability controls
- Render deployment config included

## Local Setup

1. Create and activate a virtual environment.

```bash
python -m venv venv
venv\Scripts\activate
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Create your local environment file.

```bash
copy .env.example .env
```

4. Run the app.

```bash
python app.py
```

Open http://127.0.0.1:5000.

## Environment Variables

- `SECRET_KEY`: Required in production. Use a long random value.
- `DATABASE_URL`: Optional. If blank, the app uses SQLite at `instance/project.db`.
- `RAZORPAY_KEY_ID`: Optional. Required for online payment.
- `RAZORPAY_KEY_SECRET`: Optional. Required for online payment.
- `FLASK_DEBUG`: Set to `1` only for local development.

## Deploying On Render

This repo includes `render.yaml`.

Set these Render environment variables before deploying:

- `SECRET_KEY`
- `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` if you want online payments

The default Render start command is:

```bash
gunicorn app:app
```

## GitHub Notes

Do not commit `venv/`, `.env`, logs, or SQLite database files. They are ignored by `.gitignore`.
