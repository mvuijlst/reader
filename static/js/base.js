// Loaded in <head> on every page: applies the saved theme before first paint
// (dark is the default), handles the theme toggle, and provides the in-app
// dialog that replaces the browser's confirm() and prompt().
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

  // --- in-app dialog ----------------------------------------------------------
  // appConfirm({title, message, confirmLabel, danger}) resolves true or false.
  // appPrompt({title, message, placeholder, value, confirmLabel}) resolves the
  // trimmed text, or null when cancelled.

  function openDialog(opts) {
    var dialog = document.getElementById('app-dialog');
    if (!dialog) return Promise.resolve(opts.input ? null : false);
    var title = document.getElementById('app-dialog-title');
    var message = document.getElementById('app-dialog-message');
    var input = document.getElementById('app-dialog-input');
    var ok = document.getElementById('app-dialog-ok');
    var cancel = document.getElementById('app-dialog-cancel');

    title.textContent = opts.title || '';
    title.hidden = !opts.title;
    message.textContent = opts.message || '';
    message.hidden = !opts.message;
    ok.textContent = opts.confirmLabel || 'OK';
    cancel.textContent = opts.cancelLabel || 'Cancel';
    dialog.classList.toggle('is-danger', !!opts.danger);

    input.hidden = !opts.input;
    input.value = opts.input ? (opts.value || '') : '';
    input.placeholder = opts.placeholder || '';
    var syncOk = function () { ok.disabled = !!opts.input && !input.value.trim(); };
    input.oninput = syncOk;
    syncOk();

    dialog.returnValue = '';
    return new Promise(function (resolve) {
      dialog.addEventListener('close', function () {
        var accepted = dialog.returnValue === 'ok';
        resolve(opts.input ? (accepted ? input.value.trim() : null) : accepted);
      }, { once: true });
      dialog.showModal();
      // Destructive confirmations start on Cancel, so a stray Enter is harmless.
      (opts.input ? input : opts.danger ? cancel : ok).focus();
    });
  }

  window.appConfirm = function (opts) { return openDialog(opts || {}); };
  window.appPrompt = function (opts) { return openDialog(Object.assign({}, opts, { input: true })); };

  // Clicking the backdrop cancels.
  document.addEventListener('click', function (e) {
    if (e.target.id === 'app-dialog') e.target.close('cancel');
  });

  // Forms that need confirming: <form data-confirm="Message" data-confirm-title="…"
  // data-confirm-label="…">.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    var question = form.getAttribute('data-confirm');
    if (!question) return;
    e.preventDefault();
    window.appConfirm({
      title: form.getAttribute('data-confirm-title'),
      message: question,
      confirmLabel: form.getAttribute('data-confirm-label'),
      danger: true,
    }).then(function (accepted) {
      if (accepted) HTMLFormElement.prototype.submit.call(form);  // doesn't re-fire 'submit'
    });
  });

  // htmx's hx-confirm, in the same dialog instead of window.confirm.
  document.addEventListener('htmx:confirm', function (e) {
    if (!e.detail.question) return;
    e.preventDefault();
    var el = e.detail.elt;
    window.appConfirm({
      title: el.getAttribute('data-confirm-title'),
      message: e.detail.question,
      confirmLabel: el.getAttribute('data-confirm-label'),
      danger: true,
    }).then(function (accepted) {
      if (accepted) e.detail.issueRequest(true);
    });
  });
})();
