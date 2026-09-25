/* PapAssist front end: reader + explanation panel. Plain JavaScript, no build step. */
(() => {
  'use strict';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const state = {
    pid: null, paper: null, units: {}, cardUnits: {}, keyToEls: new Map(), termToEls: new Map(),
    stack: [], preview: null, pins: [], hoverTimer: null, hoverToken: 0, lastHoverSig: null, settings: {},
    statusTimer: null, glossary: null,
  };

  // ------------------------------------------------------------------ utils
  const toast = (msg, ms = 3200) => { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(t._t); t._t = setTimeout(() => (t.hidden = true), ms); };
  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) { /* ignore */ } throw new Error(m); }
    return r.json();
  };
  const mjReady = () => (window.MathJax && MathJax.startup && MathJax.startup.promise) ? MathJax.startup.promise : new Promise((res) => setTimeout(() => mjReady().then(res), 100));
  async function typeset(els) {
    await mjReady();
    try { await MathJax.typesetPromise(els); } catch (e) { console.warn('MathJax', e); }
  }

  // ------------------------------------------------------------------ loading
  async function loadPaper(pid) {
    const status = $('#dz-status');
    status.textContent = 'Loading paper…';
    let paper;
    try { paper = await api(`/api/papers/${pid}`); } catch (e) { status.textContent = 'Could not load: ' + e.message; return; }
    state.pid = pid; state.paper = paper; state.units = paper.units || {}; state.settings = paper.settings || {};
    state.stack = []; state.preview = null; state.cardUnits = {}; state.glossary = null;
    location.hash = 'paper=' + pid;
    $('#paper-title').textContent = paper.title || pid;
    document.title = (paper.title ? paper.title + ' · ' : '') + 'PapAssist';
    renderLlmPill(paper.llm, paper.settings);
    loadPins();
    renderStack();

    const reader = $('#reader');
    reader.innerHTML = paper.blocks.map(blockHtml).join('');
    $('#dropzone').hidden = true; reader.hidden = false;
    renderToc(paper.toc);
    const prog = document.createElement('div'); prog.className = 'progress'; prog.textContent = 'Typesetting formulas…'; reader.prepend(prog);

    await mjReady();
    await defineMacros(paper.macros || {});
    const blocks = $$('.pa-block', reader);
    const batch = 24;
    for (let i = 0; i < blocks.length; i += batch) {
      await typeset(blocks.slice(i, i + batch));
      prog.textContent = `Typesetting formulas… ${Math.min(i + batch, blocks.length)}/${blocks.length}`;
    }
    prog.remove();
    buildIndexes();
    pollStatus();
  }

  async function defineMacros(macros) {
    const host = document.createElement('div'); host.className = 'pa-nomath-outside'; host.style.cssText = 'position:absolute;left:-9999px;top:0;height:0;overflow:hidden';
    document.body.appendChild(host);
    const parts = [];
    for (const [name, def] of Object.entries(macros)) {
      if (!/^[A-Za-z]+$/.test(name)) continue;
      let tex;
      if (Array.isArray(def)) {
        const [body, n, dflt] = def;
        tex = dflt !== undefined ? `\\newcommand{\\${name}}[${n}][${dflt}]{${body}}` : `\\newcommand{\\${name}}[${n}]{${body}}`;
      } else tex = `\\newcommand{\\${name}}{${def}}`;
      parts.push(`<span>\\(${esc(tex)}\\)</span>`);
    }
    host.innerHTML = parts.join('');
    await typeset([host]);
    host.remove();
  }

  function blockHtml(b) {
    const cls = ['pa-block', 'pa-' + b.kind, b.thm_kind ? 'k-' + b.thm_kind : ''].filter(Boolean).join(' ');
    const id = b.label_id && (b.kind === 'theorem' || b.kind === 'proof' || b.kind === 'figure') ? ` id="${esc(b.label_id)}"` : '';
    return `<section class="${cls}"${id} data-bid="${esc(b.id)}" data-kind="${esc(b.kind)}">${b.html}</section>`;
  }

  function renderToc(toc) {
    const el = $('#toc');
    el.innerHTML = '<div class="toc-title">Contents</div>' + (toc || []).filter((h) => h.level <= 3).map((h) => `<a href="#" data-jump="${esc(h.id)}" class="l${h.level}">${h.number ? esc(h.number) + ' ' : ''}${esc(h.text)}</a>`).join('');
  }

  function buildIndexes() {
    state.keyToEls = new Map(); state.termToEls = new Map();
    for (const el of $$('#reader mjx-container [class*="pa-u-"]')) {
      const uid = unitIdOf(el); if (!uid) continue;
      const info = state.units[uid]; if (!info) continue;
      const key = info[0];
      if (!state.keyToEls.has(key)) state.keyToEls.set(key, []);
      state.keyToEls.get(key).push(el);
    }
    for (const el of $$('#reader .pa-term')) {
      const k = el.dataset.term;
      if (!state.termToEls.has(k)) state.termToEls.set(k, []);
      state.termToEls.get(k).push(el);
    }
  }

  // ------------------------------------------------------------------ hover targets
  function unitIdOf(el) {
    for (const c of el.classList) { if (c.startsWith('pa-u-')) return c.slice(5); }
    return null;
  }
  function findTarget(node) {
    let el = node.nodeType === 1 ? node : node.parentElement;
    if (!el) return null;
    const card = el.closest('.card');
    const blockEl = el.closest('.pa-block');
    const block = blockEl ? blockEl.dataset.bid : (card ? card.dataset.block || null : null);
    const container = el.closest('mjx-container');
    if (container) {
      let cur = el; let unitEl = null;
      while (cur && cur !== container) { if (unitIdOf(cur)) { unitEl = cur; break; } cur = cur.parentElement; }
      if (unitEl) {
        const uid = unitIdOf(unitEl);
        const info = state.units[uid] || state.cardUnits[uid];
        return { type: 'unit', uid, key: info ? info[0] : null, tex: info ? info[3] : null, base: info ? info[2] : null, el: unitEl, block, inCard: !!card };
      }
      const mo = el.closest('mjx-mo');
      if (mo) {
        const c = mo.querySelector('mjx-c') || mo;
        for (const cls of c.classList) { const m = /^mjx-c([0-9A-F]+)$/i.exec(cls); if (m) return { type: 'op', char: String.fromCodePoint(parseInt(m[1], 16)), el: mo, block }; }
      }
      const mathSpan = el.closest('.pa-math');
      if (mathSpan && mathSpan.dataset.mid) return { type: 'formula', mid: mathSpan.dataset.mid, el: container, block };
      return null;
    }
    const term = el.closest('.pa-term'); if (term) return { type: 'term', term: term.dataset.term, el: term, block, inCard: !!card };
    const cite = el.closest('.pa-cite'); if (cite) return { type: 'cite', keys: cite.dataset.keys, el: cite, block };
    const ref = el.closest('.pa-ref'); if (ref) return { type: 'ref', label: ref.dataset.label, el: ref, block };
    return null;
  }
  function targetSig(t) { return t ? [t.type, t.uid || t.key || t.term || t.mid || t.char || t.keys || t.label, t.block].join('|') : ''; }
  function resolveUrl(t) {
    const q = new URLSearchParams();
    if (t.block) q.set('block', t.block);
    if (t.type === 'unit') { if (t.inCard) { q.set('key', t.key || ''); q.set('tex', t.tex || ''); if (t.base) q.set('base', t.base); } else q.set('uid', t.uid); }
    else if (t.type === 'term') q.set('term', t.term);
    else if (t.type === 'formula') q.set('mid', t.mid);
    else if (t.type === 'op') q.set('op', t.char);
    else if (t.type === 'cite') q.set('cite', t.keys);
    else if (t.type === 'ref') q.set('label', t.label);
    return `/api/papers/${state.pid}/resolve?${q}`;
  }

  // ------------------------------------------------------------------ highlighting
  function clearHighlights() {
    for (const el of $$('.pa-hover, .pa-hl')) el.classList.remove('pa-hover', 'pa-hl');
  }
  function highlightTarget(t) {
    clearHighlights();
    if (!t || !t.el) return;
    t.el.classList.add('pa-hover');
    if (t.type === 'unit' && t.key && state.keyToEls.has(t.key)) for (const el of state.keyToEls.get(t.key)) el.classList.add('pa-hl');
    if (t.type === 'term' && state.termToEls.has(t.term)) for (const el of state.termToEls.get(t.term)) el.classList.add('pa-hl');
  }

  // ------------------------------------------------------------------ hover / click wiring
  function onMouseOver(e) {
    if (!state.pid) return;
    const t = findTarget(e.target);
    const sig = targetSig(t);
    if (sig === state.lastHoverSig) return;
    state.lastHoverSig = sig;
    clearTimeout(state.hoverTimer);
    if (!t) return;
    highlightTarget(t);
    state.hoverTimer = setTimeout(() => showPreview(t), 220);
  }
  async function showPreview(t) {
    const token = ++state.hoverToken;
    try {
      const card = await api(resolveUrl(t));
      if (token !== state.hoverToken) return;
      card._target = t;
      state.preview = card;
      renderStack();
    } catch (e) { console.warn(e); }
  }
  async function onClick(e) {
    if (!state.pid) return;
    const jump = e.target.closest('[data-jump]'); if (jump) { e.preventDefault(); jumpTo(jump.dataset.jump); return; }
    const ref = e.target.closest('a.pa-ref'); if (ref) { e.preventDefault(); const info = state.paper && ref.dataset.label; const t = { type: 'ref', label: ref.dataset.label, block: null }; pushTarget(t); return; }
    const t = findTarget(e.target);
    if (!t) return;
    if (t.type === 'ref') e.preventDefault();
    pushTarget(t);
  }
  async function pushTarget(t) {
    clearTimeout(state.hoverTimer);
    state.hoverToken++;
    try {
      let card = (state.preview && targetSig(state.preview._target) === targetSig(t)) ? state.preview : await api(resolveUrl(t));
      card._target = t;
      state.preview = null;
      state.stack.push(card);
      renderStack();
      $('.tab[data-tab="cards"]').click();
    } catch (e) { toast('Could not resolve: ' + e.message); }
  }
  function jumpTo(bid) {
    const el = $(`#reader [data-bid="${CSS.escape(bid)}"]`);
    if (!el) return toast('Block not on page');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  }

  // ------------------------------------------------------------------ cards
  function cardTitleHtml(card) {
    if (card.kind === 'symbol') return card.tex_html || `<span class="pa-math">\\(${esc(card.tex)}\\)</span>`;
    if (card.kind === 'operator') return `<span class="pa-math">\\(${esc(card.tex || card.char)}\\)</span> <span class="small">${esc(card.name || '')}</span>`;
    if (card.kind === 'term') return esc(card.display || card.term);
    if (card.kind === 'formula') return 'Formula';
    if (card.kind === 'block') return esc(card.heading || 'Block');
    if (card.kind === 'cite') return 'Reference' + (card.entries.length > 1 ? 's' : '');
    return '';
  }
  function cardTitleText(card) {
    if (card.kind === 'symbol') return '$' + card.tex + '$';
    if (card.kind === 'operator') return card.tex || card.char;
    if (card.kind === 'term') return card.display || card.term;
    if (card.kind === 'block') return card.heading || 'Block';
    return card.kind;
  }
  function srcBadge(label, cls) { return `<span class="src src-${esc(cls || 'unknown')}">${esc(label)}</span>`; }
  function locLink(loc) {
    if (!loc) return '';
    return `<a data-jump="${esc(loc.block)}" title="jump to it in the paper">${esc(loc.heading || loc.block)} ↗</a>`;
  }
  function confNote(c) { return c ? `<span class="conf">${c === 'high' ? 'explicit' : c === 'medium' ? 'likely' : 'inferred from wording'}</span>` : ''; }
  function occHtml(occ, label) {
    if (!occ || !occ.count) return '';
    const first = occ.first ? ` · first in ${locLink(occ.first)}` : '';
    return `<div class="small status-line">${label} appears ${occ.count}× in ${occ.blocks.length} block${occ.blocks.length === 1 ? '' : 's'}${first}</div>`;
  }

  function cardBodyHtml(card) {
    const llm = state.settings.llm_active;
    let h = '';
    if (card.kind === 'symbol') {
      const exact = card.entries.filter((e) => e.exact); const partial = card.entries.filter((e) => !e.exact);
      if (exact.length) h += exact.map(entryHtml).join('');
      if (!exact.length && !card.dictionary && !card.macro) {
        h += `<div class="status-line"><span class="status-none">Not defined in this paper's text</span>${llm ? '' : ''}</div>`;
      }
      if (card.dictionary) {
        h += `<div class="entry"><div class="meaning">${card.dictionary.meaning_html}</div><div class="srcline">${srcBadge('Standard notation', 'dictionary')} <span>${esc(card.dictionary.name)}${card.dictionary.exact ? '' : ' (matched the base symbol)'}</span></div></div>`;
      }
      if (card.macro) h += `<div class="small status-line">Produced by the paper's macro <code>\\${esc(card.macro.name)}</code> → <code>${esc(card.macro.body)}</code></div>`;
      if (partial.length) { h += `<h4>About the base symbol</h4>` + partial.map(entryHtml).join(''); }
      h += occHtml(card.occurrences, 'Symbol');
      if (card.related && card.related.length) {
        h += `<h4>Related notation</h4><div class="chips">` + card.related.map((r) => `<span class="chip" data-key="${esc(r.key)}" data-tex="${esc(r.tex)}" title="${r.count} occurrences"><span class="pa-math">\\(${esc(r.tex)}\\)</span></span>`).join('') + `</div>`;
      }
      if (llm) h += llmButtons(card, 'symbol');
    } else if (card.kind === 'operator') {
      if (card.status === 'dictionary') h += `<div class="entry"><div class="meaning">${card.meaning_html}</div><div class="srcline">${srcBadge('Standard notation', 'dictionary')}</div></div>`;
      else h += `<div class="status-line"><span class="status-none">Not in the notation dictionary</span> (character ${esc(card.char)})</div>`;
    } else if (card.kind === 'term') {
      if (card.entries.length) {
        for (const e of card.entries) {
          h += `<div class="entry">`;
          if (e.source === 'paper_definition') h += `<div class="defblock" data-block="${esc(e.location ? e.location.block : '')}">${e.definition_html}</div>`;
          else h += `<div class="meaning">${e.definition_html}</div>`;
          h += `<div class="srcline">${srcBadge(e.source_label, e.source_class)} ${e.location ? locLink(e.location) : ''} ${confNote(e.confidence)}</div>`;
          if (e.cite_hints && e.cite_hints.length) h += `<div class="small">Cites nearby: ${e.cite_hints.map((c) => `<span class="chip" data-cite="${esc(c.key)}">${esc(c.short)}${c.arxiv ? ' · arXiv:' + esc(c.arxiv) : ''}</span>`).join(' ')}</div>`;
          h += `</div>`;
        }
      } else {
        h += `<div class="status-line"><span class="status-none">Not defined in this paper's text</span></div>`;
        if (card.cite_hints && card.cite_hints.length) h += `<div class="small">Nearby citations: ${card.cite_hints.map((c) => esc(c.short)).join(', ')}</div>`;
      }
      if (card.depends_on && card.depends_on.length) h += `<h4>Uses these notions</h4><div class="chips">` + card.depends_on.map((d) => `<span class="chip" data-term="${esc(d.term)}">${esc(d.display)}</span>`).join('') + `</div>`;
      h += occHtml(card.mentions, 'Term');
      if (llm) h += llmButtons(card, 'term');
    } else if (card.kind === 'formula') {
      h += `<div class="defblock">${card.formula_html || ''}</div><h4>Symbols in this formula</h4><div class="formula-list">` +
        card.items.map((it) => `<div class="fl" data-uid="${esc(it.uid)}"><span class="ftex"><span class="pa-math">\\(${esc(it.tex)}\\)</span></span><span class="fmean">${it.meaning ? esc(it.meaning) : (it.status === 'not_found' ? '<span class="status-none">not defined here</span>' : '')}</span></div>`).join('') + `</div>`;
    } else if (card.kind === 'block') {
      h += card.status === 'found' ? `<div class="defblock" data-block="${esc(card.block)}">${card.html}</div><div class="srcline">${srcBadge('This paper', 'paper')} ${locLink(card.location)}</div>` : `<div class="status-none">Not found</div>`;
    } else if (card.kind === 'cite') {
      h += card.entries.map((e) => `<div class="entry bib"><div>${esc((e.authors || []).join(', '))}${e.year ? ' (' + esc(e.year) + ')' : ''}</div><div class="t">${esc(e.title || '')}</div><div class="small">${esc(e.venue || '')}${e.arxiv ? ` · <a href="https://arxiv.org/abs/${esc(e.arxiv)}" target="_blank" rel="noopener">arXiv:${esc(e.arxiv)}</a>` : ''}${e.doi ? ` · <a href="https://doi.org/${esc(e.doi)}" target="_blank" rel="noopener">doi</a>` : ''}</div></div>`).join('');
    }
    return h;
  }
  function entryHtml(e) {
    let q = e.quote_html ? `<details class="quote"><summary>where the paper says so</summary><div class="q">${e.quote_html}</div></details>` : '';
    return `<div class="entry"><div class="meaning">${e.meaning_html}</div><div class="srcline">${srcBadge(e.source_label, e.source_class)} ${e.location ? locLink(e.location) : ''} ${confNote(e.confidence)}${e.scope && e.scope.kind === 'blocks' ? ' <span class="conf">· local to that statement</span>' : ''}</div>${q}</div>`;
  }
  function llmButtons(card, kind) {
    const notFound = card.status === 'not_found';
    let h = '<div class="chips">';
    if (notFound) h += `<button class="btn btn-small" data-llm="explain" data-kind="${kind}">Ask the LLM what this paper says about it</button>`;
    else h += `<button class="btn btn-small" data-llm="paraphrase" data-kind="${kind}">Explain in plain English (LLM)</button>`;
    return h + '</div><div class="llm-out"></div>';
  }
  function cardHtml(card, opts) {
    const kindLabel = { symbol: 'symbol', operator: 'operator', term: 'term', formula: 'formula', block: 'statement', cite: 'citation' }[card.kind] || card.kind;
    const pinned = isPinned(card);
    return `<div class="card${opts.preview ? ' preview' : ''}" data-block="${esc(card.block || '')}">
      <div class="card-head"><span class="card-title">${cardTitleHtml(card)}</span><span class="kind">${opts.preview ? 'hovering · ' : ''}${kindLabel}</span>
        <span class="card-actions">${opts.preview ? '<button data-act="keep" title="keep this card">keep</button>' : `<button data-act="pin" class="${pinned ? 'pinned' : ''}" title="pin to the Pinned tab">${pinned ? '★ pinned' : '☆ pin'}</button>`}${!opts.preview && state.stack.length > 1 ? '<button data-act="back" title="back">← back</button>' : ''}${!opts.preview ? '<button data-act="close" title="close">×</button>' : ''}</span></div>
      ${cardBodyHtml(card)}</div>`;
  }
  async function renderStack() {
    const host = $('#card-host'); const crumbs = $('#crumbs');
    const top = state.stack[state.stack.length - 1];
    let h = '';
    if (state.preview) h += cardHtml(state.preview, { preview: true });
    if (top) h += cardHtml(top, { preview: false });
    if (!h) h = host.innerHTML.includes('card-empty') ? host.innerHTML : '';
    host.innerHTML = h;
    crumbs.innerHTML = state.stack.map((c, i) => `<button data-crumb="${i}" class="${i === state.stack.length - 1 ? 'current' : ''}">${esc(cardTitleText(c)).slice(0, 28)}</button>`).join('');
    for (const c of [state.preview, top]) if (c && c.units) Object.assign(state.cardUnits, mapUnits(c.units));
    await typeset([host]);
  }
  function mapUnits(u) { const out = {}; for (const [k, v] of Object.entries(u)) out[k] = [v[0], v[1], v[2], v[3] || v[0]]; return out; }

  // ------------------------------------------------------------------ pins
  const pinKey = () => 'papassist-pins-' + state.pid;
  function isPinned(card) { return state.pins.some((p) => p.sig === cardSig(card)); }
  function cardSig(card) { return card.kind + ':' + (card.key || card.term || card.mid || card.char || card.block || ''); }
  function loadPins() { try { state.pins = JSON.parse(localStorage.getItem(pinKey()) || '[]'); } catch (e) { state.pins = []; } updatePinCount(); }
  function savePins() { try { localStorage.setItem(pinKey(), JSON.stringify(state.pins)); } catch (e) { /* ignore */ } updatePinCount(); }
  function updatePinCount() { $('#pin-count').textContent = state.pins.length; }
  function togglePin(card) {
    const sig = cardSig(card);
    const i = state.pins.findIndex((p) => p.sig === sig);
    if (i >= 0) state.pins.splice(i, 1); else state.pins.push({ sig, card, title: cardTitleText(card), added: Date.now() });
    savePins(); renderStack(); renderPins();
  }
  async function renderPins() {
    const host = $('#pins-host');
    host.innerHTML = state.pins.length ? state.pins.map((p, i) => `<div class="card" data-block="${esc(p.card.block || '')}"><div class="card-head"><span class="card-title">${cardTitleHtml(p.card)}</span><span class="card-actions"><button data-unpin="${i}">remove</button></span></div>${cardBodyHtml(p.card)}</div>`).join('') : '<p class="small">Nothing pinned yet. Use ☆ pin on a card.</p>';
    for (const p of state.pins) if (p.card.units) Object.assign(state.cardUnits, mapUnits(p.card.units));
    await typeset([host]);
  }
  function exportPins() {
    const lines = [`# Cheat sheet: ${state.paper ? state.paper.title : ''}`, ''];
    for (const p of state.pins) {
      const c = p.card;
      lines.push(`## ${cardTitleText(c)}`);
      if (c.kind === 'symbol') {
        for (const e of c.entries.filter((x) => x.exact)) lines.push(`- ${e.meaning_text}  _(${e.source_label}${e.location ? ', ' + e.location.heading : ''})_`);
        if (c.dictionary) lines.push(`- ${c.dictionary.meaning_text}  _(Standard notation)_`);
        if (!c.entries.length && !c.dictionary) lines.push('- Not defined in this paper.');
      } else if (c.kind === 'term') {
        for (const e of c.entries) lines.push(`- ${e.definition_text}  _(${e.source_label}${e.location ? ', ' + e.location.heading : ''})_`);
      } else if (c.kind === 'operator') lines.push(`- ${c.meaning_text || ''}`);
      lines.push('');
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'papassist-cheatsheet.md'; a.click();
  }

  // ------------------------------------------------------------------ glossary tab
  async function renderGlossary() {
    if (!state.pid) return;
    if (!state.glossary) state.glossary = await api(`/api/papers/${state.pid}/glossary`);
    const q = ($('#glossary-filter').value || '').toLowerCase();
    const g = state.glossary;
    const syms = g.symbols.filter((s) => s.source !== 'paper_macro' && (!q || (s.tex + ' ' + s.meaning).toLowerCase().includes(q)));
    const terms = g.terms.filter((t) => !q || (t.term + ' ' + (t.display || '')).toLowerCase().includes(q));
    const seen = new Set();
    let h = `<h4>Terms (${terms.length})</h4>` + terms.map((t) => `<div class="gl-item" data-term="${esc(t.term)}"><span class="gk">${esc(t.display || t.term)}</span><span class="gm small">${esc((t.definition_text || '').slice(0, 90))}</span></div>`).join('');
    h += `<h4>Symbols (${syms.length})</h4>` + syms.filter((s) => { const k = s.key + '|' + s.meaning; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 400).map((s) => `<div class="gl-item" data-key="${esc(s.key)}" data-tex="${esc(s.tex)}"><span class="gk"><span class="pa-math">\\(${esc(s.tex)}\\)</span></span><span class="gm">${esc(s.meaning.slice(0, 110))}</span></div>`).join('');
    $('#glossary-host').innerHTML = h;
    await typeset([$('#glossary-host')]);
  }

  // ------------------------------------------------------------------ LLM
  function renderLlmPill(llm, settings) {
    const pill = $('#llm-pill'); const s = settings || state.settings || {};
    pill.className = 'pill';
    if (!s.api_key_present) { pill.classList.add('pill-off'); pill.textContent = 'LLM: off (no API key)'; pill.title = 'Put ANTHROPIC_API_KEY in a .env file next to papassist.bat to enable LLM features.'; return; }
    if (!s.llm_enabled) { pill.classList.add('pill-off'); pill.textContent = 'LLM: disabled'; return; }
    const st = llm && llm.status;
    if (st === 'running') { pill.classList.add('pill-run'); pill.textContent = 'LLM: glossary pass running…'; }
    else if (st === 'done') { pill.classList.add('pill-on'); pill.textContent = `LLM: on · glossary enriched (${s.model})`; }
    else if (st === 'error') { pill.classList.add('pill-off'); pill.textContent = 'LLM: glossary pass failed'; pill.title = llm.error || ''; }
    else { pill.classList.add('pill-on'); pill.textContent = `LLM: on (${s.model})`; }
  }
  function pollStatus() {
    clearTimeout(state.statusTimer);
    if (!state.pid || !state.settings.llm_active) return;
    const tick = async () => {
      try {
        const st = await api(`/api/papers/${state.pid}/status`);
        renderLlmPill(st.llm, st.settings);
        if (st.llm && st.llm.status === 'running') state.statusTimer = setTimeout(tick, 4000);
        else if (st.job && st.job.status === 'done' && !state._enrichToastShown) { state._enrichToastShown = true; toast('LLM glossary pass finished. Reload the paper to see new terms.', 6000); }
      } catch (e) { /* ignore */ }
    };
    tick();
  }
  async function runLlm(btn) {
    const cardEl = btn.closest('.card'); const out = $('.llm-out', cardEl);
    const card = findCardForEl(cardEl); if (!card) return;
    btn.disabled = true; out.innerHTML = '<div class="small">Asking the model… (this reads the paper, not the internet)</div>';
    try {
      let res;
      if (btn.dataset.llm === 'explain') {
        res = await api(`/api/papers/${state.pid}/explain`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: card.kind, key: card.key || card.term, tex: card.tex, block: card.block }) });
        if (res.found) out.innerHTML = `<div class="llm-box">${srcBadge("LLM, from this paper's text", 'llm')}<div class="meaning">${res.meaning_html}</div>${res.evidence_html ? `<details class="quote"><summary>evidence in the paper</summary><div class="q">${res.evidence_html}</div></details>` : ''}${res.evidence_blocks && res.evidence_blocks.length ? `<div class="small">See ${res.evidence_blocks.map((b) => `<a data-jump="${esc(b)}">${esc(b)}</a>`).join(', ')}</div>` : ''}</div>`;
        else out.innerHTML = `<div class="llm-box">${srcBadge('LLM', 'none')} <span class="status-none">The paper's text does not determine this.</span> <span class="small">${esc(res.note || '')}</span></div>`;
      } else {
        const text = card.kind === 'term' ? (card.entries[0] ? card.entries[0].definition_text : '') : (card.entries.filter((e) => e.exact).map((e) => e.meaning_text).join('; ') || (card.dictionary ? card.dictionary.meaning_text : ''));
        res = await api(`/api/papers/${state.pid}/paraphrase`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, context: cardTitleText(card) }) });
        out.innerHTML = `<div class="llm-box">${srcBadge('LLM paraphrase of the source above', 'llm')}<div class="meaning">${res.html}</div></div>`;
      }
      await typeset([out]);
    } catch (e) { out.innerHTML = `<div class="small status-none">${esc(e.message)}</div>`; }
    finally { btn.disabled = false; }
  }
  function findCardForEl(cardEl) {
    if (!cardEl) return null;
    if (cardEl.classList.contains('preview')) return state.preview;
    if (cardEl.closest('#pins-host')) { const i = $$('#pins-host .card').indexOf(cardEl); return state.pins[i] ? state.pins[i].card : null; }
    return state.stack[state.stack.length - 1];
  }

  // ------------------------------------------------------------------ panel events
  $('#panel').addEventListener('click', async (e) => {
    const act = e.target.closest('[data-act]');
    if (act) {
      const a = act.dataset.act;
      if (a === 'keep' && state.preview) { const c = state.preview; state.preview = null; state.stack.push(c); renderStack(); }
      else if (a === 'back') { state.stack.pop(); renderStack(); }
      else if (a === 'close') { state.stack = []; renderStack(); }
      else if (a === 'pin') { togglePin(state.stack[state.stack.length - 1]); }
      return;
    }
    const crumb = e.target.closest('[data-crumb]'); if (crumb) { state.stack = state.stack.slice(0, Number(crumb.dataset.crumb) + 1); renderStack(); return; }
    const unpin = e.target.closest('[data-unpin]'); if (unpin) { state.pins.splice(Number(unpin.dataset.unpin), 1); savePins(); renderPins(); renderStack(); return; }
    const jump = e.target.closest('[data-jump]'); if (jump) { e.preventDefault(); jumpTo(jump.dataset.jump); return; }
    const llmBtn = e.target.closest('[data-llm]'); if (llmBtn) { runLlm(llmBtn); return; }
    const fl = e.target.closest('.fl[data-uid]'); if (fl) { pushTarget({ type: 'unit', uid: fl.dataset.uid, key: (state.units[fl.dataset.uid] || [])[0], block: (fl.closest('.card') || {}).dataset ? fl.closest('.card').dataset.block : null }); return; }
    const chipTerm = e.target.closest('.chip[data-term], .gl-item[data-term]'); if (chipTerm) { pushTarget({ type: 'term', term: chipTerm.dataset.term, block: null }); return; }
    const chipKey = e.target.closest('.chip[data-key], .gl-item[data-key]'); if (chipKey) { pushTarget({ type: 'unit', inCard: true, key: chipKey.dataset.key, tex: chipKey.dataset.tex, block: null }); return; }
    const chipCite = e.target.closest('.chip[data-cite]'); if (chipCite) { pushTarget({ type: 'cite', keys: chipCite.dataset.cite, block: null }); return; }
    const t = findTarget(e.target);
    if (t && (t.type === 'unit' || t.type === 'term' || t.type === 'cite' || t.type === 'ref')) { e.preventDefault(); pushTarget(t); }
  });
  $('#panel').addEventListener('mouseover', (e) => {
    // hovering inside a card highlights the same symbol in the paper, but does not replace the card
    const t = findTarget(e.target);
    if (t && (t.type === 'unit' || t.type === 'term')) highlightTarget(t);
  });
  $$('.panel-tabs .tab').forEach((tab) => tab.addEventListener('click', () => {
    $$('.panel-tabs .tab').forEach((x) => x.classList.toggle('active', x === tab));
    $$('.tab-body').forEach((b) => (b.hidden = b.id !== 'tab-' + tab.dataset.tab));
    if (tab.dataset.tab === 'pins') renderPins();
    if (tab.dataset.tab === 'glossary') renderGlossary();
  }));
  $('#glossary-filter').addEventListener('input', () => renderGlossary());
  $('#btn-export').addEventListener('click', exportPins);
  $('#btn-clear-pins').addEventListener('click', () => { state.pins = []; savePins(); renderPins(); renderStack(); });

  // ------------------------------------------------------------------ reader events
  const reader = $('#reader');
  reader.addEventListener('mouseover', onMouseOver);
  reader.addEventListener('click', onClick);
  $('#toc').addEventListener('click', (e) => { const a = e.target.closest('[data-jump]'); if (a) { e.preventDefault(); jumpTo(a.dataset.jump); } });

  // ------------------------------------------------------------------ opening papers
  async function openFiles(files) {
    const fd = new FormData();
    for (const f of files) fd.append('files', f, f.name);
    $('#dz-status').textContent = `Converting ${files.length} file(s)…`;
    try { const res = await api('/api/papers/upload', { method: 'POST', body: fd }); await loadPaper(res.paper_id); }
    catch (e) { $('#dz-status').textContent = 'Could not open: ' + e.message; toast(e.message, 6000); }
  }
  $('#btn-open').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', (e) => { if (e.target.files.length) openFiles(Array.from(e.target.files)); e.target.value = ''; });
  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('drag'); }));
  dz.addEventListener('drop', (e) => { if (e.dataTransfer.files.length) openFiles(Array.from(e.dataTransfer.files)); });
  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files.length && !dz.hidden) openFiles(Array.from(e.dataTransfer.files)); });
  $('#path-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const path = $('#path-input').value.trim(); if (!path) return;
    $('#dz-status').textContent = 'Converting…';
    try { const res = await api('/api/papers/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) }); await loadPaper(res.paper_id); }
    catch (err) { $('#dz-status').textContent = 'Could not open: ' + err.message; }
  });
  async function showLibrary() {
    $('#reader').hidden = true; $('#dropzone').hidden = false;
    const list = $('#library-list');
    try {
      const res = await api('/api/library');
      list.innerHTML = res.papers.length ? '<h3>Your library</h3>' + res.papers.map((p) => `<div class="lib-item"><a href="#paper=${esc(p.id)}" data-pid="${esc(p.id)}"><strong>${esc(p.title || p.id)}</strong><div class="lib-meta">${esc((p.authors || []).join(', '))} · ${p.blocks || '?'} blocks · ${p.formulas || '?'} formulas · ${p.terms || 0} terms</div></a><button class="btn btn-small" data-del="${esc(p.id)}" title="remove from library">remove</button></div>`).join('') : '';
    } catch (e) { list.innerHTML = ''; }
  }
  $('#library-list').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-del]'); if (del) { e.preventDefault(); await api(`/api/papers/${del.dataset.del}`, { method: 'DELETE' }); showLibrary(); return; }
    const a = e.target.closest('[data-pid]'); if (a) { e.preventDefault(); loadPaper(a.dataset.pid); }
  });
  $('#btn-library').addEventListener('click', showLibrary);

  // ------------------------------------------------------------------ boot
  (async () => {
    try { const h = await api('/api/health'); state.settings = h.settings; renderLlmPill(null, h.settings); } catch (e) { /* ignore */ }
    const m = /paper=([^&]+)/.exec(location.hash);
    await showLibrary();
    if (m) loadPaper(m[1]);
  })();
})();
