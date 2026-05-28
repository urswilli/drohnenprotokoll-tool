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

def _hash(pw): return generate_password_hash(pw, method='pbkdf2:sha256')
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'drohnen-protokoll-srg-2024-secret')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PATH = os.path.join(BASE_DIR, 'SRG_Weisung und Checkliste für den Einsatz von Drohnen_V500e.pdf')
DB_PATH = os.path.join(BASE_DIR, 'drohnen.db')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')

os.makedirs(OUTPUT_DIR, exist_ok=True)

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
                is_admin INTEGER DEFAULT 0
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

        # Signature section
        'Info.35': form_data.get('ort_eva', ''),
        'Info.36': form_data.get('datum_eva', today),
        'Info.37': form_data.get('ort_pilot', ''),
        'Info.38': form_data.get('datum_pilot', today),
        'Text13':  form_data.get('eva_signature_email', '') or form_data.get('eva_email', ''),
        'Text15':  form_data.get('pilot_signature', '') or form_data.get('pilot_email', ''),

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
        'Info.33': form_data.get('eva_name', ''),     # "Vorname, Nachname" unter E-Mail (EVA)
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
    smtp_from = form_data.get('pilot_email', '') or get_setting('smtp_from') or smtp_user

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

    msg = MIMEMultipart()
    msg['From'] = smtp_from
    msg['To'] = 'drohnen@srf.ch; eng-service@srf.ch'
    cc_list = [e for e in [eva_email, pilot_email] if e]
    if cc_list:
        msg['Cc'] = '; '.join(cc_list)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    with open(pdf_path, 'rb') as f:
        attachment = MIMEApplication(f.read(), _subtype='pdf')
        attachment.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(attachment)

    try:
        recipients = ['drohnen@srf.ch', 'eng-service@srf.ch'] + cc_list
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
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
            return redirect(url_for('index'))
        flash('Ungültiger Benutzername oder Passwort.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


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

        elif action == 'create_user':
            username = request.form.get('new_username', '').strip()
            password = request.form.get('new_password', '')
            is_admin = 1 if request.form.get('new_is_admin') else 0
            if username and len(password) >= 6:
                try:
                    with get_db() as conn:
                        conn.execute('INSERT INTO users(username,password_hash,is_admin) VALUES(?,?,?)',
                                     (username, _hash(password), is_admin))
                        conn.commit()
                    flash(f'Benutzer {username!r} erstellt.', 'success')
                except sqlite3.IntegrityError:
                    flash('Benutzername bereits vergeben.', 'danger')
            else:
                flash('Benutzername und Passwort (min. 6 Zeichen) erforderlich.', 'danger')

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
        users = conn.execute('SELECT id,username,is_admin FROM users').fetchall()
        drones = conn.execute('SELECT * FROM drones ORDER BY name').fetchall()
        sendeformate = conn.execute('SELECT * FROM sendeformate ORDER BY id').fetchall()
        verwendungszwecke = conn.execute('SELECT * FROM verwendungszwecke ORDER BY id').fetchall()
    smtp_settings = {k: get_setting(k) for k in ['smtp_host','smtp_port','smtp_user','smtp_pass','smtp_from']}
    return render_template('admin.html', users=users, smtp=smtp_settings, drones=drones,
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
