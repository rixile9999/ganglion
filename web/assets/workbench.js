/* ============================================================
   GANGLION WORKBENCH  ·  focus-pane orchestration
   ============================================================ */

(function () {
  'use strict';

  // ---- focus stage handling -----------------------------------
  const stages = document.querySelectorAll('[data-stage]');
  const focusCtxs = document.querySelectorAll('.fctx');
  const triadCells = document.querySelectorAll('.triad-mini__cell');

  function setFocus(id) {
    stages.forEach((s) => {
      s.classList.toggle('is-active', s.dataset.stage === id);
    });
    triadCells.forEach((c) => {
      const targets = (c.dataset.target || '').split(',');
      c.classList.toggle('active', targets.includes(id));
    });
    focusCtxs.forEach((c) => {
      c.classList.toggle('is-active', c.dataset.ctx === id);
    });
    // sync header
    const titleEl = document.querySelector('[data-focus-title]');
    const kindEl = document.querySelector('[data-focus-kind]');
    const subEl = document.querySelector('[data-focus-sub]');
    const ctx = document.querySelector(`.fctx[data-ctx="${id}"]`);
    if (ctx && titleEl) {
      titleEl.innerHTML = ctx.dataset.title || id;
      if (kindEl) kindEl.innerHTML = ctx.dataset.kind || '';
      if (subEl) subEl.textContent = ctx.dataset.sub || '';
    }
    // reset tab to first
    const tabs = document.querySelectorAll(`.fctx[data-ctx="${id}"] .fp__tab`);
    if (tabs.length) {
      tabs.forEach((t) => t.classList.remove('active'));
      tabs[0].classList.add('active');
      const subs = document.querySelectorAll(`.fctx[data-ctx="${id}"] .fctx__sub`);
      subs.forEach((s) => s.classList.remove('is-active'));
      const first = document.querySelector(`.fctx[data-ctx="${id}"] .fctx__sub`);
      if (first) first.classList.add('is-active');
    }
  }

  stages.forEach((s) => {
    s.addEventListener('click', (e) => { setFocus(s.dataset.stage); });
  });
  triadCells.forEach((c) => {
    c.addEventListener('click', () => {
      const t = (c.dataset.target || '').split(',')[0];
      if (t) setFocus(t);
    });
  });

  // ---- tabs ----------------------------------------------------
  document.addEventListener('click', (e) => {
    const tab = e.target.closest('.fp__tab');
    if (!tab) return;
    const ctx = tab.closest('.fctx');
    if (!ctx) return;
    ctx.querySelectorAll('.fp__tab').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    const key = tab.dataset.sub;
    ctx.querySelectorAll('.fctx__sub').forEach((s) => {
      s.classList.toggle('is-active', s.dataset.sub === key);
    });
  });

  // ---- toggles -------------------------------------------------
  document.querySelectorAll('.toggle:not(.lock)').forEach((t) => {
    t.addEventListener('click', () => t.classList.toggle('on'));
  });

  // ---- seg controls -------------------------------------------
  document.querySelectorAll('.seg').forEach((seg) => {
    seg.addEventListener('click', (e) => {
      const opt = e.target.closest('.seg__opt');
      if (!opt) return;
      seg.querySelectorAll('.seg__opt').forEach((o) => o.classList.remove('active'));
      opt.classList.add('active');
    });
  });

  // ---- default focus ------------------------------------------
  setFocus('pipeline');
})();
