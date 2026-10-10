/* StudyFlow downloads — verificación humana (slide-to-download) */
(function () {
  'use strict';

  var overlay = null, track = null, knob = null, fill = null, label = null;
  var fileEl = null, cb = null, dlBtn = null;
  var currentHref = '';
  var dragging = false, startX = 0, startOffset = 0, offset = 0, maxX = 0;
  var slided = false;
  var THRESHOLD = 0.95;

  function checkBoth() {
    if (!dlBtn) return;
    var ok = slided && cb.checked;
    dlBtn.disabled = !ok;
    dlBtn.classList.toggle('on', ok);
  }

  function zipHref(target) {
    var el = target;
    while (el && el.nodeType === 1) {
      if (el.tagName === 'A') {
        var h = el.getAttribute('href');
        if (h && /\.zip$/i.test(h.split(/[?#]/)[0])) return h;
      }
      el = el.parentNode;
    }
    return null;
  }

  function basename(u) {
    return (u.split(/[?#]/)[0].split('/').pop()) || u;
  }

  function build() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.className = 'hc-overlay';
    overlay.innerHTML =
      '<div class="hc-card" role="dialog" aria-modal="true" aria-label="Verificación humana">' +
        '<button type="button" class="hc-close" aria-label="Cancelar verificación">✕</button>' +
        '<h2 class="hc-title">Verificación humana</h2>' +
        '<p class="hc-sub">Desliza el botón para iniciar la descarga</p>' +
        '<div class="hc-file"></div>' +
        '<div class="hc-track">' +
          '<div class="hc-fill"></div>' +
          '<span class="hc-label">Desliza para descargar →</span>' +
          '<div class="hc-knob" role="slider" aria-label="Desliza para descargar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">→</div>' +
        '</div>' +
        '<div class="hc-fallback">' +
          '<label class="hc-fallback-lab"><input type="checkbox"> No soy un robot</label>' +
          '<button type="button" class="hc-dl" disabled>Descargar</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    track = overlay.querySelector('.hc-track');
    knob = overlay.querySelector('.hc-knob');
    fill = overlay.querySelector('.hc-fill');
    label = overlay.querySelector('.hc-label');
    fileEl = overlay.querySelector('.hc-file');
    cb = overlay.querySelector('input[type="checkbox"]');
    dlBtn = overlay.querySelector('.hc-dl');

    overlay.querySelector('.hc-close').addEventListener('click', close);
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) close(); // clic fuera de la tarjeta
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && overlay && overlay.classList.contains('open')) close();
    });

    cb.addEventListener('change', function () {
      checkBoth();
    });
    dlBtn.addEventListener('click', function () {
      if (dlBtn.disabled) return;
      go();
    });

    knob.addEventListener('pointerdown', onDown);
    knob.addEventListener('pointermove', onMove);
    knob.addEventListener('pointerup', onUp);
    knob.addEventListener('pointercancel', onUp);
  }

  function calcMax() {
    maxX = Math.max(0, track.clientWidth - knob.offsetWidth - 8); // 4px a cada lado
  }

  function render() {
    knob.style.transform = 'translateX(' + offset + 'px)';
    knob.setAttribute('aria-valuenow', String(Math.round(maxX ? (offset / maxX) * 100 : 0)));
    var w = offset + knob.offsetWidth + 4;
    fill.style.width = Math.min(w, track.clientWidth) + 'px';
  }

  function onDown(e) {
    if (track.classList.contains('done')) return; // ya verificado
    dragging = true;
    calcMax();
    startX = e.clientX;
    startOffset = offset;
    if (knob.setPointerCapture) { try { knob.setPointerCapture(e.pointerId); } catch (err) {} }
    e.preventDefault();
  }

  function onMove(e) {
    if (!dragging) return;
    var dx = e.clientX - startX;
    offset = Math.min(maxX, Math.max(0, startOffset + dx));
    render();
    e.preventDefault();
  }

  function onUp(e) {
    if (!dragging) return;
    dragging = false;
    if (offset >= maxX * THRESHOLD) {
      offset = maxX;
      render();
      complete();
    } else {
      offset = 0;
      knob.style.transition = 'transform .25s ease';
      fill.style.transition = 'width .25s ease';
      render();
      setTimeout(function () {
        knob.style.transition = '';
        fill.style.transition = '';
      }, 280);
    }
    if (knob.releasePointerCapture) { try { knob.releasePointerCapture(e.pointerId); } catch (err) {} }
  }

  function complete() {
    track.classList.add('done');
    slided = true;
    checkBoth();
    label.textContent = '¡Verificado! Listo para descargar';
    // no auto-go; require checkbox+button or still allow? but want BOTH: checkbox must be checked AND slided
    // if checkbox already checked, button enabled
  }

  function go() {
    var url = currentHref;
    close();
    if (url) window.location.href = url;
  }

  function open(href) {
    build();
    currentHref = href;
    fileEl.textContent = basename(href);
    track.classList.remove('done');
    label.textContent = 'Desliza para descargar →';
    offset = 0;
    knob.style.transform = 'translateX(0px)';
    fill.style.width = '0px';
    cb.checked = false;
    slided = false;
    checkBoth();
    overlay.classList.add('open'); // primero visible, luego medir el track
    document.body.style.overflow = 'hidden';
    calcMax();
    render();
  }

  function close() {
    if (!overlay) return;
    overlay.classList.remove('open');
    document.body.style.overflow = '';
    currentHref = '';
  }

  window.addEventListener('resize', function () {
    if (!overlay || !overlay.classList.contains('open')) return;
    calcMax();
    if (!track.classList.contains('done')) {
      offset = Math.min(offset, maxX);
      render();
    }
  });

  document.addEventListener('click', function (e) {
    if (e.defaultPrevented) return;
    if (e.button !== undefined && e.button !== 0) return; // solo click izquierdo
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return; // permitir abrir en nueva pestaña
    var href = zipHref(e.target);
    if (!href) return;
    e.preventDefault();
    open(href);
  });
})();
