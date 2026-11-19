// This runs before the stylesheets so a saved preference is applied on first paint.
(() => {
  const key = 'carebridge:assistant-theme';
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'light';
  try {
    const saved = localStorage.getItem(key);
    if (saved === 'light' || saved === 'dark' || saved === 'system') preference = saved;
  } catch { /* The theme still works when storage is unavailable. */ }

  function applyTheme() {
    const theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.assistantTheme = theme;
    document.querySelector('meta[name="color-scheme"]').content = theme;
    document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#0d171e' : '#edf3f5';
  }

  applyTheme();
  media.addEventListener('change', applyTheme);
  document.addEventListener('DOMContentLoaded', () => {
    const select = document.getElementById('assistant-theme');
    select.value = preference;
    select.closest('label').hidden = false;
    select.addEventListener('change', () => {
      preference = select.value;
      try { localStorage.setItem(key, preference); }
      catch { /* Apply the preference for the current page. */ }
      applyTheme();
    });
  });
})();
