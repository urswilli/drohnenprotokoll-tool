# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
python3 app.py          # starts on http://localhost:5050 with debug=True
```

The SQLite database and `output/` folder are created automatically on first run in the same directory as `app.py`. Default admin credentials are printed to the console on first launch (`admin` / `drohnen2024`).

Dependencies: `flask`, `pypdf`, `werkzeug`, `gunicorn` (see `requirements.txt`)

## Docker / Production Deployment

**GitHub Actions** (`.github/workflows/docker.yml`) builds and pushes a multi-arch image (`linux/amd64`, `linux/arm64`) to `ghcr.io/urswilli/drohnenprotokoll-tool:latest` on every push to `main`.

**Data paths** are controlled by the `DATA_DIR` environment variable (default: same directory as `app.py`). In Docker, `DATA_DIR=/app/data` and the host directory `/opt/drohnenprotokoll/data` is mounted there. DB and output files are created inside `DATA_DIR` automatically.

**First-time setup on Pi:**
```bash
sudo mkdir -p /opt/drohnenprotokoll/data
```
Then deploy via Portainer (Stacks → Add Stack → Repository) with environment variables:
- `SECRET_KEY` = output of `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `DATA_DIR` = `/app/data`

**Update:** Portainer → Stack → Pull and redeploy

**Migrating existing DB to Pi:**
```bash
# On Mac:
scp "/path/to/drohnen.db" pi@<ip>:~/drohnen.db
# On Pi:
sudo mv ~/drohnen.db /opt/drohnenprotokoll/data/drohnen.db
```

`SECRET_KEY` falls back to a hardcoded dev string when not set.

## Architecture

Single-file Flask app (`app.py`) — no blueprints, no separate models file. `init_db()` is called at module level (not inside `__main__`) so it runs under both Gunicorn and the dev server.

**Data layer:** SQLite via `sqlite3` directly (no ORM). `get_db()` returns a `Row`-factory connection. `init_db()` creates all tables and runs `ALTER TABLE` migrations in try/except blocks (idempotent). Tables: `users`, `profiles`, `aircraft` (per-user drones), `drones` (SRG fleet, admin-managed), `settings` (key/value SMTP config), `sendeformate`, `verwendungszwecke`.

**PDF generation:** `fill_pdf(form_data)` uses `pypdf` to clone `SRG_Weisung und Checkliste für den Einsatz von Drohnen_V500e.pdf` and write form field values. Critical field mapping (confirmed by PDF coordinate analysis):
- `Text12x` = EVA section: `Text121`=eva_name, `Text122`=redaktion, `Text123`=srg_ue
- `Text13x` = Pilot section: `Text131`=pilot_name, `Text132`=pilot_address
- `Info.33`/`Info.34` = Vorname/Nachname under email in signature block (kept visible)
- `Info.32` = green instructional text above Ort/Datum (cleared to `''`)
- `Info.35`/`Info.37` = Ort EVA/Pilot; `Info.36`/`Info.38` = Datum EVA/Pilot
- Drohnenhalter: `Text111`=drone_holder_company, `Text112`=drone_holder_address (fallback to SRF hardcoded values)

**Form submission flow:** `GET /` renders `form.html` with profile data + drone lists → `POST /submit` calls `fill_pdf()`, optionally `send_email()`, renders `success.html`. The readonly Drohnenhalter fields in Section 1 are accompanied by hidden inputs (`drone_holder_company`, `drone_holder_address`) so the values reach `form_data`.

**Location API:** `GET /api/location-data?lat=&lon=` fetches Nominatim (reverse geocoding) and Open-Meteo (weather). Returns both `location` (full address with street) and `location_short` (PLZ + city only). `static/app.js` uses `location_short` for the Ort signature fields and `location` for the Drehort field.

**Profile address split:** `profiles` table has two address fields:
- `pilot_company_address` → form Section 1 (Drohnenhalter), goes to `Text111`/`Text112` in PDF
- `pilot_address` → form Section 2 (private pilot address), goes to `Text132` in PDF

**Auth:** Session-based (`flask.session`). `login_required` decorator. Login accepts username or pilot email. Admin flag stored in `users.is_admin`. Two extra `users` columns (added via `CREATE TABLE` + idempotent `ALTER TABLE`): `email` and `is_approved` (default 1; self-registrations set 0).

**Self-registration & approval:** `GET/POST /register` (public) creates a user with `username=email`, `is_approved=0`, plus a matching `profiles` row. Unapproved users are blocked at login. Admin approves via the "Offene Registrierungen" list in `admin.html` (actions `approve_user` → sets `is_approved=1` + sends notification mail; `reject_user` → deletes user + profile).

**Password reset:** `GET/POST /forgot-password` → looks up user by email, sends a link to `/reset-password/<token>`. Tokens are stateless via `itsdangerous.URLSafeTimedSerializer` (`_reset_serializer`, salt `pw-reset`, `max_age=3600`, signed with `SECRET_KEY`). The forgot-password response is always neutral (no account-existence leak).

**Test mode:** Settings `test_mode` (`'true'`/`'false'`), `test_email`, `feedback_email`, managed in the admin "Test-Modus" card (action `save_testmode`). Helper `test_mode_on()`. When on: (1) login is blocked for non-admins and `login.html` shows a banner (`test_mode` template var passed to login/register/forgot/reset views); (2) `send_email()` redirects all protocol mail to `test_email` only (no Cc, `[TEST]` subject prefix).

**Feedback:** `GET/POST /feedback` (`login_required`) — sender prefilled from `profiles.pilot_email` (fallback session username), sends via `send_simple_email()` to `feedback_email`. Navbar link in `base.html`.

**Versioning:** Displayed as `Beta 0.X` in the navbar (`base.html`) and on the login footer, X = running commit count. `_get_version()` reads env `APP_VERSION` (the count, injected at Docker build time by GitHub Actions via `build-args`), falling back to `git rev-list --count HEAD` for local dev. Exposed to all templates as `app_version` via an `@app.context_processor`. The workflow uses `fetch-depth: 0` so the full history is available for counting.

**Generic mail helper:** `send_simple_email(to, subject, body)` — plain-text mail using the same SMTP settings as `send_email()`; used by feedback, password reset, and approval notifications.

**Frontend:** Bootstrap 5.3 + Bootstrap Icons via CDN. `static/style.css` defines brand colours (`--srg-red: #c8102e`) and `.field-empty` (orange border, yellow background) for real-time empty-field highlighting. `static/app.js` handles GPS/weather fetch, drone dropdown autofill, flight-minutes calculation, and Drehdatum→Datum sync. Per-template JS lives in `{% block scripts %}`.

**Empty-field highlighting:** `initEmptyHighlight()` in `form.html` toggles `.field-empty` on `input`/`change`/`blur`. Because programmatic `.value =` assignments don't fire those events, every autofill path must dispatch a synthetic `input` event so the highlight clears: `setField`/`setTextarea` and the Drehdatum/flight-minutes sync in `app.js`, and the `setVal(el, v)` helper used by the drone dropdown, Sendeformat, flight-minutes and EVA-signature sync in `form.html`.

## Key Conventions

- All POST actions on `/profile` and `/admin` use a hidden `action` field to dispatch logic (e.g. `save_profile`, `add_aircraft`, `edit_drone`).
- New DB columns must be added both in the `CREATE TABLE` statement and as a `try/except ALTER TABLE` block in `init_db()` for existing databases.
- `fill_pdf()` receives raw `request.form.to_dict()` — checkbox fields absent from POST are normalised to `'off'` in `/submit` before calling `fill_pdf`.
- Generated PDFs land in `DATA_DIR/output/` (gitignored). Filename pattern: `Drohnenprotokoll_{date}_{pilot}.pdf`.
