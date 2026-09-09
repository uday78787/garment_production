# Garment Production Management System

A Django 5 web application for garment-factory production planning and
daily tracking: line assignments, ramp-up efficiency forecasting, daily
production entry, and Excel/PDF reporting.

## Stack

- **Backend:** Python 3.12+, Django 5+, Django ORM, PostgreSQL (prod) / SQLite (dev)
- **Auth:** Django's built-in `User` model + `UserProfile` (role), Django Groups
  (`Admin`, `Production User`)
- **Frontend:** Django Templates, Bootstrap 5, Chart.js, DataTables, Font Awesome
- **Reports:** pandas + openpyxl (Excel), WeasyPrint (PDF)
- **Deployment:** Docker, docker-compose, Gunicorn, Nginx, django-environ

## App structure

| App | Responsibility |
|---|---|
| `accounts` | Login/logout/password reset, `UserProfile`, role↔Group sync, access-control mixins |
| `master_data` | `ProductionLine`, `Article` (style + SAM), `Holiday` |
| `planning` | `ProductionOrder`, `LineAssignment`, and `planning/services/calculations.py` |
| `production` | `DailyProductionEntry` (daily entry form/list, derived-field calculation) |
| `dashboard` | Admin dashboard (factory-wide) and User dashboard (assigned lines only) |
| `reports` | Filterable report page + Excel/PDF export |
| `common` | `AuditLog`, shared base model, shared utilities |

All garment-production math lives in **`planning/services/calculations.py`**
as pure, framework-independent functions — this is the single source of
truth for every efficiency/output/forecast number in the app, and is
covered by `planning/tests/test_calculations.py`.

## Local development (SQLite, no Docker)

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # edit as needed; DATABASE_URL can stay unset for SQLite

python manage.py migrate
python manage.py setup_roles      # creates Admin / Production User groups + permissions
python manage.py createsuperuser  # first Admin login

python manage.py runserver
```

### OTP password reset API

Request an OTP:

```http
POST /accounts/api/password-reset/request/
Content-Type: application/json

{"email": "user@example.com"}
```

Responses use a neutral `200` message whether or not an active account exists,
so the endpoint does not reveal account existence. `429` means a recent OTP
request is still in its one-minute cooldown, and `503` means email delivery is
not configured or failed.

Confirm the OTP and set the new password:

```http
POST /accounts/api/password-reset/confirm/
Content-Type: application/json

{"email": "user@example.com", "otp": "123456", "new_password": "A-secure-password-123"}
```

The OTP expires after 10 minutes and is limited to five attempts. During local
development, the default console email backend prints the OTP in the server
terminal. To deliver real email, set these values in `.env` (use a Gmail app
password, not the normal Gmail password):

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-gmail-app-password
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=your-email@gmail.com
```

Then:
1. Log in to `/admin/` as your superuser.
2. Set that user's `UserProfile.role` to `Admin` (or use the admin panel's
   User Profiles section) — new users default to `Production User`.
3. Create `ProductionLine`, `Article`, `ProductionOrder`, and
   `LineAssignment` records (via `/admin/`).
4. Create Production User accounts and assign them to specific
   `ProductionLine`s via their `UserProfile.assigned_lines`.

## Running tests

```bash
python manage.py test
```

This runs, among others, `planning/tests/test_calculations.py`, which
verifies every calculation function against the sample scenario:

```
Order Qty = 100,000   SAM = 35            Manpower = 40
Minutes/operator/day = 480
Start Efficiency = 25%   Daily Growth = 5%   Max Efficiency = 60%
```

## Docker deployment

```bash
cp .env.example .env              # set DJANGO_SECRET_KEY, DEBUG=False, ALLOWED_HOSTS, POSTGRES_* etc.
docker compose build
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

This brings up three containers:
- `db` — PostgreSQL 16
- `web` — Django app served by Gunicorn (migrations, `setup_roles`, and
  `collectstatic` run automatically on start via `docker/entrypoint.sh`)
- `nginx` — reverse proxy on port 80, serving `/static/` and `/media/`
  directly and proxying everything else to `web`

### Database backups

```bash
./docker/backup_db.sh
```

Dumps the Postgres database to `docker/backups/<db>_<timestamp>.sql`.
Schedule it with cron for regular backups; restore with:

```bash
docker compose exec -T db psql -U <user> <db> < docker/backups/<file>.sql
```

## Roles & access control

- **Admin**: full access to every `ProductionLine`, `ProductionOrder`, and
  report. Manages master data via `/admin/`.
- **Production User**: can only view/enter `DailyProductionEntry` rows for
  the `ProductionLine`s listed in their `UserProfile.assigned_lines`. This
  is enforced in views/forms (queryset scoping), not just at the template
  level.

Access control is layered:
- `@login_required` / `LoginRequiredMixin` — must be authenticated.
- `AdminRequiredMixin` / `admin_required` — Admin-only views (e.g. would
  gate a future "manage all lines" screen).
- `LineAccessRequiredMixin` — per-object line scoping.
- Django Groups + `permission_required` — model-level permissions, kept in
  sync with `UserProfile.role` automatically via signals, and bootstrapped
  by `python manage.py setup_roles`.

## Notes on WeasyPrint (PDF export)

WeasyPrint needs native libraries (Pango, Cairo, GDK-Pixbuf) which are
installed in the provided `Dockerfile`. If you run outside Docker and PDF
export returns a 501, install those system packages for your OS (see
WeasyPrint's install docs) or drop in a ReportLab-based exporter instead —
`reports/views.py:export_pdf` is isolated so swapping the PDF engine only
touches that one function.
