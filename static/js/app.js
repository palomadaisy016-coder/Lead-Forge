/* ==========================================================================
   LEAD FORGE — theme engine + navigation
   --------------------------------------------------------------------------
   No frameworks, no build step. Two jobs:
     1. Theme engine: every appearance setting is a data-attribute on <html>,
        saved in this browser so it survives refreshes. When accounts sync
        preferences server-side, saveTheme() is the single place to also
        POST it to the server.
     2. Navigation: desktop rail collapse, mobile drawer, bottom-tab "More"
        sheet, and the appearance panel.
   ========================================================================== */

(function () {
  'use strict';

  var root = document.documentElement;
  var STORE = 'lf.theme';

  /* ---------- accent presets ---------- */
  var ACCENTS = [
    { id: 'forge',   name: 'Forge Blue', a: '#2563ff', b: '#6c3ff2' },
    { id: 'ocean',   name: 'Ocean',      a: '#0ea5e9', b: '#2563eb' },
    { id: 'violet',  name: 'Violet',     a: '#7c3aed', b: '#c026d3' },
    { id: 'emerald', name: 'Emerald',    a: '#059669', b: '#0d9488' },
    { id: 'cyan',    name: 'Cyan',       a: '#06b6d4', b: '#3b82f6' },
    { id: 'rose',    name: 'Rose',       a: '#e11d48', b: '#f43f5e' },
    { id: 'amber',   name: 'Amber',      a: '#d97706', b: '#ea580c' },
    { id: 'slate',   name: 'Slate',      a: '#475569', b: '#64748b' }
  ];

  var DEFAULTS = {
    mode: 'system', accentId: 'forge',
    accent: '#2563ff', accent2: '#6c3ff2',
    accentRgb: '37, 99, 255', accent2Rgb: '108, 63, 242',
    glass: 'balanced', blur: 'medium', border: 'medium', shadow: 'medium',
    radius: 'round', density: 'comfortable', bg: 'gradient', motion: 'full'
  };

  var settings = load();

  function load() {
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem(STORE) || '{}'); } catch (e) {}
    var out = {};
    for (var k in DEFAULTS) out[k] = validate(k, saved[k]);
    return out;
  }

  function validate(key, value) {
    if (typeof value !== 'string') return DEFAULTS[key];
    if (key === 'accent' || key === 'accent2') {
      return /^#[0-9a-f]{6}$/i.test(value) ? value : DEFAULTS[key];
    }
    if (key === 'accentRgb' || key === 'accent2Rgb') {
      return /^\d{1,3}, \d{1,3}, \d{1,3}$/.test(value) ? value : DEFAULTS[key];
    }
    return value;
  }

  function hexToRgb(hex) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!m) return null;
    return parseInt(m[1], 16) + ', ' + parseInt(m[2], 16) + ', ' + parseInt(m[3], 16);
  }

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(settings)); } catch (e) {}
  }

  function apply() {
    var dark = settings.mode === 'system'
      ? matchMedia('(prefers-color-scheme: dark)').matches
      : settings.mode === 'dark';

    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    ['glass', 'blur', 'border', 'shadow', 'radius', 'density', 'bg', 'motion']
      .forEach(function (k) { root.setAttribute('data-' + k, settings[k]); });

    root.style.setProperty('--accent', settings.accent);
    root.style.setProperty('--accent-2', settings.accent2);
    root.style.setProperty('--accent-rgb', settings.accentRgb);
    root.style.setProperty('--accent-2-rgb', settings.accent2Rgb);

    syncControls();
  }

  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
    if (settings.mode === 'system') apply();
  });

  var swatchWrap = document.getElementById('swatches');

  if (swatchWrap) {
    ACCENTS.forEach(function (preset) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'swatch';
      b.dataset.accent = preset.id;
      b.title = preset.name;
      b.setAttribute('aria-label', 'Accent: ' + preset.name);
      b.style.background = 'linear-gradient(135deg,' + preset.a + ',' + preset.b + ')';
      b.addEventListener('click', function () {
        settings.accentId = preset.id;
        settings.accent = preset.a;
        settings.accent2 = preset.b;
        settings.accentRgb = hexToRgb(preset.a);
        settings.accent2Rgb = hexToRgb(preset.b);
        save(); apply();
      });
      swatchWrap.appendChild(b);
    });

    var custom = document.createElement('input');
    custom.type = 'color';
    custom.className = 'swatch-custom';
    custom.title = 'Custom colour';
    custom.setAttribute('aria-label', 'Custom accent colour');
    custom.value = settings.accent;
    custom.addEventListener('input', function () {
      var rgb = hexToRgb(custom.value);
      if (!rgb) return;
      settings.accentId = 'custom';
      settings.accent = custom.value;
      settings.accent2 = custom.value;
      settings.accentRgb = rgb;
      settings.accent2Rgb = rgb;
      save(); apply();
    });
    swatchWrap.appendChild(custom);
  }

  document.querySelectorAll('.seg[data-setting]').forEach(function (seg) {
    var key = seg.dataset.setting;
    seg.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        settings[key] = btn.dataset.value;
        save(); apply();
      });
    });
  });

  function syncControls() {
    document.querySelectorAll('.seg[data-setting]').forEach(function (seg) {
      var key = seg.dataset.setting;
      seg.querySelectorAll('button').forEach(function (btn) {
        btn.setAttribute('aria-pressed', String(btn.dataset.value === settings[key]));
      });
    });
    document.querySelectorAll('.swatch').forEach(function (s) {
      s.setAttribute('aria-pressed', String(s.dataset.accent === settings.accentId));
    });
    var label = document.getElementById('accent-name');
    if (label) {
      var found = ACCENTS.filter(function (p) { return p.id === settings.accentId; })[0];
      label.textContent = found ? found.name : 'Custom';
    }
    var icon = document.getElementById('theme-quick-icon');
    if (icon) {
      icon.setAttribute('href', root.getAttribute('data-theme') === 'dark' ? '#i-sun' : '#i-moon');
    }
  }

  apply();

  var reset = document.getElementById('studio-reset');
  if (reset) reset.addEventListener('click', function () {
    settings = JSON.parse(JSON.stringify(DEFAULTS));
    save(); apply();
  });

  var quick = document.getElementById('theme-quick');
  if (quick) quick.addEventListener('click', function () {
    settings.mode = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    save(); apply();
  });

  var rail = document.getElementById('rail');
  var railToggle = document.getElementById('rail-toggle');
  var drawer = document.getElementById('drawer');
  var scrim = document.getElementById('scrim');

  function isMobile() { return matchMedia('(max-width: 899px)').matches; }

  var openSurface = null;

  function openPanel(el) {
    if (!el) return;
    closePanel();
    openSurface = el;
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    scrim.hidden = false;
    requestAnimationFrame(function () { scrim.classList.add('open'); });
    document.body.style.overflow = 'hidden';
    var first = el.querySelector('button, a, input');
    if (first) first.focus({ preventScroll: true });
  }

  function closePanel() {
    if (!openSurface) return;
    openSurface.classList.remove('open');
    openSurface.setAttribute('aria-hidden', 'true');
    openSurface = null;
    scrim.classList.remove('open');
    setTimeout(function () { if (!openSurface) scrim.hidden = true; }, 260);
    document.body.style.overflow = '';
  }

  if (scrim) scrim.addEventListener('click', closePanel);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closePanel();
    if (e.key === '/' && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
      e.preventDefault();
      var s = document.getElementById('global-search');
      if (s) s.focus();
    }
  });

  if (railToggle) railToggle.addEventListener('click', function () {
    if (isMobile()) {
      openPanel(drawer);
    } else {
      rail.classList.toggle('collapsed');
      var collapsed = rail.classList.contains('collapsed');
      railToggle.setAttribute('aria-label', collapsed ? 'Expand navigation' : 'Collapse navigation');
      try { localStorage.setItem('lf.rail', collapsed ? '1' : '0'); } catch (e) {}
    }
  });

  try {
    if (rail && localStorage.getItem('lf.rail') === '1') rail.classList.add('collapsed');
  } catch (e) {}

  var drawerClose = document.getElementById('drawer-close');
  if (drawerClose) drawerClose.addEventListener('click', closePanel);

  var moreOpen = document.getElementById('more-open');
  if (moreOpen) moreOpen.addEventListener('click', function () {
    openPanel(document.getElementById('more-sheet'));
  });

  var studioOpen = document.getElementById('studio-open');
  if (studioOpen) studioOpen.addEventListener('click', function () {
    openPanel(document.getElementById('studio'));
    studioOpen.setAttribute('aria-expanded', 'true');
  });

  var studioDone = document.getElementById('studio-done');
  if (studioDone) studioDone.addEventListener('click', function () {
    closePanel();
    if (studioOpen) studioOpen.setAttribute('aria-expanded', 'false');
  });
})();
