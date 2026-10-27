/* Shared helpers: API calls, formatting, toasts, food emoji, celebrations. */
(function () {
  const NT = (window.NT = window.NT || {});

  NT.api = async function (url, { method = 'GET', body, form } = {}) {
    const headers = { 'X-Requested-With': 'fetch' };
    let payload;
    if (form) payload = form;
    else if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    const res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin' });
    if (res.status === 401) {
      location.href = '/login';
      throw new Error('Please sign in again.');
    }
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) throw new Error((data && (data.detail || data.error)) || `Request failed (${res.status})`);
    return data;
  };

  NT.esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  NT.fmt = function (v, digits) {
    const n = Number(v) || 0;
    if (digits === undefined) digits = Math.abs(n) >= 100 ? 0 : Math.abs(n) >= 10 ? 1 : 2;
    return n.toLocaleString('en-IN', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  };

  NT.isoDate = (d) => {
    const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
    return z.toISOString().slice(0, 10);
  };

  let toastTimer;
  NT.toast = function (msg, ms = 2600) {
    let el = document.querySelector('.toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'toast';
      el.setAttribute('role', 'status');
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.animation = 'none';
    void el.offsetWidth; // restart animation
    el.style.animation = '';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.remove(), ms);
  };

  const CATEGORY_EMOJI = {
    'Breads': '🫓', 'Rice & Grains': '🍚', 'Breakfast': '🥞', 'Dals & Legumes': '🍲',
    'Vegetable Curries': '🥘', 'Meat & Poultry': '🍗', 'Fish & Seafood': '🐟', 'Eggs': '🥚',
    'Snacks': '🥟', 'Sweets': '🍮', 'Beverages': '🥤', 'Dairy': '🥛', 'Fats & Oils': '🧈',
    'Ingredients': '🌾', 'Vegetables': '🥦', 'Fruits': '🥭', 'Nuts & Seeds': '🥜',
    'Condiments': '🫙', 'Fast Food': '🍕', 'Salads': '🥗', 'Supplements': '💪',
    'Packaged': '📦', 'Branded': '🏷️', 'Generic (USDA)': '🧺', 'Recipes': '👩‍🍳', 'My foods': '⭐',
  };
  const NAME_EMOJI = [
    [/^water$/i, '💧'], [/\b(chai|tea)\b/i, '🍵'], [/coffee|kaapi/i, '☕'], [/lassi|chaas|buttermilk|\bmilk\b/i, '🥛'],
    [/curd|dahi|raita|yogurt|shrikhand/i, '🥣'], [/biryani|pulao|\brice\b|khichdi|poha|upma|pongal/i, '🍚'], [/roti|chapati|paratha|naan|kulcha|puri|thepla/i, '🫓'],
    [/dosa|uttapam|idli/i, '🥞'], [/dal|sambar|rasam|rajma|chole/i, '🍲'], [/paneer/i, '🧀'],
    [/chicken/i, '🍗'], [/mutton|keema|lamb|beef|pork/i, '🍖'], [/fish|prawn|crab/i, '🐟'], [/egg|omelette/i, '🥚'],
    [/samosa|pakora|vada|bhaji|kachori/i, '🥟'], [/banana/i, '🍌'], [/mango/i, '🥭'], [/apple/i, '🍎'],
    [/orange|mosambi/i, '🍊'], [/grape/i, '🍇'], [/watermelon/i, '🍉'], [/salad/i, '🥗'],
    [/pizza/i, '🍕'], [/burger/i, '🍔'], [/fries|chips/i, '🍟'], [/cake|pastry/i, '🍰'], [/ice cream|kulfi/i, '🍨'],
    [/ladoo|barfi|jamun|halwa|kheer|sweet|jalebi/i, '🍮'], [/nut|badam|almond|cashew|peanut/i, '🥜'],
  ];
  NT.emoji = function (food) {
    const name = food.food_name || food.name || '';
    for (const [re, e] of NAME_EMOJI) if (re.test(name)) return e;
    return CATEGORY_EMOJI[food.category] || '🍽️';
  };

  NT.confetti = function (symbols = ['🥕', '🍎', '🥦', '🍋', '🥭', '🎉', '✨']) {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    for (let i = 0; i < 28; i++) {
      const el = document.createElement('span');
      el.className = 'confetti';
      el.textContent = symbols[i % symbols.length];
      el.style.left = Math.random() * 100 + 'vw';
      el.style.animationDuration = 1.8 + Math.random() * 1.6 + 's';
      el.style.animationDelay = Math.random() * 0.4 + 's';
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 4000);
    }
  };

  NT.splash = function (x, y, symbol = '💧') {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const el = document.createElement('span');
    el.className = 'splash';
    el.textContent = symbol;
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 900);
  };

  /** Animate a number from its current text value to `to`. */
  NT.countUp = function (el, to, digits = 0, ms = 900) {
    if (!el) return;
    const from = Number(el.dataset.value || 0);
    el.dataset.value = to;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) { el.textContent = NT.fmt(to, digits); return; }
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = NT.fmt(from + (to - from) * eased, digits);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
})();
