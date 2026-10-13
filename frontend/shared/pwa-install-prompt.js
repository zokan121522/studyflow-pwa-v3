// In-app PWA install banner.
//
// Chrome hides the install affordance in the ⋮ menu, which non-technical
// users never find. When the browser fires `beforeinstallprompt` — i.e. it
// has decided the app is installable — we surface a small banner with a
// direct [Instalar] button and reuse the browser's own prompt.
//
// Supersedes the bare floating button that used to live in app.js: that one
// only showed "📥 Instalar App" with no explanation, and its dismiss flag
// (`installPromptDismissed`) is retired in favour of `sf-pwa-install-dismissed`.
(function () {
  'use strict';

  var DISMISS_KEY = 'sf-pwa-install-dismissed';
  var INSTALLED_KEY = 'sf-pwa-installed';
  var SHOW_DELAY_MS = 2500;

  var deferredPrompt = null;
  var banner = null;
  var showTimer = null;

  function isStandalone() {
    return (
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true
    );
  }

  function storedFlag(key) {
    try {
      return localStorage.getItem(key) === '1';
    } catch (err) {
      return false;
    }
  }

  function setFlag(key) {
    try {
      localStorage.setItem(key, '1');
    } catch (err) {
      /* private mode / storage disabled — banner still works this session */
    }
  }

  function suppress() {
    return isStandalone() || storedFlag(DISMISS_KEY) || storedFlag(INSTALLED_KEY);
  }

  function hide() {
    if (banner && banner.parentNode) {
      banner.parentNode.removeChild(banner);
    }
    banner = null;
  }

  function build() {
    var el = document.createElement('div');
    el.className = 'pwa-install-banner';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Instalar StudyFlow');

    var icon = document.createElement('span');
    icon.className = 'pwa-install-banner__icon';
    icon.textContent = '📲';

    var text = document.createElement('span');
    text.className = 'pwa-install-banner__text';
    text.textContent = '¿Quieres una mejor experiencia? Instala StudyFlow desde aquí';

    var install = document.createElement('button');
    install.type = 'button';
    install.className = 'pwa-install-banner__install';
    install.textContent = 'Instalar';

    var dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'pwa-install-banner__dismiss';
    dismiss.setAttribute('aria-label', 'Descartar');
    dismiss.textContent = '✕';

    install.addEventListener('click', onInstall);
    dismiss.addEventListener('click', onDismiss);

    el.appendChild(icon);
    el.appendChild(text);
    el.appendChild(install);
    el.appendChild(dismiss);
    return el;
  }

  function show() {
    if (suppress() || banner || !document.body) return;
    banner = build();
    document.body.appendChild(banner);
  }

  function onInstall() {
    if (!deferredPrompt) {
      hide();
      return;
    }
    var prompt = deferredPrompt;
    deferredPrompt = null;
    prompt.prompt();
    Promise.resolve(prompt.userChoice)
      .catch(function () {
        return { outcome: 'dismissed' };
      })
      .then(function () {
        // The deferred prompt is single-use: either way the banner has done
        // its job, and an unanswered one would only leave a dead button.
        hide();
      });
  }

  function onDismiss() {
    hide();
    setFlag(DISMISS_KEY);
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    if (suppress()) return;
    deferredPrompt = e;
    if (showTimer) clearTimeout(showTimer);
    showTimer = setTimeout(show, SHOW_DELAY_MS);
  });

  window.addEventListener('appinstalled', function () {
    hide();
    deferredPrompt = null;
    setFlag(INSTALLED_KEY);
  });
})();
