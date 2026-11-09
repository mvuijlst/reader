// Manage page: select feeds and act on them together.
// Click a row's checkbox (or an empty part of the row) to toggle it; shift-click
// another to give every row in between the same state. The bar at the bottom
// refreshes or unsubscribes the selection; Esc clears it.
(function () {
  'use strict';
  const table = document.querySelector('table.subs');
  const form = document.getElementById('bulk-form');
  if (!table || !form) return;
  const all = document.getElementById('bulk-all');
  const count = form.querySelector('.bulk-count');
  let anchor = null;  // feed id of the row clicked last: where a shift-click range starts

  const boxes = () => [...table.querySelectorAll('tbody .bulk-check')];
  const selected = () => boxes().filter((box) => box.checked);
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  function sync() {
    const list = boxes();
    const n = list.filter((box) => box.checked).length;
    for (const box of list) box.closest('tr').classList.toggle('is-selected', box.checked);
    all.checked = n > 0 && n === list.length;
    all.indeterminate = n > 0 && n < list.length;
    form.hidden = n === 0;
    count.textContent = `${plural(n, 'feed')} selected`;
  }

  function selectRange(fromId, toBox) {
    const list = boxes();
    const to = list.indexOf(toBox);
    const from = list.findIndex((box) => box.value === fromId);
    if (from < 0) return;
    for (const box of list.slice(Math.min(from, to), Math.max(from, to) + 1)) box.checked = toBox.checked;
  }

  function clear() {
    for (const box of boxes()) box.checked = false;
    anchor = null;
    sync();
  }

  // Each row saves its fields through htmx on 'change'. Selecting isn't an edit,
  // so stop those changes on the way down, before they reach the row.
  table.addEventListener('change', (e) => {
    if (!e.target.matches('.bulk-check, #bulk-all')) return;
    e.stopPropagation();
    if (e.target === all) {
      for (const box of boxes()) box.checked = all.checked;
      anchor = null;
    }
    sync();
  }, true);

  // Shift-click on a row would otherwise select its text.
  table.addEventListener('mousedown', (e) => {
    if (e.shiftKey && e.target.closest('tbody tr') && !e.target.closest('input, select, textarea')) e.preventDefault();
  });

  table.addEventListener('click', (e) => {
    const row = e.target.closest('tbody tr');
    if (!row) return;
    let box = e.target.closest('.bulk-check');
    if (!box) {
      // An empty part of the row toggles it too; fields, links and buttons keep their own jobs.
      if (e.target.closest('input, select, textarea, button, a, label')) return;
      box = row.querySelector('.bulk-check');
      if (!box) return;
      box.checked = !box.checked;
    }
    // A click on the checkbox itself has already toggled it by now.
    if (e.shiftKey && anchor && anchor !== box.value) selectRange(anchor, box);
    anchor = box.value;
    sync();
  });

  form.querySelector('[data-bulk-clear]').addEventListener('click', clear);

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || form.hidden || document.querySelector('dialog[open]')) return;
    if (e.target.closest && e.target.closest('input[type="text"], select, textarea')) return;
    clear();
  });

  // Unsubscribing asks first, in the app's own dialog.
  form.addEventListener('submit', async (e) => {
    const button = e.submitter;
    const n = selected().length;
    if (!n) { e.preventDefault(); return; }
    if (!button || button.value !== 'unsubscribe') return;
    if (form.dataset.confirmed) { delete form.dataset.confirmed; return; }
    e.preventDefault();
    const ok = await window.appConfirm({
      title: `Unsubscribe from ${plural(n, 'feed')}?`,
      message: 'Their items are deleted, starred ones included.',
      confirmLabel: 'Unsubscribe',
      danger: true,
    });
    if (!ok) return;
    form.dataset.confirmed = '1';
    form.requestSubmit(button);
  });

  // Editing a row swaps in a fresh copy of it (unticked), and unsubscribing
  // one removes it: keep the selection and the counts right.
  let keep = new Set();
  document.body.addEventListener('htmx:beforeSwap', () => {
    keep = new Set(selected().map((box) => box.value));
  });
  document.body.addEventListener('htmx:afterSettle', () => {
    for (const box of boxes()) if (keep.has(box.value)) box.checked = true;
    sync();
  });

  sync();
})();
