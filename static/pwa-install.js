let deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  document.dispatchEvent(new CustomEvent('pwa-install-available'));
});

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
}

function isIOS() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

async function triggerPwaInstall() {
  if (isStandalone()) return { status: 'already' };
  if (deferredInstallPrompt) {
    deferredInstallPrompt.prompt();
    const { outcome } = await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    return { status: outcome };
  }
  if (isIOS()) return { status: 'ios-guide' };
  return { status: 'manual-guide' };
}

window.PwaInstall = { isStandalone, isIOS, triggerPwaInstall };
