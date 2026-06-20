'use strict';

// ── Geolocation + Weather + Reverse Geocoding ─────────────────────────────

const btn = document.getElementById('btnLocation');
const statusTopBox = document.getElementById('locationStatusTop');
const statusTopText = document.getElementById('locationStatusTopText');
const spinnerTop = document.getElementById('locationSpinnerTop');

const weatherStatusBox = document.getElementById('weatherStatus');
const weatherStatusText = document.getElementById('weatherStatusText');
const weatherSpinner = document.getElementById('weatherSpinner');

// Legacy status in Drehort section (map picker etc.)
const statusBox = document.getElementById('locationStatus');
const statusText = document.getElementById('locationStatusText');
const spinner = document.getElementById('locationSpinner');

function setAutoFilled(el) {
  if (!el) return;
  el.classList.add('auto-filled');
  setTimeout(() => el.classList.remove('auto-filled'), 2000);
}

function setField(id, value) {
  const el = document.getElementById(id);
  if (el && value) {
    el.value = value;
    setAutoFilled(el);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

function setTextarea(id, value) {
  const el = document.getElementById(id);
  if (el && value) {
    el.value = value;
    setAutoFilled(el);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

function showStatusTop(msg, done = false) {
  if (!statusTopBox) return;
  statusTopBox.classList.remove('d-none');
  if (statusTopText) statusTopText.textContent = msg;
  if (spinnerTop) spinnerTop.classList.toggle('d-none', done);
}

function showWeatherStatus(msg, done = false) {
  if (!weatherStatusBox) return;
  weatherStatusBox.classList.remove('d-none');
  if (weatherStatusText) weatherStatusText.textContent = msg;
  if (weatherSpinner) weatherSpinner.classList.toggle('d-none', done);
}

function showStatus(msg, done = false) {
  if (!statusBox) return;
  statusBox.classList.remove('d-none');
  if (statusText) statusText.textContent = msg;
  if (spinner) spinner.classList.toggle('d-none', done);
}

function requestLocation() {
  if (!navigator.geolocation) {
    alert('Ihr Browser unterstützt keine Geolokalisierung.');
    return;
  }
  showStatusTop('Standort wird ermittelt…');
  if (btn) btn.disabled = true;

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      showStatusTop('Standort gefunden – Wetter und Adresse werden abgerufen…');

      fetch(`/api/location-data?lat=${lat}&lon=${lon}`)
        .then(r => r.json())
        .then(data => {
          setField('drehort', data.location);
          setField('koordinaten', data.coordinates);
          setField('ort_eva', data.location_short || data.location);
          setField('ort_pilot', data.location_short || data.location);
          setTextarea('wetterlage', data.weather);
          showStatusTop('Standort, Koordinaten und Wetterlage erfolgreich ermittelt.', true);
        })
        .catch(() => {
          showStatusTop('Fehler beim Abrufen der Wetterdaten.', true);
        })
        .finally(() => {
          if (btn) btn.disabled = false;
        });
    },
    (err) => {
      let msg = 'Standort konnte nicht ermittelt werden.';
      if (err.code === 1) msg = 'Standortzugriff verweigert. Bitte in Browser-Einstellungen erlauben.';
      if (err.code === 2) msg = 'Standort nicht verfügbar.';
      showStatusTop(msg, true);
      if (btn) btn.disabled = false;
    },
    { enableHighAccuracy: true, timeout: 15000 }
  );
}

if (btn) btn.addEventListener('click', requestLocation);

// ── Nur Wetter einfügen (ohne Standort-/Koordinaten-Felder) ──────────────

(function () {
  const btnW = document.getElementById('btnWeatherOnly');
  if (!btnW) return;
  btnW.addEventListener('click', function () {
    if (!navigator.geolocation) { alert('Geolokalisierung nicht verfügbar.'); return; }
    showWeatherStatus('Wetterdaten werden ermittelt…');
    btnW.disabled = true;
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        fetch('/api/location-data?lat=' + pos.coords.latitude + '&lon=' + pos.coords.longitude)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            setTextarea('wetterlage', data.weather);
            showWeatherStatus('Wetterlage erfolgreich ermittelt.', true);
          })
          .catch(function () {
            showWeatherStatus('Fehler beim Abrufen der Wetterdaten.', true);
          })
          .finally(function () { btnW.disabled = false; });
      },
      function () {
        showWeatherStatus('Standort konnte nicht ermittelt werden.', true);
        btnW.disabled = false;
      },
      { enableHighAccuracy: false, timeout: 10000 }
    );
  });
})();

// ── Aircraft dropdown ─────────────────────────────────────────────────────

const aircraftSelect = document.getElementById('aircraftSelect');
if (aircraftSelect) {
  function fillAircraftFields() {
    const opt = aircraftSelect.options[aircraftSelect.selectedIndex];
    if (!opt || !opt.value) return;
    const map = {
      droneBrand: opt.dataset.brand,
      droneType:  opt.dataset.type,
      droneReg:   opt.dataset.reg,
      droneEquip: opt.dataset.equip,
    };
    Object.entries(map).forEach(([id, val]) => {
      const el = document.getElementById(id);
      if (el && val !== undefined) { el.value = val; setAutoFilled(el); }
    });
  }
  aircraftSelect.addEventListener('change', fillAircraftFields);
  if (aircraftSelect.value) fillAircraftFields();
}

// ── Sendeformat dropdown → text field ────────────────────────────────────

const sfSelect = document.getElementById('sendeformatSelect');
const sfInput  = document.getElementById('sendeformat');
if (sfSelect && sfInput) {
  sfSelect.addEventListener('change', () => {
    if (sfSelect.value) { sfInput.value = sfSelect.value; setAutoFilled(sfInput); }
  });
}

// ── Flight duration auto-calculation ─────────────────────────────────────

const startInput   = document.getElementById('startzeit');
const landInput    = document.getElementById('landezeit');
const minutesInput = document.getElementById('flugminuten');

function calcMinutes() {
  if (!startInput || !landInput || !minutesInput) return;
  const [sh, sm] = startInput.value.split(':').map(Number);
  const [lh, lm] = landInput.value.split(':').map(Number);
  if (isNaN(sh) || isNaN(lh)) return;
  const diff = (lh * 60 + lm) - (sh * 60 + sm);
  if (diff > 0) { minutesInput.value = diff; setAutoFilled(minutesInput); minutesInput.dispatchEvent(new Event('input', { bubbles: true })); }
}

function calcLandingTime() {
  if (!startInput || !minutesInput || !landInput) return;
  const [sh, sm] = startInput.value.split(':').map(Number);
  const mins = parseInt(minutesInput.value, 10);
  if (isNaN(sh) || isNaN(mins) || mins <= 0) return;
  const total = sh * 60 + sm + mins;
  const lh = Math.floor(total / 60) % 24;
  const lm = total % 60;
  landInput.value = String(lh).padStart(2, '0') + ':' + String(lm).padStart(2, '0');
  setAutoFilled(landInput);
  landInput.dispatchEvent(new Event('input', { bubbles: true }));
}

startInput?.addEventListener('change', () => {
  if (landInput?.value) calcMinutes();
  else if (minutesInput?.value) calcLandingTime();
});
landInput?.addEventListener('change', calcMinutes);
minutesInput?.addEventListener('change', calcLandingTime);

// ── Drehdatum → Datum-Felder in Abschnitt 4 synchronisieren ──────────────

const drehdatumInput = document.getElementById('drehdatum');
const datumEva   = document.getElementById('datum_eva');
const datumPilot = document.getElementById('datum_pilot');

if (drehdatumInput) {
  drehdatumInput.addEventListener('change', () => {
    const v = drehdatumInput.value;
    if (datumEva   && !datumEva._manuallyChanged)   { datumEva.value   = v; datumEva.dispatchEvent(new Event('input', { bubbles: true })); }
    if (datumPilot && !datumPilot._manuallyChanged) { datumPilot.value = v; datumPilot.dispatchEvent(new Event('input', { bubbles: true })); }
  });
  [datumEva, datumPilot].forEach(el => {
    el?.addEventListener('change', () => { el._manuallyChanged = true; });
  });
}
