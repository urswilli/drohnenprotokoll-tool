import os
import io
import json
import sqlite3
import urllib.request
import urllib.parse
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_file, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

def _hash(pw): return generate_password_hash(pw, method='pbkdf2:sha256')
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'drohnen-protokoll-srg-2024-secret')

# Serializer für zeitlich begrenzte Passwort-Reset-Tokens (nutzt SECRET_KEY)
_reset_serializer = URLSafeTimedSerializer(app.secret_key, salt='pw-reset')
# Serializer für Direkt-Freischalt-Links in der Admin-Benachrichtigungsmail
_approve_serializer = URLSafeTimedSerializer(app.secret_key, salt='user-approve')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PATH = os.path.join(BASE_DIR, 'SRG_Weisung und Checkliste für den Einsatz von Drohnen_V500e.pdf')

# Im Docker-Container liegen DB und Output im gemounteten /app/data/,
# lokal (Entwicklung) direkt neben app.py
DATA_DIR   = os.environ.get('DATA_DIR', BASE_DIR)
DB_PATH    = os.path.join(DATA_DIR, 'drohnen.db')
OUTPUT_DIR = os.path.join(DATA_DIR, 'output')

os.makedirs(OUTPUT_DIR, exist_ok=True)


def _get_version():
    """Versionsstring 'Beta 0.X', wobei X die fortlaufende Commit-Anzahl ist.
    Im Docker-Image kommt die Zahl aus der Umgebungsvariable APP_VERSION
    (beim Build von GitHub Actions gesetzt); lokal wird sie aus Git ermittelt."""
    n = os.environ.get('APP_VERSION', '').strip()
    if not n:
        try:
            import subprocess
            n = subprocess.check_output(
                ['git', 'rev-list', '--count', 'HEAD'],
                cwd=BASE_DIR, stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            n = ''
    return f'Beta 0.{n}' if n else 'Beta 0.x'


def _get_changelog():
    """Liste der Changelog-Einträge {version, hash, subject, date}, neueste zuerst.
    Version = fortlaufende Commit-Position (ältester Commit = 1, neuester = APP_VERSION).

    Priorität:
    1. changelog.json in BASE_DIR (beim Docker-Build von GitHub Actions gebacken)
    2. Live-`git log` (lokale Entwicklung)
    3. leere Liste"""
    path = os.path.join(BASE_DIR, 'changelog.json')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    try:
        import subprocess
        out = subprocess.check_output(
            ['git', 'log', '--no-merges', '--reverse',
             '--pretty=format:%h\x1f%s\x1f%cI'],
            cwd=BASE_DIR, stderr=subprocess.DEVNULL).decode('utf-8')
        entries = []
        for i, line in enumerate(out.splitlines()):
            if not line.strip():
                continue
            parts = line.split('\x1f')
            if len(parts) != 3:
                continue
            h, subject, iso = parts
            entries.append({
                'version': i + 1,
                'hash': h,
                'subject': subject,
                'date': iso[:10],
            })
        entries.reverse()  # neueste zuerst
        return entries
    except Exception:
        return []


APP_VERSION = _get_version()
CHANGELOG = _get_changelog()


@app.context_processor
def inject_version():
    return {'app_version': APP_VERSION, 'changelog': CHANGELOG}

WEATHER_CODES = {
    0: 'Klarer Himmel', 1: 'Überwiegend klar', 2: 'Teilweise bewölkt', 3: 'Bedeckt',
    45: 'Nebel', 48: 'Eisnebel',
    51: 'Leichter Nieselregen', 53: 'Mässiger Nieselregen', 55: 'Starker Nieselregen',
    61: 'Leichter Regen', 63: 'Mässiger Regen', 65: 'Starker Regen',
    71: 'Leichter Schneefall', 73: 'Mässiger Schneefall', 75: 'Starker Schneefall',
    80: 'Leichte Regenschauer', 81: 'Mässige Regenschauer', 82: 'Starke Regenschauer',
    95: 'Gewitter', 96: 'Gewitter mit Hagel', 99: 'Gewitter mit starkem Hagel',
}

CHECKLIST_ITEMS = [
    (1,  False, 'Startplatz, Flugweg, Landeplatz und Notlandeplatz sind festgelegt.'),
    (2,  False, 'Luftfahrthindernisse sind bekannt.'),
    (3,  True,  'Einschränkungen des Luftraums bekannt – Abstand zu Flugpiste oder Helikopterlandeplatz eingehalten.'),
    (4,  True,  'Einschränkungen des Luftraums bekannt – Einschränkungen wegen Kontrollzone (CTR) berücksichtigt.'),
    (5,  True,  'Einschränkungen des Luftraums bekannt – NOTAM* und DABS** eingesehen und berücksichtigt.'),
    (6,  True,  'Einschränkungen des Luftraums bekannt – Weitere Luftraumsperrungen (militärische Anlagen, Jagdbanngebiete etc.) berücksichtigt.'),
    (7,  False, 'Flugwetterprognose (inkl. Windverhältnisse) eingeholt und für Einsatzort berücksichtigt.'),
    (8,  False, 'Technische Einschränkungen wie elektromagnetische Strahlung etc. für Betrieb der Drohne berücksichtigt.'),
    (9,  False, 'Die Drohne wird bezüglich Übernahme der Steuerung durch Dritte (Hijacking) entsprechend den Herstellervorgaben verwendet.'),
    (10, False, 'Beurteilung über Sicherheitsrisiken durch den Drohnenpiloten erfolgt und dokumentiert (Logbucheintrag).'),
    (11, False, 'Beim Flug der Drohne sind anwesende Personen instruiert, wo sie sich aufhalten dürfen.'),
    (12, False, 'Menschenansammlungen werden nicht überflogen. Eine Risikobeurteilung wurde gemacht.'),
    (13, False, 'Nie tief über Privatgrundstücke oder öffentliche Orte mit Personen geflogen (Privatsphäre).'),
    (14, False, 'Bestimmbare Personen werden nur mit Einwilligung oder überwiegendem öffentlichem Interesse gefilmt.'),
    (15, False, 'Die Drohne ist sachgemäss gewartet, Firmware der Drohne und des Controllers sind aktuell.'),
    (16, False, 'Akkus werden gemäss geltenden Anweisungen eingesetzt, transportiert und ersetzt.'),
    (17, False, 'Ladezustand der Akkus ist bei Planung der Flugdauer und Witterung (spez. Temperatur) berücksichtigt.'),
]

SENDEFORMATE = [
    'SRF 1', 'SRF 2', 'SRF info', 'SRF News', 'SRF Sport', 'SRFzwei',
    'SRF mySchool', '10 vor 10', 'Tagesschau', 'DOK', 'SRF bi de Lüt',
    'Schweizer Helden', 'SRF.ch / Online', 'RTS', 'RSI', 'RTR', 'Sonstige',
]

VERWENDUNGSZWECKE = [
    'Flugaufnahme für Film & Fernsehen',
    'Nachrichtenaufnahme / News',
    'Sportaufnahme',
    'Dokumentationsaufnahme',
    'Kulturaufnahme',
    'Anderes',
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                email TEXT DEFAULT '',
                is_approved INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                pilot_name TEXT DEFAULT '',
                pilot_email TEXT DEFAULT '',
                pilot_company TEXT DEFAULT '',
                pilot_address TEXT DEFAULT '',
                pilot_company_address TEXT DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS aircraft (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                brand TEXT DEFAULT '',
                type_serial TEXT DEFAULT '',
                registration TEXT DEFAULT '',
                equipment TEXT DEFAULT '',
                is_default INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS drones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                typ TEXT DEFAULT '',
                seriennummer TEXT DEFAULT '',
                reg_nr TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sendeformate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS verwendungszwecke (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
        ''')
        try:
            conn.execute("ALTER TABLE drones ADD COLUMN reg_nr TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE profiles ADD COLUMN pilot_company_address TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_approved INTEGER DEFAULT 1")
        except Exception:
            pass
        if conn.execute('SELECT COUNT(*) FROM sendeformate').fetchone()[0] == 0:
            conn.executemany('INSERT OR IGNORE INTO sendeformate(name) VALUES(?)',
                             [(s,) for s in SENDEFORMATE])
        if conn.execute('SELECT COUNT(*) FROM verwendungszwecke').fetchone()[0] == 0:
            conn.executemany('INSERT OR IGNORE INTO verwendungszwecke(name) VALUES(?)',
                             [(v,) for v in VERWENDUNGSZWECKE])
        conn.commit()


def get_setting(key, default=''):
    with get_db() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, value))
        conn.commit()


def test_mode_on():
    return get_setting('test_mode', 'false') == 'true'


def send_simple_email(to_addr, subject, body):
    """Verschickt eine einfache Text-Mail über die konfigurierten SMTP-Settings.
    Wird für Feedback und Passwort-Reset genutzt. Gibt (bool, str) zurück."""
    smtp_host = get_setting('smtp_host')
    smtp_port = int(get_setting('smtp_port', '587'))
    smtp_user = get_setting('smtp_user')
    smtp_pass = get_setting('smtp_pass')
    smtp_from = get_setting('smtp_from') or smtp_user
    if not smtp_host:
        return False, 'SMTP nicht konfiguriert'

    msg = MIMEMultipart()
    msg['From'] = smtp_from
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, [to_addr], msg.as_string())
        return True, 'E-Mail erfolgreich gesendet'
    except Exception as e:
        return False, str(e)


def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def dd_to_dms(dd, is_lat):
    d = int(abs(dd))
    m = int((abs(dd) - d) * 60)
    s = round(((abs(dd) - d) * 60 - m) * 60)
    direction = ('N' if dd >= 0 else 'S') if is_lat else ('O' if dd >= 0 else 'W')
    return f"{d}°{m}'{s}''{direction}"


def format_coordinates(lat, lon):
    return f"{dd_to_dms(lat, True)} / {dd_to_dms(lon, False)}"


def fill_pdf(form_data):
    reader = PdfReader(PDF_PATH)
    writer = PdfWriter()
    writer.clone_reader_document_root(reader)

    today = date.today().strftime('%Y/%m/%d')

    fields = {
        # Drohnenhalter (aus Profil, mit SRF-Fallback)
        'Text111': form_data.get('drone_holder_company', '').strip() or 'Schweizer Radio und Fernsehen (SRF)',
        'Text112': form_data.get('drone_holder_address', '').strip() or 'Fernsehstrasse 1-4\n8052 Zürich',

        # Text12x = EVA-Sektion (mit Redaktion/SRG-UE), Text13x = Pilot-Sektion (mit Wohnadresse)
        'Text121': form_data.get('eva_name', ''),
        'Text131': form_data.get('pilot_name', ''),
        'Text132': form_data.get('pilot_address', ''),

        # Einsatz details
        'Text122': form_data.get('redaktion', ''),
        'Text123': form_data.get('srg_ue', ''),

        # Aircraft
        'Text141': form_data.get('drone_brand', ''),
        'Text142': form_data.get('drone_type', ''),
        'Text143': form_data.get('drone_equipment', ''),
        'Text144': ' / '.join(p for p in [
            form_data.get('drone_seriennummer', ''),
            form_data.get('drone_reg', '')
        ] if p),

        # Dates & location
        'Text151': form_data.get('drehdatum', today),
        'Text12':  form_data.get('drehdatum', today),
        'Text16':  form_data.get('ort_pilot', ''),

        # Drehort & format
        'Text161': form_data.get('verwendungszweck', ''),
        'Text162': form_data.get('sendeformat', ''),
        'Text171': form_data.get('drehort', ''),
        'Text172': form_data.get('koordinaten', ''),

        # Weather, risk, flight
        'Text181': form_data.get('wetterlage', ''),
        'Text191': form_data.get('risikobeurteilung', ''),
        'Text201': form_data.get('startzeit', ''),
        'Text202': form_data.get('landezeit', ''),
        'Text203': form_data.get('flugminuten', ''),
        'Text204': form_data.get('anzahl_fluege', ''),
        'Text211': form_data.get('besondere_ereignisse', 'Keine besonderen Vorkommnisse'),

        # Signature section (§4) – pro Block: Signaturlinie + Zeile darunter
        'Info.35': form_data.get('ort_eva', ''),
        'Info.36': form_data.get('datum_eva', today),
        'Info.37': form_data.get('ort_pilot', ''),
        'Info.38': form_data.get('datum_pilot', today),
        # EVA-Block: E-Mail auf der Linie (Text13), Vorname/Nachname darunter (Info.33)
        'Text13':  form_data.get('eva_signature_email', '') or form_data.get('eva_email', ''),
        # Pilot-Block: NUR die E-Mail auf der Linie (Text15), Vorname/Nachname darunter (Info.34)
        'Text15':  form_data.get('pilot_email', ''),

        # Clear large instruction text fields
        'Info.01': '',
        'Info.11': '',
        'Info.12': '',
        'Info.13': '',
        'Info.15': '',
        'Info.16': '',
        'Info.17': '',
        'Info.18': '',
        'Info.19': '',
        'Info.20': '',
        'Info.31': '',
        'Info.32': '',                                # grüner Hinweistext oberhalb Ort/Datum – hide
        # "Vorname, Nachname" unter der E-Mail-Signaturlinie (EVA: §4-Feld, Fallback Abschnitt 3)
        'Info.33': form_data.get('eva_signature', '') or form_data.get('eva_name', ''),
        'Info.34': form_data.get('pilot_name', ''),   # "Vorname, Nachname" unter E-Mail (Pilot)

        # JA confirmation
        'JA': '/Ja',
    }

    # Checkboxes
    for i in range(1, 18):
        checked = form_data.get(f'cb_{i}', 'on') == 'on'
        fields[f'Check Box{i}'] = '/Oui' if checked else '/Off'

    for page in writer.pages:
        try:
            writer.update_page_form_field_values(page, fields, auto_regenerate=False)
        except Exception:
            pass

    drehdatum = form_data.get('drehdatum', today).replace('/', '-')
    pilot = form_data.get('pilot_name', 'Pilot').replace(' ', '_')
    filename = f'Drohnenprotokoll_{drehdatum}_{pilot}.pdf'
    output_path = os.path.join(OUTPUT_DIR, filename)
    with open(output_path, 'wb') as f:
        writer.write(f)
    return output_path, filename


def send_email(form_data, pdf_path, filename):
    smtp_host = get_setting('smtp_host')
    smtp_port = int(get_setting('smtp_port', '587'))
    smtp_user = get_setting('smtp_user')
    smtp_pass = get_setting('smtp_pass')
    # Absender MUSS zur SMTP-Domain passen, sonst lehnt der Mailserver wegen
    # SPF ab (z.B. "550 SPF check failed ... not allowed to send from srf.ch").
    # Daher NICHT die Pilot-Adresse als From verwenden – diese kommt in Reply-To.
    smtp_from = get_setting('smtp_from') or smtp_user

    if not smtp_host:
        return False, 'SMTP nicht konfiguriert'

    drehdatum = form_data.get('drehdatum', '')
    firma = 'SRF'
    redaktion = form_data.get('redaktion', '')
    sendeformat = form_data.get('sendeformat', '')
    pilot_name = form_data.get('pilot_name', '')
    drone_info = f"{form_data.get('drone_brand','')} {form_data.get('drone_type','')} {form_data.get('drone_reg','')}"
    drehort = form_data.get('drehort', '')
    kommentar = form_data.get('besondere_ereignisse', '')
    eva_email = form_data.get('eva_email', '')
    pilot_email = form_data.get('pilot_email', '')

    subject = f"DFB: {drehdatum}, {firma}, {redaktion}, {sendeformat}"
    body = (
        f"Zusammenfassung Drohnen-Flugbericht: {drehdatum}\n\n"
        f"Firma:   {firma}\n"
        f"Pilot:   {pilot_name}\n"
        f"Luftfahrzeug:   {drone_info}\n"
        f"Mail-Pilot:   {pilot_email}\n"
        f"Mail Verant. SRG:   {eva_email}\n"
        f"Format:   {sendeformat}\n"
        f"Drehort:   {drehort}\n"
        f"Kommentar:   {kommentar}\n\n"
        f"Alle weiteren Details sind dem angehängten Formular zu entnehmen.\n"
        f"Der Absender bestätigt alle Angaben sowie die E-Mailadressen wahrheitsgetreu ausgefüllt zu haben.\n\n"
        f"Die Anlage wird ohne Unterschrift versendet. Die E-Mail wird samt Anlage an drohnen@srf.ch, "
        f"die verantwortliche Person SRG sowie an den Auftragnehmer zugestellt. "
        f"Ohne Gegenbericht innert 10 Tagen gelten die Angaben von allen Beteiligten als genehmigt.\n\n"
        f"Gezeichnet: {pilot_name} {drehdatum}\n"
        f"digitales Formular V500e"
    )

    # Test-Modus: alle Mails nur an die Testadresse, kein Cc, [TEST]-Präfix
    if test_mode_on():
        test_addr = get_setting('test_email', 'test@dronenerds.ch')
        to_header = test_addr
        cc_list = []
        recipients = [test_addr]
        subject = f"[TEST] {subject}"
    else:
        to_header = 'drohnen@srf.ch; eng-service@srf.ch'
        cc_list = [e for e in [eva_email, pilot_email] if e]
        recipients = ['drohnen@srf.ch', 'eng-service@srf.ch'] + cc_list

    msg = MIMEMultipart()
    msg['From'] = smtp_from
    msg['To'] = to_header
    if cc_list:
        msg['Cc'] = '; '.join(cc_list)
    # Antworten sollen beim Piloten (bzw. EVA) landen, nicht beim noreply-Absender
    reply_to = pilot_email or eva_email
    if reply_to:
        msg['Reply-To'] = reply_to
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    with open(pdf_path, 'rb') as f:
        attachment = MIMEApplication(f.read(), _subtype='pdf')
        attachment.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(attachment)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, recipients, msg.as_string())
        return True, 'E-Mail erfolgreich gesendet'
    except Exception as e:
        return False, str(e)


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as conn:
            user = conn.execute(
                'SELECT u.* FROM users u LEFT JOIN profiles p ON u.id = p.user_id '
                'WHERE u.username=? OR p.pilot_email=?',
                (username, username)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            if not user['is_approved']:
                flash('Dein Konto wurde noch nicht freigeschaltet. Bitte warte auf die Bestätigung durch einen Administrator.', 'warning')
                return render_template('login.html', test_mode=test_mode_on())
            if test_mode_on() and not user['is_admin']:
                flash('Testmodus aktiv – Login zurzeit nur für Administratoren möglich.', 'warning')
                return render_template('login.html', test_mode=test_mode_on())
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
            return redirect(url_for('index'))
        flash('Ungültiger Benutzername oder Passwort.', 'danger')
    return render_template('login.html', test_mode=test_mode_on())


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm  = request.form.get('password_confirm', '')

        if not name or not email or '@' not in email:
            flash('Bitte Name und eine gültige E-Mail-Adresse angeben.', 'danger')
        elif len(password) < 6:
            flash('Das Passwort muss mindestens 6 Zeichen lang sein.', 'danger')
        elif password != confirm:
            flash('Die Passwörter stimmen nicht überein.', 'danger')
        else:
            try:
                with get_db() as conn:
                    cur = conn.execute(
                        'INSERT INTO users(username,password_hash,is_admin,email,is_approved) '
                        'VALUES(?,?,0,?,0)',
                        (email, _hash(password), email))
                    new_id = cur.lastrowid
                    conn.execute(
                        'INSERT INTO profiles(user_id,pilot_name,pilot_email) VALUES(?,?,?)',
                        (new_id, name, email))
                    conn.commit()

                # Bestätigungsmail an den Nutzer (best-effort)
                try:
                    send_simple_email(
                        email, 'Registrierung erhalten – Drohnenprotokoll',
                        f'Hallo {name}\n\n'
                        'Vielen Dank für deine Registrierung beim Drohnenprotokoll-Tool. '
                        'Aus Sicherheitsgründen wird dein Konto manuell geprüft und freigeschaltet. '
                        'Sobald das erledigt ist, erhältst du eine weitere E-Mail und kannst dich anmelden.\n\n'
                        'Happy flying!\nUrs\n')
                except Exception:
                    pass

                # Benachrichtigung an den Admin mit Direkt-Freischalt-Link (best-effort)
                try:
                    token = _approve_serializer.dumps(new_id)
                    link = url_for('approve_via_link', token=token, _external=True)
                    send_simple_email(
                        get_setting('admin_email', 'info@dronenerds.ch'),
                        f'Neue Registrierung: {email}',
                        f'Eine neue Registrierung ist eingegangen:\n\n'
                        f'Name:   {name}\n'
                        f'E-Mail: {email}\n\n'
                        f'Direkt freischalten (Link 7 Tage gültig):\n{link}\n\n'
                        f'Alternativ im Admin-Bereich unter "Offene Registrierungen".\n')
                except Exception:
                    pass

                flash('Registrierung eingegangen – ein Administrator schaltet dein Konto frei. '
                      'Du wirst per E-Mail benachrichtigt, sobald du dich anmelden kannst.', 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Diese E-Mail-Adresse ist bereits registriert.', 'danger')
    return render_template('register.html', test_mode=test_mode_on())


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        with get_db() as conn:
            user = conn.execute(
                'SELECT u.* FROM users u LEFT JOIN profiles p ON u.id = p.user_id '
                'WHERE u.username=? OR u.email=? OR p.pilot_email=?',
                (email, email, email)).fetchone()
        if user:
            token = _reset_serializer.dumps(user['id'])
            link = url_for('reset_password', token=token, _external=True)
            body = (
                f"Hallo\n\n"
                f"Es wurde ein Zurücksetzen deines Passworts für das Drohnenprotokoll-Tool angefordert.\n"
                f"Über den folgenden Link kannst du innerhalb von 60 Minuten ein neues Passwort setzen:\n\n"
                f"{link}\n\n"
                f"Falls du diese Anfrage nicht gestellt hast, kannst du diese E-Mail ignorieren.\n"
            )
            send_simple_email(email, 'Passwort zurücksetzen – Drohnenprotokoll', body)
        # Immer dieselbe neutrale Antwort (kein Leak, ob E-Mail existiert)
        flash('Falls ein Konto mit dieser E-Mail existiert, wurde ein Link zum Zurücksetzen verschickt.', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html', test_mode=test_mode_on())


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        user_id = _reset_serializer.loads(token, max_age=3600)
    except SignatureExpired:
        flash('Der Link ist abgelaufen. Bitte fordere einen neuen an.', 'danger')
        return redirect(url_for('forgot_password'))
    except BadSignature:
        flash('Ungültiger Link.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('password_confirm', '')
        if len(password) < 6:
            flash('Das Passwort muss mindestens 6 Zeichen lang sein.', 'danger')
        elif password != confirm:
            flash('Die Passwörter stimmen nicht überein.', 'danger')
        else:
            with get_db() as conn:
                conn.execute('UPDATE users SET password_hash=? WHERE id=?',
                             (_hash(password), user_id))
                conn.commit()
            flash('Passwort erfolgreich geändert. Du kannst dich jetzt anmelden.', 'success')
            return redirect(url_for('login'))
    return render_template('reset_password.html', token=token, test_mode=test_mode_on())


def _notify_user_approved(addr):
    """Schickt dem Nutzer die Freischalt-Benachrichtigung (best-effort)."""
    if not addr:
        return
    try:
        send_simple_email(
            addr, 'Konto freigeschaltet – Drohnenprotokoll',
            'Hallo\n\nDein Konto für das Drohnenprotokoll-Tool wurde freigeschaltet. '
            'Du kannst dich ab sofort anmelden.\n\nHappy flying!\nUrs\n')
    except Exception:
        pass


@app.route('/approve-user/<token>')
def approve_via_link(token):
    try:
        uid = _approve_serializer.loads(token, max_age=7 * 24 * 3600)
    except SignatureExpired:
        flash('Der Freischalt-Link ist abgelaufen. Bitte schalte den Nutzer im Admin-Bereich frei.', 'danger')
        return redirect(url_for('login'))
    except BadSignature:
        flash('Ungültiger Freischalt-Link.', 'danger')
        return redirect(url_for('login'))

    with get_db() as conn:
        user = conn.execute('SELECT id, username, email, is_approved FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            flash('Benutzer nicht gefunden (evtl. bereits gelöscht).', 'warning')
            return redirect(url_for('login'))
        addr = user['email'] or user['username']
        if user['is_approved']:
            flash(f'Benutzer {addr} ist bereits freigeschaltet.', 'info')
            return redirect(url_for('admin') if session.get('is_admin') else url_for('login'))
        conn.execute('UPDATE users SET is_approved=1 WHERE id=?', (uid,))
        conn.commit()

    _notify_user_approved(addr)
    flash(f'Benutzer {addr} wurde freigeschaltet.', 'success')
    return redirect(url_for('admin') if session.get('is_admin') else url_for('login'))


@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    with get_db() as conn:
        profile = conn.execute('SELECT pilot_email FROM profiles WHERE user_id=?',
                               (session['user_id'],)).fetchone()
    sender = (profile['pilot_email'] if profile and profile['pilot_email']
              else session.get('username', ''))

    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        text    = request.form.get('text', '').strip()
        if not subject or not text:
            flash('Bitte Betreff und Text ausfüllen.', 'danger')
        else:
            body = f"Feedback von: {sender}\n\nBetreff: {subject}\n\n{text}\n"
            ok, msg = send_simple_email(
                get_setting('feedback_email', 'feedback@dronenerds.ch'),
                f"Feedback: {subject}", body)
            if ok:
                flash('Vielen Dank für dein Feedback. Falls die Idee nicht kompletter '
                      'Mumpitz ist, wird sie innert nützlicher Frist implementiert.', 'success')
                return redirect(url_for('index'))
            else:
                flash(f'Feedback konnte nicht gesendet werden: {msg}', 'danger')
    return render_template('feedback.html', sender=sender)


@app.route('/')
@login_required
def index():
    with get_db() as conn:
        profile = conn.execute('SELECT * FROM profiles WHERE user_id=?',
                               (session['user_id'],)).fetchone()
        aircraft_list = conn.execute(
            'SELECT * FROM aircraft WHERE user_id=? ORDER BY is_default DESC, name',
            (session['user_id'],)).fetchall()
    with get_db() as conn:
        drones = conn.execute('SELECT * FROM drones ORDER BY name').fetchall()
        sendeformate = [r['name'] for r in conn.execute('SELECT name FROM sendeformate ORDER BY id').fetchall()]
        verwendungszwecke = [r['name'] for r in conn.execute('SELECT name FROM verwendungszwecke ORDER BY id').fetchall()]
    today = date.today().strftime('%Y/%m/%d')
    return render_template('form.html',
                           profile=profile,
                           aircraft_list=aircraft_list,
                           drones=drones,
                           today=today,
                           checklist=CHECKLIST_ITEMS,
                           sendeformate=sendeformate,
                           verwendungszwecke=verwendungszwecke)


@app.route('/submit', methods=['POST'])
@login_required
def submit():
    form_data = request.form.to_dict()
    # Handle checkboxes (unchecked = missing from form_data)
    for i in range(1, 18):
        if f'cb_{i}' not in form_data:
            form_data[f'cb_{i}'] = 'off'

    try:
        pdf_path, filename = fill_pdf(form_data)
    except Exception as e:
        flash(f'Fehler beim PDF-Erstellen: {e}', 'danger')
        return redirect(url_for('index'))

    sf = form_data.get('sendeformat', '').strip()
    if sf:
        with get_db() as conn:
            conn.execute('INSERT OR IGNORE INTO sendeformate(name) VALUES(?)', (sf,))
            conn.commit()

    email_sent = False
    email_msg = ''
    if form_data.get('send_email') == 'on':
        email_sent, email_msg = send_email(form_data, pdf_path, filename)

    return render_template('success.html',
                           filename=filename,
                           email_sent=email_sent,
                           email_msg=email_msg,
                           smtp_configured=bool(get_setting('smtp_host')))


@app.route('/download/<path:filename>')
@login_required
def download(filename):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.exists(file_path):
        flash('Datei nicht gefunden.', 'danger')
        return redirect(url_for('index'))
    return send_file(file_path, as_attachment=True, download_name=safe_name,
                     mimetype='application/pdf')


@app.route('/api/location-data')
@login_required
def location_data():
    try:
        lat = float(request.args.get('lat', 0))
        lon = float(request.args.get('lon', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid coordinates'}), 400

    result = {'location': '', 'location_short': '', 'coordinates': format_coordinates(lat, lon), 'weather': ''}

    # Reverse geocoding
    try:
        geo_url = f'https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=de'
        req = urllib.request.Request(geo_url, headers={'User-Agent': 'DrohnenprotokollTool/1.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            geo = json.loads(r.read())
        addr = geo.get('address', {})
        road   = addr.get('road', '')
        number = addr.get('house_number', '')
        post   = addr.get('postcode', '')
        city   = addr.get('city') or addr.get('town') or addr.get('village') or addr.get('hamlet', '')
        street = f"{road} {number}".strip()
        loc    = ', '.join(p for p in [street, f"{post} {city}".strip()] if p)
        result['location'] = loc if loc else geo.get('display_name', '')
        result['location_short'] = f"{post} {city}".strip() if (post or city) else result['location']
    except Exception:
        pass

    # Weather
    try:
        w_url = (f'https://api.open-meteo.com/v1/forecast'
                 f'?latitude={lat}&longitude={lon}'
                 f'&current=temperature_2m,wind_speed_10m,weather_code'
                 f'&timezone=auto')
        with urllib.request.urlopen(w_url, timeout=5) as r:
            w = json.loads(r.read())
        c = w['current']
        desc = WEATHER_CODES.get(c['weather_code'], 'Unbekannt')
        result['weather'] = (
            f"{desc}, {c['temperature_2m']:.1f}°C, "
            f"Wind: {c['wind_speed_10m']:.1f} km/h"
        )
    except Exception:
        pass

    return jsonify(result)


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_id = session['user_id']
    with get_db() as conn:
        if request.method == 'POST':
            action = request.form.get('action')

            if action == 'save_profile':
                conn.execute('''INSERT INTO profiles(user_id,pilot_name,pilot_email,pilot_company,pilot_address,pilot_company_address)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET
                    pilot_name=excluded.pilot_name,
                    pilot_email=excluded.pilot_email,
                    pilot_company=excluded.pilot_company,
                    pilot_address=excluded.pilot_address,
                    pilot_company_address=excluded.pilot_company_address''',
                    (user_id,
                     request.form.get('pilot_name',''),
                     request.form.get('pilot_email',''),
                     request.form.get('pilot_company',''),
                     request.form.get('pilot_address',''),
                     request.form.get('pilot_company_address','')))
                conn.commit()
                flash('Profil gespeichert.', 'success')

            elif action == 'add_aircraft':
                is_default = 1 if request.form.get('ac_default') else 0
                if is_default:
                    conn.execute('UPDATE aircraft SET is_default=0 WHERE user_id=?', (user_id,))
                conn.execute('''INSERT INTO aircraft(user_id,name,brand,type_serial,registration,equipment,is_default)
                    VALUES(?,?,?,?,?,?,?)''',
                    (user_id,
                     request.form.get('ac_name',''),
                     request.form.get('ac_brand',''),
                     request.form.get('ac_type',''),
                     request.form.get('ac_reg',''),
                     request.form.get('ac_equip',''),
                     is_default))
                conn.commit()
                flash('Drohne hinzugefügt.', 'success')

            elif action == 'edit_aircraft':
                ac_id = request.form.get('ac_id')
                conn.execute('''UPDATE aircraft SET name=?, brand=?, type_serial=?, registration=?, equipment=?
                                WHERE id=? AND user_id=?''',
                    (request.form.get('ac_name', ''),
                     request.form.get('ac_brand', ''),
                     request.form.get('ac_type', ''),
                     request.form.get('ac_reg', ''),
                     request.form.get('ac_equip', ''),
                     ac_id, user_id))
                conn.commit()
                flash('Drohne aktualisiert.', 'success')

            elif action == 'delete_aircraft':
                ac_id = request.form.get('ac_id')
                conn.execute('DELETE FROM aircraft WHERE id=? AND user_id=?', (ac_id, user_id))
                conn.commit()
                flash('Drohne gelöscht.', 'success')

            elif action == 'set_default':
                ac_id = request.form.get('ac_id')
                conn.execute('UPDATE aircraft SET is_default=0 WHERE user_id=?', (user_id,))
                conn.execute('UPDATE aircraft SET is_default=1 WHERE id=? AND user_id=?', (ac_id, user_id))
                conn.commit()

            elif action == 'change_password':
                old_pw = request.form.get('old_password','')
                new_pw = request.form.get('new_password','')
                user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
                if check_password_hash(user['password_hash'], old_pw) and len(new_pw) >= 6:
                    conn.execute('UPDATE users SET password_hash=? WHERE id=?',
                                 (_hash(new_pw), user_id))
                    conn.commit()
                    flash('Passwort geändert.', 'success')
                else:
                    flash('Passwort falsch oder zu kurz (min. 6 Zeichen).', 'danger')

            return redirect(url_for('profile'))

        profile_data = conn.execute('SELECT * FROM profiles WHERE user_id=?', (user_id,)).fetchone()
        aircraft_list = conn.execute(
            'SELECT * FROM aircraft WHERE user_id=? ORDER BY is_default DESC, name',
            (user_id,)).fetchall()

    return render_template('profile.html', profile=profile_data, aircraft_list=aircraft_list)


@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if not session.get('is_admin'):
        flash('Kein Zugriff.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save_smtp':
            for key in ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from']:
                set_setting(key, request.form.get(key, ''))
            flash('SMTP-Einstellungen gespeichert.', 'success')

        elif action == 'save_testmode':
            set_setting('test_mode', 'true' if request.form.get('test_mode') else 'false')
            set_setting('test_email', request.form.get('test_email', '').strip() or 'test@dronenerds.ch')
            flash('Test-Modus-Einstellungen gespeichert.', 'success')

        elif action == 'save_notifications':
            set_setting('feedback_email', request.form.get('feedback_email', '').strip() or 'feedback@dronenerds.ch')
            set_setting('admin_email', request.form.get('admin_email', '').strip() or 'info@dronenerds.ch')
            flash('Benachrichtigungs-Einstellungen gespeichert.', 'success')

        elif action == 'create_user':
            username = request.form.get('new_username', '').strip()
            password = request.form.get('new_password', '')
            is_admin = 1 if request.form.get('new_is_admin') else 0
            if username and len(password) >= 6:
                try:
                    with get_db() as conn:
                        conn.execute('INSERT INTO users(username,password_hash,is_admin,email,is_approved) VALUES(?,?,?,?,1)',
                                     (username, _hash(password), is_admin, username))
                        conn.commit()
                    flash(f'Benutzer {username!r} erstellt.', 'success')
                except sqlite3.IntegrityError:
                    flash('Benutzername bereits vergeben.', 'danger')
            else:
                flash('Benutzername und Passwort (min. 6 Zeichen) erforderlich.', 'danger')

        elif action == 'approve_user':
            uid = request.form.get('uid')
            with get_db() as conn:
                conn.execute('UPDATE users SET is_approved=1 WHERE id=?', (uid,))
                row = conn.execute('SELECT username, email FROM users WHERE id=?', (uid,)).fetchone()
                conn.commit()
            if row:
                _notify_user_approved(row['email'] or row['username'])
            flash('Benutzer freigeschaltet.', 'success')

        elif action == 'reject_user':
            uid = request.form.get('uid')
            with get_db() as conn:
                conn.execute('DELETE FROM profiles WHERE user_id=?', (uid,))
                conn.execute('DELETE FROM users WHERE id=?', (uid,))
                conn.commit()
            flash('Registrierung abgelehnt und gelöscht.', 'success')

        elif action == 'delete_user':
            uid = request.form.get('uid')
            if str(uid) != str(session['user_id']):
                with get_db() as conn:
                    conn.execute('DELETE FROM users WHERE id=?', (uid,))
                    conn.commit()
                flash('Benutzer gelöscht.', 'success')

        elif action == 'add_drone':
            name = request.form.get('drone_name', '').strip()
            if name:
                with get_db() as conn:
                    conn.execute(
                        'INSERT INTO drones(name, typ, seriennummer, reg_nr) VALUES(?,?,?,?)',
                        (name,
                         request.form.get('drone_typ', '').strip(),
                         request.form.get('drone_seriennummer', '').strip(),
                         request.form.get('drone_reg_nr', '').strip()))
                    conn.commit()
                flash('Drohne hinzugefügt.', 'success')
            else:
                flash('Name ist erforderlich.', 'danger')

        elif action == 'edit_drone':
            did = request.form.get('drone_id')
            name = request.form.get('drone_name', '').strip()
            if name:
                with get_db() as conn:
                    conn.execute(
                        'UPDATE drones SET name=?, typ=?, seriennummer=?, reg_nr=? WHERE id=?',
                        (name,
                         request.form.get('drone_typ', '').strip(),
                         request.form.get('drone_seriennummer', '').strip(),
                         request.form.get('drone_reg_nr', '').strip(),
                         did))
                    conn.commit()
                flash('Drohne aktualisiert.', 'success')
            else:
                flash('Name ist erforderlich.', 'danger')

        elif action == 'delete_drone':
            did = request.form.get('drone_id')
            with get_db() as conn:
                conn.execute('DELETE FROM drones WHERE id=?', (did,))
                conn.commit()
            flash('Drohne gelöscht.', 'success')

        elif action == 'add_sendeformat':
            name = request.form.get('sf_name', '').strip()
            if name:
                with get_db() as conn:
                    try:
                        conn.execute('INSERT INTO sendeformate(name) VALUES(?)', (name,))
                        conn.commit()
                        flash('Sendeformat hinzugefügt.', 'success')
                    except Exception:
                        flash('Sendeformat bereits vorhanden.', 'danger')
            else:
                flash('Name ist erforderlich.', 'danger')

        elif action == 'edit_sendeformat':
            sfid = request.form.get('sf_id')
            name = request.form.get('sf_name', '').strip()
            if name:
                with get_db() as conn:
                    conn.execute('UPDATE sendeformate SET name=? WHERE id=?', (name, sfid))
                    conn.commit()
                flash('Sendeformat aktualisiert.', 'success')
            else:
                flash('Name ist erforderlich.', 'danger')

        elif action == 'delete_sendeformat':
            sfid = request.form.get('sf_id')
            with get_db() as conn:
                conn.execute('DELETE FROM sendeformate WHERE id=?', (sfid,))
                conn.commit()
            flash('Sendeformat gelöscht.', 'success')

        elif action == 'add_verwendungszweck':
            name = request.form.get('vz_name', '').strip()
            if name:
                with get_db() as conn:
                    try:
                        conn.execute('INSERT INTO verwendungszwecke(name) VALUES(?)', (name,))
                        conn.commit()
                        flash('Verwendungszweck hinzugefügt.', 'success')
                    except Exception:
                        flash('Verwendungszweck bereits vorhanden.', 'danger')
            else:
                flash('Name ist erforderlich.', 'danger')

        elif action == 'edit_verwendungszweck':
            vzid = request.form.get('vz_id')
            name = request.form.get('vz_name', '').strip()
            if name:
                with get_db() as conn:
                    conn.execute('UPDATE verwendungszwecke SET name=? WHERE id=?', (name, vzid))
                    conn.commit()
                flash('Verwendungszweck aktualisiert.', 'success')
            else:
                flash('Name ist erforderlich.', 'danger')

        elif action == 'delete_verwendungszweck':
            vzid = request.form.get('vz_id')
            with get_db() as conn:
                conn.execute('DELETE FROM verwendungszwecke WHERE id=?', (vzid,))
                conn.commit()
            flash('Verwendungszweck gelöscht.', 'success')

        return redirect(url_for('admin'))

    with get_db() as conn:
        users = conn.execute(
            'SELECT id,username,is_admin,email,is_approved FROM users WHERE is_approved=1').fetchall()
        pending = conn.execute(
            'SELECT u.id, u.username, u.email, p.pilot_name '
            'FROM users u LEFT JOIN profiles p ON u.id = p.user_id '
            'WHERE u.is_approved=0').fetchall()
        drones = conn.execute('SELECT * FROM drones ORDER BY name').fetchall()
        sendeformate = conn.execute('SELECT * FROM sendeformate ORDER BY id').fetchall()
        verwendungszwecke = conn.execute('SELECT * FROM verwendungszwecke ORDER BY id').fetchall()
    smtp_settings = {k: get_setting(k) for k in ['smtp_host','smtp_port','smtp_user','smtp_pass','smtp_from']}
    test_settings = {
        'test_mode': test_mode_on(),
        'test_email': get_setting('test_email', 'test@dronenerds.ch'),
    }
    notify_settings = {
        'feedback_email': get_setting('feedback_email', 'feedback@dronenerds.ch'),
        'admin_email': get_setting('admin_email', 'info@dronenerds.ch'),
    }
    return render_template('admin.html', users=users, pending=pending, smtp=smtp_settings,
                           test=test_settings, notify=notify_settings, drones=drones,
                           sendeformate=sendeformate, verwendungszwecke=verwendungszwecke)


# Initialisierung beim Start (sowohl via Gunicorn als auch python3 app.py)
init_db()
with get_db() as conn:
    count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    if count == 0:
        conn.execute('INSERT INTO users(username,password_hash,is_admin) VALUES(?,?,1)',
                     ('admin', _hash('drohnen2024')))
        conn.commit()
        print('Standard-Admin erstellt: admin / drohnen2024')

if __name__ == '__main__':
    app.run(debug=True, port=5050)
