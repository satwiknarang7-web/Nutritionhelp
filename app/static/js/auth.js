/* Sign-in / sign-up niceties: show password, strength meter. */
document.querySelectorAll('[data-toggle-pw]').forEach((btn) => btn.addEventListener('click', () => {
  const input = btn.parentElement.querySelector('input');
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  btn.textContent = show ? '🙈' : '👁️';
  btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
}));
const meter = document.getElementById('pw-meter');
if (meter) {
  const hint = document.getElementById('pw-hint');
  document.getElementById('password').addEventListener('input', (e) => {
    const v = e.target.value;
    let s = 0;
    if (v.length >= 8) s++;
    if (v.length >= 12) s++;
    if (/[A-Z]/.test(v) && /[a-z]/.test(v)) s++;
    if (/\d/.test(v)) s++;
    if (/[^A-Za-z0-9]/.test(v)) s++;
    const levels = [['Too short', 'var(--status-crit)'], ['Weak', 'var(--status-crit)'], ['Okay', 'var(--status-warn)'], ['Good', 'var(--status-warn)'], ['Strong', 'var(--status-good)'], ['Excellent', 'var(--status-good)']];
    const [label, color] = levels[v.length < 8 ? 0 : s];
    meter.style.width = `${Math.max(8, (v.length < 8 ? 1 : s) / 5 * 100)}%`;
    meter.style.background = color;
    hint.textContent = v ? label : 'At least 8 characters.';
  });
}
