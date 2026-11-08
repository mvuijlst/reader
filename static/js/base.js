// Loaded in <head> on every page: applies the saved theme before first paint
// (dark is the default), and handles the theme toggle and confirm-before-submit.
(function () {
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem('reader.theme'); } catch (e) { /* storage blocked */ }
  root.setAttribute('data-theme', saved === 'light' ? 'light' : 'dark');

  document.addEventListener('click', function (e) {
    var button = e.target.closest && e.target.closest('[data-action="theme"]');
    if (!button) return;
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('reader.theme', next); } catch (e2) { /* ignore */ }
  });

  document.addEventListener('submit', function (e) {
    var message = e.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) e.preventDefault();
  });
})();
