/* Diary page: renders the day from JSON and keeps it in sync after every change. */
(function () {
  const NT = window.NT;
  const D = window.DIARY;
  const MEALS = [
    ['breakfast', '🌅', 'var(--meal-breakfast)', 'Poha, idli, paratha, chai…'],
    ['lunch', '🍛', 'var(--meal-lunch)', 'Dal, roti, rice, sabzi…'],
    ['dinner', '🌙', 'var(--meal-dinner)', 'Khichdi, curry, roti…'],
    ['snacks', '🍪', 'var(--meal-snacks)', 'Fruit, chana, chai, namkeen…'],
  ];
  const MACROS = [['protein', 'Protein', '#8fc1ff', 'var(--series-1)'], ['carbs', 'Carbs', '#ffb18e', 'var(--series-2)'], ['fat', 'Fat', '#7fe0bb', 'var(--series-3)']];
  const GAUGES = [['protein', 'Protein'], ['fiber', 'Fibre'], ['iron', 'Iron'], ['calcium', 'Calcium'], ['vit_b12', 'B12'], ['vit_c', 'Vit C']];
  let summary = D.summary;
  let activeCat = 'Vitamins';
  let freshIds = new Set();
  let editing = null;

  const targetsBy = () => Object.fromEntries(summary.targets.map((t) => [t.key, t]));
  const setDash = (el, circumference, frac) => { el.style.strokeDashoffset = String(circumference * (1 - Math.max(0, Math.min(1, frac)))); };

  // ---------- header ----------
  const isToday = D.day === NT.isoDate(new Date());
  const hour = new Date().getHours();
  const hello = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  document.getElementById('greeting').textContent = isToday ? `${hello}${D.name ? ', ' + D.name : ''} 👋` : 'Looking back';
  document.getElementById('day-picker').addEventListener('change', (e) => { if (e.target.value) location.href = `/diary?d=${e.target.value}`; });

  // ---------- hero ----------
  function renderHero() {
    const t = targetsBy();
    const eaten = summary.totals.energy || 0;
    const goal = t.energy?.target || 0;
    const pct = goal ? eaten / goal : 0;
    const ring = document.querySelector('#energy-ring .value');
    setDash(ring, 402.1, pct);
    ring.classList.toggle('over', pct > 1.1);
    NT.countUp(document.getElementById('kcal-eaten'), eaten);
    const left = Math.round(goal - eaten);
    document.getElementById('kcal-left').textContent = goal ? (left >= 0 ? `${NT.fmt(left, 0)} kcal left` : `${NT.fmt(-left, 0)} kcal over`) : 'kcal';
    const circ = { protein: 339.3, carbs: 301.6, fat: 263.9 };
    MACROS.forEach(([k]) => setDash(document.querySelector(`#energy-ring [data-m="${k}"]`), circ[k], t[k]?.target ? summary.totals[k] / t[k].target : 0));

    document.getElementById('macro-stats').innerHTML = MACROS.map(([k, label, light]) => {
      const v = summary.totals[k] || 0, g = t[k]?.target || 0;
      return `<div class="macro-stat"><div class="k"><span class="swatch" style="background:${light}"></span>${label}</div>
        <div class="v">${NT.fmt(v, 0)}<small> / ${NT.fmt(g, 0)} g</small></div>
        <div class="meter"><span style="width:${g ? Math.min(100, v / g * 100) : 0}%;background:${light}"></span></div></div>`;
    }).join('');

    const chips = [];
    if (summary.streak > 0) chips.push(`<span class="glass-chip">🔥 ${summary.streak}-day streak</span>`);
    chips.push(`<span class="glass-chip">🌿 Score ${summary.score.value}</span>`);
    chips.push(`<span class="glass-chip">💧 ${NT.fmt((summary.totals.water || 0) / 1000, 1)} L water</span>`);
    chips.push(`<span class="glass-chip">🥦 ${NT.fmt(summary.totals.fiber, 0)} g fibre</span>`);
    document.getElementById('hero-chips').innerHTML = chips.join('');
  }

  // ---------- week strip ----------
  function renderWeek() {
    document.getElementById('week').innerHTML = summary.week.map((d) => {
      const frac = Math.min(1, d.pct / 100);
      return `<a class="day ${d.selected ? 'selected' : ''} ${d.future ? 'future' : ''}" href="/diary?d=${d.date}" ${d.selected ? 'aria-current="date"' : ''} aria-label="${d.dow} ${d.dom}: ${d.energy} kcal">
        <span class="dow">${d.dow}</span>
        <span class="mini"><svg viewBox="0 0 40 40"><circle class="t" cx="20" cy="20" r="16"/>
          <circle class="v ${d.pct > 110 ? 'over' : ''}" cx="20" cy="20" r="16" stroke-dasharray="100.5" stroke-dashoffset="${100.5 * (1 - frac)}"/></svg><b>${d.dom}</b></span>
        <span class="kc">${d.energy ? NT.fmt(d.energy, 0) : '–'}</span></a>`;
    }).join('');
  }

  // ---------- meals ----------
  function entryRow(e) {
    const n = e.nutrients || {};
    const qty = e.unit === 'g' || e.unit === 'ml' ? `${NT.fmt(e.grams, 0)} ${e.unit}` : `${NT.fmt(e.quantity)} × ${NT.esc(e.unit)}`;
    const src = e.source === 'photo' ? ' <span class="badge" title="Logged from a photo">📸</span>' : e.source === 'text_ai' ? ' <span class="badge" title="AI estimate">✨</span>' : '';
    return `<li class="entry ${freshIds.has(e.id) ? 'pop' : ''}" data-id="${e.id}">
      <span class="food-tile" style="--tile:${NT.tile(e)}" aria-hidden="true">${NT.emoji(e)}</span>
      <span class="title">${NT.esc(e.food_name)}${src}</span>
      <span class="meta">${qty} · P ${NT.fmt(n.protein, 0)} · C ${NT.fmt(n.carbs, 0)} · F ${NT.fmt(n.fat, 0)}</span>
      <span class="kcal">${NT.fmt(n.energy, 0)}<small>kcal</small></span>
      <span class="actions"><button class="icon-btn" data-edit="${e.id}" aria-label="Edit ${NT.esc(e.food_name)}">✏️</button>
      <button class="icon-btn" data-del="${e.id}" aria-label="Delete ${NT.esc(e.food_name)}">🗑️</button></span></li>`;
  }

  function renderMeals() {
    document.getElementById('meals').innerHTML = MEALS.map(([m, emo, grad, hint]) => {
      const meal = summary.meals[m];
      const tot = meal.totals;
      const kcalP = tot.protein * 4, kcalC = tot.carbs * 4, kcalF = tot.fat * 9, sum = kcalP + kcalC + kcalF || 1;
      const items = meal.entries.length;
      return `<section class="card meal-card reveal" style="--meal:${grad}" aria-label="${m}">
        <div class="meal-head">
          <span class="meal-icon" aria-hidden="true">${emo}</span>
          <div class="title"><h3>${m}</h3><div class="sub">${items ? `${items} item${items > 1 ? 's' : ''} · P ${NT.fmt(tot.protein, 0)} C ${NT.fmt(tot.carbs, 0)} F ${NT.fmt(tot.fat, 0)}` : 'Nothing yet'}</div></div>
          <div class="kcal"><strong>${NT.fmt(tot.energy, 0)}</strong><span>kcal</span></div>
          <button class="add-pill" data-add-meal="${m}" aria-label="Add food to ${m}">＋</button>
        </div>
        ${items ? `<div class="macro-line" aria-hidden="true"><span style="width:${kcalP / sum * 100}%;background:var(--series-1)"></span><span style="width:${kcalC / sum * 100}%;background:var(--series-2)"></span><span style="width:${kcalF / sum * 100}%;background:var(--series-3)"></span></div>` : ''}
        ${items ? `<ul class="entries">${meal.entries.map(entryRow).join('')}</ul>`
          : `<button class="empty-meal" data-add-meal="${m}"><span class="e">${emo}</span><span><strong>Add ${m}</strong><br><span class="tiny">${hint}</span></span></button>`}
      </section>`;
    }).join('');
  }

  // ---------- water ----------
  function renderWater() {
    const t = targetsBy();
    const total = summary.totals.water || 0;
    const goal = t.water?.target || 2500;
    const plain = summary.plain_water_ml || 0;
    const pct = Math.min(1, total / goal);
    document.getElementById('water-fill').style.height = `${Math.max(4, pct * 100)}%`;
    NT.countUp(document.getElementById('water-total'), total);
    document.getElementById('water-goal').textContent = NT.fmt(goal, 0);
    document.getElementById('water-break').textContent = `${NT.fmt(plain, 0)} ml plain water · ${NT.fmt(Math.max(0, total - plain), 0)} ml from drinks & food`;
    document.getElementById('water-chip').textContent = pct >= 1 ? '🎉 Goal reached' : `${Math.round(pct * 100)}%`;
    document.getElementById('water-list').innerHTML = (summary.water_entries || []).map((e) =>
      `<li class="${freshIds.has(e.id) ? 'pop' : ''}" title="${new Date(e.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}">💧 ${NT.fmt(e.grams, 0)} ml
       <button data-del="${e.id}" aria-label="Remove ${NT.fmt(e.grams, 0)} ml water">✕</button></li>`).join('');
  }

  // ---------- score + gauges ----------
  function renderScore() {
    const s = summary.score;
    setDash(document.querySelector('#score-ring .v'), 235.6, s.value / 100);
    NT.countUp(document.getElementById('score-value'), s.value);
    document.getElementById('score-ring').setAttribute('aria-label', `Nutrition score ${s.value} of 100`);
    document.getElementById('score-label').textContent = { Excellent: '🌟 Excellent', Good: '👍 Good', Fair: '🙂 Fair', 'Needs work': '🌱 Needs work' }[s.label] || s.label;
    const t = targetsBy();
    document.getElementById('gauges').innerHTML = GAUGES.map(([k, label]) => {
      const pct = t[k]?.pct || 0;
      const color = pct >= 90 ? 'var(--status-good)' : pct >= 60 ? 'var(--status-warn)' : 'var(--series-2)';
      return `<div class="gauge" title="${NT.fmt(t[k]?.value)} / ${NT.fmt(t[k]?.target)} ${t[k]?.unit}">
        <div class="g"><svg viewBox="0 0 54 54"><circle class="t" cx="27" cy="27" r="22"/>
        <circle class="v" cx="27" cy="27" r="22" stroke="${color}" stroke-dasharray="138.2" stroke-dashoffset="${138.2 * (1 - Math.min(1, pct / 100))}"/></svg>
        <b>${Math.round(pct)}%</b></div><span class="n">${label}</span></div>`;
    }).join('');
  }

  // ---------- tips ----------
  function renderTips() {
    document.getElementById('tips').innerHTML = summary.tips.map((tip) => `
      <div class="tip ${tip.kind}"><span class="ti" aria-hidden="true">${tip.icon}</span>
        <strong>${NT.esc(tip.title)}</strong><p>${NT.esc(tip.body)}</p>
        ${tip.foods.length ? `<div class="suggest">${tip.foods.map((f) => `<button data-suggest="${NT.esc(f.name)}" title="Add ${NT.esc(f.name)}">${NT.emoji(f)} ${NT.esc(f.name.replace(/ \(.*\)$/, ''))} <small>${NT.fmt(f.amount)} ${NT.esc(tip.unit || '')}</small></button>`).join('')}</div>` : ''}
      </div>`).join('');
  }

  // ---------- all nutrients ----------
  function renderTargets() {
    const cats = [...new Set(summary.targets.map((t) => t.category))];
    document.getElementById('target-tabs').innerHTML = cats.map((c) =>
      `<button class="tab ${c === activeCat ? 'active' : ''}" role="tab" aria-selected="${c === activeCat}" data-cat="${c}">${c}</button>`).join('');
    const label = { ok: '✓ On track', near: '◐ Almost there', low: '○ Low', over: '▲ Over limit', none: '' };
    document.getElementById('targets').innerHTML = summary.targets.filter((t) => t.category === activeCat).map((t) => {
      const w = t.pct === null ? 0 : Math.min(100, t.pct);
      const statusText = t.kind === 'max' && t.status === 'ok' ? '✓ Under limit' : label[t.status];
      return `<div class="target"><span class="label">${NT.esc(t.label)}${t.kind === 'max' ? ' <span class="badge">limit</span>' : ''}</span>
        <span class="nums">${NT.fmt(t.value)} / ${NT.fmt(t.target)} ${NT.esc(t.unit)}</span>
        <div class="bar"><span class="${t.status}" style="width:${w}%"></span></div>
        <span class="state">${statusText}${t.pct !== null ? ` · ${Math.round(t.pct)}%` : ''}</span></div>`;
    }).join('');
  }
  document.getElementById('target-tabs').addEventListener('click', (e) => {
    const c = e.target.closest('[data-cat]');
    if (c) { activeCat = c.dataset.cat; renderTargets(); }
  });

  function renderAll() {
    renderHero(); renderWeek(); renderMeals(); renderWater(); renderScore(); renderTips(); renderTargets();
    freshIds = new Set();
  }

  async function refresh(newEntries = []) {
    const before = targetsBy();
    newEntries.forEach((e) => freshIds.add(e.id));
    summary = await NT.api(`/api/diary?day=${D.day}`);
    renderAll();
    celebrate(before);
  }

  function celebrate(before) {
    const after = targetsBy();
    const reached = (k, at) => after[k] && after[k].pct >= at && !(before[k] && before[k].pct >= at);
    if (reached('water', 100)) { NT.confetti(['💧', '💦', '🎉', '✨']); NT.toast('Hydration goal reached! 💧'); }
    else if (reached('protein', 100)) { NT.confetti(['💪', '🥚', '🫘', '🎉']); NT.toast('Protein goal reached! 💪'); }
    else if (reached('energy', 90)) { NT.confetti(); NT.toast('Energy target reached for today 🎯'); }
  }

  document.addEventListener('nt:logged', (e) => refresh(e.detail).catch((err) => NT.toast(err.message)));

  // ---------- actions ----------
  document.addEventListener('click', async (e) => {
    const add = e.target.closest('[data-add-meal]');
    if (add) return NT.openAddFood(add.dataset.addMeal || null);

    const sug = e.target.closest('[data-suggest]');
    if (sug) return NT.openAddFood(null, 'search', sug.dataset.suggest);

    const del = e.target.closest('[data-del]');
    if (del) {
      const row = del.closest('.entry, li');
      row?.classList.add('leaving');
      try {
        await NT.api(`/api/logs/${del.dataset.del}`, { method: 'DELETE' });
        await refresh();
      } catch (err) { row?.classList.remove('leaving'); NT.toast(err.message); }
      return;
    }

    const edit = e.target.closest('[data-edit]');
    if (edit) {
      editing = Object.values(summary.meals).flatMap((m) => m.entries).find((x) => x.id === edit.dataset.edit);
      if (!editing) return;
      document.getElementById('edit-name').textContent = editing.food_name;
      const tile = document.getElementById('edit-tile');
      tile.textContent = NT.emoji(editing);
      tile.style.setProperty('--tile', NT.tile(editing));
      document.getElementById('edit-grams').value = Math.round(editing.grams);
      document.getElementById('edit-meal').value = editing.meal;
      document.getElementById('edit-photo').innerHTML = editing.photo_id
        ? `<img src="/api/photos/${editing.photo_id}" alt="Meal photo" class="photo-preview" style="margin-bottom:14px" loading="lazy">` : '';
      document.getElementById('edit-dialog').showModal();
      return;
    }

    const water = e.target.closest('[data-water]');
    if (water) {
      NT.splash(e.clientX, e.clientY);
      try {
        const entry = await NT.api('/api/water', { method: 'POST', body: { logged_on: D.day, ml: Number(water.dataset.water) } });
        await refresh([entry]);
      } catch (err) { NT.toast(err.message); }
    }
  });

  document.getElementById('water-custom').addEventListener('click', async (e) => {
    const ml = Number(prompt('How much water (ml)?', '300'));
    if (!(ml > 0)) return;
    NT.splash(e.clientX, e.clientY);
    try {
      const entry = await NT.api('/api/water', { method: 'POST', body: { logged_on: D.day, ml } });
      await refresh([entry]);
    } catch (err) { NT.toast(err.message); }
  });

  document.getElementById('edit-save').addEventListener('click', async () => {
    if (!editing) return;
    const grams = Number(document.getElementById('edit-grams').value);
    const meal = document.getElementById('edit-meal').value;
    const body = { meal };
    if (grams > 0 && grams !== Number(editing.grams)) {
      body.grams = grams;
      body.quantity = Math.round((Number(editing.quantity) * grams / Number(editing.grams)) * 100) / 100 || 1;
    }
    try {
      await NT.api(`/api/logs/${editing.id}`, { method: 'PATCH', body });
      document.getElementById('edit-dialog').close();
      await refresh([editing]);
    } catch (err) { NT.toast(err.message); }
  });

  document.getElementById('copy-btn').addEventListener('click', async () => {
    const d = new Date(D.day + 'T00:00:00');
    d.setDate(d.getDate() - 1);
    try {
      const res = await NT.api('/api/logs/copy', { method: 'POST', body: { from_day: NT.isoDate(d), to_day: D.day } });
      NT.toast(res.copied ? `Copied ${res.copied} items from yesterday` : 'Nothing logged yesterday');
      if (res.copied) await refresh();
    } catch (err) { NT.toast(err.message); }
  });

  renderAll();
  const wanted = new URLSearchParams(location.search).get('add');
  if (wanted) {
    history.replaceState(null, '', location.pathname + (location.search.includes('d=') ? `?d=${D.day}` : ''));
    NT.openAddFood(null, wanted);
  }
})();
