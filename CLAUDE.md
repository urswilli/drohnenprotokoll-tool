# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
python3 app.py          # starts on http://localhost:5050 with debug=True
```

The SQLite database and `output/` folder are created automatically on first run in the same directory as `app.py`. Default admin credentials are printed to the console on first launch (`admin` / `drohnen2024`).

Dependencies: `flask`, `pypdf`, `werkzeug`, `gunicorn` (see `requirements.txt`). `itsdangerous` is imported directly (password-reset tokens) but ships transitively with Flask, so it is not pinned separately.

There is no build step, linter, or test suite. To smoke-test against a throwaway DB (so the real `drohnen.db` is never touched), point `DATA_DIR` at a temp dir and drive `app.test_client()`:

```bash
DATA_DIR=$(mktemp -d) python3 -c "import app; c=app.app.test_client(); print(c.get('/login').status_code)"
```

## Docker / Production Deployment

**Infrastruktur:** Proxmox auf einem HP EliteDesk Mini 800 G6. Die App läuft als Docker-Stack (Portainer) in der VM **`docker-public`** (VLAN **50**, VLAN-ID **501**). Öffentlicher Zugriff nur via Cloudflare Tunnel. **Kein SSH-Zugriff durch Agents** — Produktionsdaten nur manuell vom User bereitstellen (siehe «Lokal testen mit Produktionsdaten»).

**GitHub Actions** (`.github/workflows/docker.yml`) builds and pushes a multi-arch image (`linux/amd64`, `linux/arm64`) to `ghcr.io/urswilli/drohnenprotokoll-tool:latest` on every push to `main`.

**Data paths** are controlled by the `DATA_DIR` environment variable (default: same directory as `app.py`). In Docker, `DATA_DIR=/app/data` and the host bind-mount `./data` (Portainer-Stack-Verzeichnis auf der Proxmox-VM) is mounted there. DB and output files are created inside `DATA_DIR` automatically.

**First-time setup** in VM `docker-public` (Portainer Stack):

```bash
STACK_DIR=$(docker inspect <stack-container> | grep -o '/var/lib/portainer/compose/[0-9]*' | head -1)
sudo mkdir -p "$STACK_DIR/data"
sudo chown -R 1001:1001 "$STACK_DIR/data"
```

Then deploy via Portainer (Stacks → Add Stack → Repository) with environment variables:

- `SECRET_KEY` = output of `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `DATA_DIR` = `/app/data`
- `CLOUDFLARE_TUNNEL_TOKEN` = token from the Cloudflare Zero Trust dashboard (only if the `cloudflared` service is used)

**Update:** Portainer → Stack → Pull and redeploy

**Public access (Cloudflare Tunnel):** `docker-compose.yml` includes a `cloudflared` service that exposes the app publicly (e.g. `srf.dronenerds.ch`) with no open router ports. The public hostname is configured in the Cloudflare Zero Trust dashboard pointing at `http://app:5050`. cloudflared needs **outbound port 7844** (TCP + UDP) — there is no flag that moves it to 443; `--protocol http2` still dials 7844. Known gotcha: Sunrise's **"Surf Protect"** ISP filter silently blocks 7844 (verify with `nc -zv -w5 region1.v2.argotunnel.com 7844` from any host on the LAN — a timeout means it's blocked). Disable Surf Protect in the Sunrise customer portal; the UDR7 firewall/IPS was never the cause.

**Lokal testen mit Produktionsdaten** (User kopiert manuell von VM `docker-public`):

1. `drohnen.db` von der VM nach **`prod-import/drohnen.db`** legen (Prod-Pfad: `/opt/docker/drohnenprotokoll/data/drohnen.db`; optional: `prod-import/output/` mit PDFs).
2. `./import-prod-data.sh` → übernimmt nach `data/`, schaltet Testmodus aus.
3. `./run-local.sh` → http://localhost:5050

`prod-import/` ist gitignored (nur `.gitkeep` versioniert).

**Local dev:** `./run-local.sh` (setzt `DATA_DIR=./data`, `SESSION_COOKIE_SECURE=false` für http://localhost:5050).

`SECRET_KEY` falls back to a hardcoded dev string when not set.

## Architecture

Single-file Flask app (`app.py`) — no blueprints, no separate models file. `init_db()` is called at module level (not inside `__main__`) so it runs under both Gunicorn and the dev server.

**Data layer:** SQLite via `sqlite3` directly (no ORM). `get_db()` returns a `Row`-factory connection. `init_db()` creates all tables and runs `ALTER TABLE` migrations in try/except blocks (idempotent). Tables: `users`, `profiles`, `aircraft` (per-user drones), `drones` (SRG fleet, admin-managed), `settings` (key/value SMTP config), `sendeformate`, `verwendungszwecke`, `redaktionen`, `srg_ue`, `drone_holders`, `aircraft_holder_map`. `users.default_holder_id` stores the per-user default holder (including SRF system holder id=1 for SRF users). `users.theme_mode` stores per-user UI theme (`light` / `dark` / `system`, default `system`).

**PDF generation:** `fill_pdf(form_data)` uses `pypdf` to clone `SRG_Weisung und Checkliste für den Einsatz von Drohnen_V500e.pdf` and write form field values. Critical field mapping (confirmed by PDF coordinate analysis):

- `Text12x` = EVA section: `Text121`=eva_name, `Text122`=redaktion, `Text123`=srg_ue
- `Text13x` = Pilot section: `Text131`=pilot_name, `Text132`=pilot_address
- §4 signature lines: `Text13` = EVA email line, `Info.33` = EVA Vorname/Nachname below it (source: §4 field `eva_signature`, fallback `eva_name`); `Text15` = Pilot **email** line (never the name), `Info.34` = Pilot Vorname/Nachname below it
- `Info.32` = green instructional text above Ort/Datum (cleared to `''`)
- `Info.35`/`Info.37` = Ort EVA/Pilot; `Info.36`/`Info.38` = Datum EVA/Pilot
- Drohnenhalter: `Text111`=drone_holder_company, `Text112`=drone_holder_address (fallback to SRF hardcoded values)
- After fill: all `Info.`* fields cleared; AcroForm fields set ReadOnly via `_lock_pdf_fields()`

**Form submission flow:** `GET /` renders `form.html` with profile data + drone lists → `POST /submit` validates mandatory fields server-side (`_validate_form`), calls `fill_pdf()`, optionally `send_email()`, renders `success.html`. Session `form_prefill` enables «Neues Protokoll für diese Produktion» via `/?continue=1`; `PREFILL_CLEAR_KEYS` clears Drehort, Koordinaten, Wetterlage and flight-time fields. Continue keeps `holder_id`, `drone_select` (dropdown restored in JS), drone detail fields, `risikobeurteilung`, and §4 signature fields. «Formular zurücksetzen» reloads `GET /` when opened via continue (otherwise native reset; `pilot_company` always restored to profile value). The readonly Drohnenhalter fields in Section 1 are accompanied by hidden inputs (`drone_holder_company`, `drone_holder_address`) so the values reach `form_data`.

**SRF access control:** SRF system holder (id=1) and SRG fleet drones only for users with `@srf.ch` email (`_is_srf_user`). Protocol mail always To `drohnen@srf.ch`; `eng-service@srf.ch` only when SRF holder selected. EVA e-mail is always in Cc when `eva_email` is set (mandatory, non-editable in the form UI like `drohnen@srf.ch`).

**Holder default semantics:** The "Standard" marker in Profile/Form is driven by `users.default_holder_id` (not by globally shared system-holder flags). Users can clear the standard holder entirely; in that case the form falls back to the first holder in the dropdown. Migration backfills in `init_db()` intentionally skip SRF users so deleted custom SRF holders are not recreated on restart.

**Location API:** `GET /api/location-data?lat=&lon=` fetches Nominatim (reverse geocoding) and Open-Meteo (weather). Returns both `location` (full address with street) and `location_short` (PLZ + city only). `static/app.js` uses `location_short` for the Ort signature fields and `location` for the Drehort field.

**Profile address split:** `profiles` table has two address fields:

- `pilot_company_address` → form Section 1 (Drohnenhalter), goes to `Text111`/`Text112` in PDF
- `pilot_address` → form Section 2 (private pilot address), goes to `Text132` in PDF

**Auth:** Session-based (`flask.session`). `login_required` decorator. Login accepts username or pilot email. Admin `create_user` creates matching `profiles` row; `edit_user` / cascade `delete_user` in admin. Admin flag stored in `users.is_admin`. Two extra `users` columns (added via `CREATE TABLE` + idempotent `ALTER TABLE`): `email` and `is_approved` (default 1; self-registrations set 0).

**Self-registration & approval:** `GET/POST /register` (public) creates a user with `username=email`, `is_approved=0`, plus a matching `profiles` row. On registration two best-effort mails go out (failures never block the signup): a confirmation to the user, and a notification to `admin_email` (setting, default `info@dronenerds.ch`) containing a **direct approval link** `GET /approve-user/<token>` (token via `_approve_serializer`, salt `user-approve`, `max_age=7 days`). That route approves without login and notifies the user. Unapproved users are blocked at login. Admin can also approve via the "Offene Registrierungen" list in `admin.html` (actions `approve_user`; `reject_user` → deletes user + profile). User-approval notification text lives in the `_notify_user_approved(addr)` helper (used by both the route and the admin action).

**Email sender / SPF:** `send_email()` and `send_simple_email()` must set `From` to `smtp_from` (setting) or `smtp_user` — an address in the SMTP provider's own domain (e.g. `noreply@dronenerds.ch`). Never put the pilot's `srf.ch` address in `From`: the provider (Hostpoint) rejects it with `550 SPF check failed`. The pilot/EVA address goes into `Reply-To` instead so replies still reach them.

**Password reset:** `GET/POST /forgot-password` → looks up user by email, sends a link to `/reset-password/<token>`. Tokens are stateless via `itsdangerous.URLSafeTimedSerializer` (`_reset_serializer`, salt `pw-reset`, `max_age=3600`, signed with `SECRET_KEY`). The forgot-password response is always neutral (no account-existence leak).

**Test mode:** Settings `test_mode` (`'true'`/`'false'`) and `test_email`, managed in the admin "Test-Modus" card (action `save_testmode`). Helper `test_mode_on()`. When on: (1) login is blocked for non-admins and `login.html` shows a banner (`test_mode` template var passed to login/register/forgot/reset views); (2) `send_email()` redirects all protocol mail to `test_email` only (no Cc, `[TEST]` subject prefix).

**Notification addresses:** Settings `feedback_email` (default `feedback@dronenerds.ch`) and `admin_email` (default `info@dronenerds.ch`) live in their own admin "E-Mail-Benachrichtigungen" card (action `save_notifications`), separate from the Test-Modus card. Passed to the admin template as `notify`.

**Feedback:** `GET/POST /feedback` (`login_required`) — sender prefilled from `profiles.pilot_email` (fallback session username), sends via `send_simple_email()` to `feedback_email`. Navbar link in `base.html`.

**Versioning:** Displayed as `Beta 0.X` in the navbar (`base.html`) and on the login footer, X = running commit count (`--no-merges`, same as changelog). `_get_version()` reads env `APP_VERSION` (the count, injected at Docker build time by GitHub Actions via `build-args`), falling back to `git rev-list --count --no-merges HEAD` for local dev. Exposed to all templates as `app_version` via an `@app.context_processor`. The workflow uses `fetch-depth: 0` so the full history is available for counting. X matches Trello version labels (e.g. `0.45` → `Beta 0.45`). Release steps: see **Git Workflow** below.

**Changelog:** The version badge in the navbar is a button that opens a Bootstrap modal (`#changelogModal` in `base.html`) listing entries newest-first. `_get_changelog()` returns `[{version, hash, subject, date}]` where `version` = commit position (oldest=1, newest=`APP_VERSION`). Source priority: (1) `changelog.json` in `BASE_DIR` — generated at Docker build time by a workflow step from `git log --no-merges --reverse` and baked into the image (the slim runtime has no `git`); (2) live `git log` for local dev; (3) empty list. `changelog.json` is git-ignored (build artifact) but **not** docker-ignored — `.dockerignore` excludes `.git` so the runtime never falls back to a stale repo. Exposed to templates as `changelog`.

**Generic mail helper:** `send_simple_email(to, subject, body)` — plain-text mail using the same SMTP settings as `send_email()`; used by feedback, password reset, and approval notifications.

**Frontend:** Bootstrap 5.3 + Bootstrap Icons via CDN. `static/style.css` defines brand colours (`--srg-red: #c8102e`), `.field-readonly` (non-editable form fields), dark-mode overrides (`[data-bs-theme="dark"]`), and `.field-empty` (orange border, yellow background) for real-time empty-field highlighting. `static/theme.js` + navbar/guest dropdown toggle Hell/Dunkel/System; early inline script in `base.html` sets `data-bs-theme` before paint; `POST /api/theme` persists choice to `users.theme_mode`. `static/app.js` handles GPS/weather fetch, drone dropdown autofill, flight-minutes calculation, and Drehdatum→Datum sync. Per-template JS lives in `{% block scripts %}`.

**Empty-field highlighting:** `initEmptyHighlight()` in `form.html` toggles `.field-empty` on `input`/`change`/`blur`. Because programmatic `.value =` assignments don't fire those events, every autofill path must dispatch a synthetic `input` event so the highlight clears: `setField`/`setTextarea` and the Drehdatum/flight-minutes sync in `app.js`, and the `setVal(el, v)` helper used by the drone dropdown, Sendeformat, flight-minutes and EVA-signature sync in `form.html`.

## Key Conventions

- All POST actions on `/profile` and `/admin` use a hidden `action` field to dispatch logic (e.g. `save_profile`, `add_aircraft`, `edit_drone`).
- New DB columns must be added both in the `CREATE TABLE` statement and as a `try/except ALTER TABLE` block in `init_db()` for existing databases.
- `fill_pdf()` receives raw `request.form.to_dict()` — checkbox fields absent from POST are normalised to `'off'` in `/submit` before calling `fill_pdf`.
- Generated PDFs land in `DATA_DIR/output/` (gitignored). Filename pattern: `Drohnenprotokoll_{date}_{pilot}.pdf`.

## Git Workflow

**Do not commit or push during implementation** — only when the user explicitly orders a new version (e.g. «Version 0.45 abschliessen», «neue Version beauftragen»). Until then, leave changes uncommitted.

When a version release is ordered, commit and push are part of that release:

- No feature branches, no PRs — commit directly to `main` and push
- One release commit per version; the subject summarises all implemented changes
- Changelog in the app = commit **subject + body** (German bullet list in the body, like Beta 0.44); `_get_changelog()` reads from git / `changelog.json` — no separate changelog file
- Commit message: `Beta 0.{X}: {kurze Beschreibung in Deutsch}` — X = `git rev-list --count --no-merges HEAD` after the commit (= app version, = Trello label e.g. `0.46`); body with `-`-Bullets per changed area/file
- Update `CLAUDE.md` with new project insights from the release (architecture, conventions, deployment) — only when something relevant changed, not a mandatory diff every release

## Trello Toolcall

Board: [Drohnenprotokoll Webtool](https://trello.com/b/O9oUXt0N/drohnenprotokoll-webtool) (`O9oUXt0N`). Access via Composio MCP (`user-composio`; Trello is connected — use `COMPOSIO_SEARCH_TOOLS` → `TRELLO_*` to read and move cards).

**Version numbers:** Trello label `0.45` = app `Beta 0.45` = `git rev-list --count --no-merges HEAD` after release (see **Versioning** / **Git Workflow**).

**When starting a version** (user e.g. «Version X vorbereiten», «implementieren», «neue Version beauftragen»): fetch specs from list **Ready to implement** only — cards tagged with that version’s label (e.g. `0.46`). Each version has a master card with labels **Version** and the version number; linked cards (Trello card attachments) define scope. Do not commit yet (see **Git Workflow**).

**When releasing** (user «Version X abschliessen», after release commit + push): move cards **Ready to implement** → **Implemented** only if actually implemented for that version. Cards not done stay in **Ready to implement**. Move the master card only when the full linked scope for that version is done. If spec and implementation diverge, alert the user before moving. Put the mastercard followed by the spec cards on top of the **Implemented** list.

**Multi-version bugfix cards:** The same card can carry bugfixes across several releases. In the **description**, fixes are grouped under headings with the version number (e.g. `0.46`); the card gets an additional **label** for the current target version. **Reactivation (manual, by user):** move from **Implemented** back to **Ready to implement**, remove label **Implemented**, add the new version label — the agent does not remove **Implemented** and does not move cards back to **Ready to implement**. **Implementation:** only fixes under the heading for the **current** version; ignore older sections. **Release:** move to **Implemented** when the **current** version’s section is fully done (even if the card was in **Implemented** before).
