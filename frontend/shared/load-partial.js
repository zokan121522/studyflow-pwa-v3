/* ============================== LOAD PARTIAL ============================== */

// Simple utility to load HTML partials via fetch
async function loadPartial(elementId, url) {
  const el = document.getElementById(elementId);
  if (!el) {
    console.warn(`[loadPartial] Element #${elementId} not found`);
    return;
  }
  
  try {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`${resp.status}`);
    const html = await resp.text();
    el.innerHTML = html;
  } catch (e) {
    console.error(`[loadPartial] Failed to load ${url}:`, e);
    el.innerHTML = `<div class="empty-state">Error al cargar: ${e.message}</div>`;
  }
}

// Auto-load partials marked with data-partial attribute
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-partial]').forEach(el => {
    loadPartial(el.id, el.dataset.partial);
  });
});

window.loadPartial = loadPartial;

console.log('[Shared] load-partial.js loaded');
