// Reader: keyboard navigation, read/star state, unread counts, scroll-to-read.
// The server renders everything; this file only adds behaviour on top.
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const main = $('#main');
  if (!main) return;

  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem('reader.' + key); return v === null ? fallback : JSON.parse(v); }
      catch (e) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem('reader.' + key, JSON.stringify(value)); } catch (e) { /* ignore */ }
    },
  };

  let current = null;     // the current item (<article class="item">)
  let navCursor = null;   // sidebar entry highlighted with shift+n / shift+p
  let gPrefix = false;    // waiting for the second key of a "g x" chord
  let gTimer = null;

  const stream = () => $('#stream');
  const view = () => stream()?.dataset.view || 'expanded';
  const isList = () => view() === 'list';
  const isCards = () => view() === 'cards';
  const isMagazine = () => view() === 'magazine';
  const usesOverlay = () => isCards() || isMagazine();  // items open in the reading overlay
  const isExpanded = () => view() === 'expanded';
  const items = () => $$('#items > .item');
  const overlay = $('#card-reader');
  const overlayOpen = () => overlay.open;
  const markOnScroll = () => stream()?.dataset.markOnScroll === '1';

  // --- server calls ---------------------------------------------------------

  async function post(url, data) {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': $('meta[name="csrf-token"]').content,
        'X-Requested-With': 'fetch',
      },
      body: new URLSearchParams(data),
    });
    if (response.redirected && response.url.includes('/login/')) {
      window.location.reload();  // session expired
      throw new Error('signed out');
    }
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.status === 204 ? null : response.json();
  }

  function reload() {
    if (!stream()) return;
    htmx.ajax('GET', location.pathname + location.search, { target: '#main', swap: 'innerHTML' });
    refreshNav();
  }

  function refreshNav() { htmx.trigger(document.body, 'refresh-nav'); }

  function go(url) { htmx.ajax('GET', url, { target: '#main', swap: 'innerHTML' }).then(() => history.pushState({}, '', url)); }

  async function setPrefs(data) {
    try { await post('/api/prefs/', data); reload(); }
    catch (e) { toast("Couldn't save that setting."); }
  }

  // --- toast ----------------------------------------------------------------

  let toastTimer = null;
  function toast(message) {
    const el = $('#toast');
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
  }

  // --- unread counts ----------------------------------------------------------

  function formatCount(n) { return n <= 0 ? '' : n > 1000 ? '1000+' : String(n); }

  function setCount(el, n) {
    n = Math.max(0, n);
    el.dataset.n = n;
    el.textContent = formatCount(n);
    (el.closest('li') || el.closest('.nav-item'))?.classList.toggle('has-unread', n > 0);
  }

  function adjustCounts(item, delta) {
    const keys = ['all', 'feed:' + item.dataset.feed];
    if (item.dataset.folder) keys.push('folder:' + item.dataset.folder);
    for (const key of keys) {
      for (const el of $$(`[data-count="${key}"]`)) setCount(el, (+el.dataset.n || 0) + delta);
    }
    const head = $('.js-unread-count');
    if (head) { const n = Math.max(0, (+head.dataset.n || 0) + delta); head.dataset.n = n; head.textContent = n; }
    updateTitle();
  }

  function adjustStarred(delta) {
    const el = $('[data-starred-count]');
    if (!el) return;
    const n = Math.max(0, (+el.dataset.n || 0) + delta);
    el.dataset.n = n;
    el.textContent = n || '';
  }

  function updateTitle() {
    const n = +($('[data-count="all"]')?.dataset.n || 0);
    const name = stream()?.dataset.title || 'Reader';
    document.title = (n ? `(${formatCount(n)}) ` : '') + name + ' – Reader';
  }

  // --- item state -----------------------------------------------------------

  async function setRead(item, read, { explicit = false } = {}) {
    if (!item) return;
    const unread = item.classList.contains('unread');
    if (read !== unread) return;  // already in that state
    if (read && item.dataset.kept && !explicit) return;  // "keep unread" sticks
    if (!read) item.dataset.kept = '1'; else delete item.dataset.kept;
    item.classList.toggle('unread', !read);
    syncOverlay();
    adjustCounts(item, read ? -1 : 1);
    try {
      await post(`/api/items/${item.dataset.id}/`, { read: read ? 1 : 0 });
    } catch (e) {
      item.classList.toggle('unread', read);
      syncOverlay();
      adjustCounts(item, read ? 1 : -1);
      toast("Couldn't save — are you offline?");
    }
  }

  function toggleRead(item) {
    if (item) setRead(item, item.classList.contains('unread'), { explicit: true });
  }

  async function toggleStar(item) {
    if (!item) return;
    const on = !item.classList.contains('starred');
    item.classList.toggle('starred', on);
    syncOverlay();
    adjustStarred(on ? 1 : -1);
    try {
      await post(`/api/items/${item.dataset.id}/`, { starred: on ? 1 : 0 });
    } catch (e) {
      item.classList.toggle('starred', !on);
      syncOverlay();
      adjustStarred(on ? -1 : 1);
      toast("Couldn't save — are you offline?");
    }
  }

  // --- card view: the reading overlay -----------------------------------------

  // The overlay always shows `current`; it mirrors that card's read/star classes
  // so its buttons ("Keep unread", "Starred") read correctly.
  function syncOverlay() {
    if (!overlayOpen() || !current) return;
    const body = $('#card-reader-item');
    body.classList.toggle('unread', current.classList.contains('unread'));
    body.classList.toggle('starred', current.classList.contains('starred'));
  }

  function showInOverlay(item) {
    const tpl = $('template', item);
    if (!tpl) return;
    const body = $('#card-reader-item');
    body.replaceChildren(tpl.content.cloneNode(true));
    htmx.process(body);
    $('#card-reader-scroll').scrollTop = 0;
    if (!overlayOpen()) overlay.showModal();
    $('#card-reader-scroll').focus({ preventScroll: true });
    syncOverlay();
  }

  function closeOverlay() {
    if (overlayOpen()) overlay.close();
  }

  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeOverlay(); });  // backdrop

  // --- current item, opening, scrolling ---------------------------------------

  // Height of the toolbar covering the top of the pane (it scrolls away on phones).
  function headHeight() {
    const head = $('.stream-head');
    return head && getComputedStyle(head).position === 'sticky' ? head.offsetHeight : 0;
  }

  function expand(item) {
    if (item.classList.contains('open')) return;
    const tpl = $('template', item);
    if (!tpl) return;
    const body = document.createElement('div');
    body.className = 'row-body';
    body.append(tpl.content.cloneNode(true));
    item.append(body);
    item.classList.add('open');
    htmx.process(body);
  }

  function collapse(item) {
    item.classList.remove('open');
    $('.row-body', item)?.remove();
  }

  function scrollToItem(item) {
    const rect = item.getBoundingClientRect();
    const view = main.getBoundingClientRect();
    const top = main.scrollTop + rect.top - view.top - headHeight();
    if (isExpanded() || item.classList.contains('open')) {
      main.scrollTop = top - (isList() ? 0 : 12);
      return;
    }
    // List rows and cards: only scroll when the item is out of view.
    if (rect.top < view.top + headHeight()) main.scrollTop = top - 8;
    else if (rect.bottom > view.bottom) main.scrollTop += rect.bottom - view.bottom + 8;
  }

  function select(item, { open = false, read = true, scroll = true } = {}) {
    if (!item) return;
    if (current && current !== item) {
      current.classList.remove('current');
      if (isList()) collapse(current);
    }
    current = item;
    item.classList.add('current');
    if (isList() && open) expand(item);
    if (usesOverlay() && open) showInOverlay(item);
    if (scroll) scrollToItem(item);
    if (read && (isExpanded() || open)) setRead(item, true);
  }

  function move(direction, { read = true } = {}) {
    const list = items();
    if (!list.length) return;
    let index = current ? list.indexOf(current) : -1;
    const target = index === -1 ? (direction > 0 ? list[0] : null) : list[index + direction];
    if (!target) {
      if (direction > 0) $('#items .more')?.scrollIntoView({ block: 'end' });
      return;
    }
    // j/k open the next item (list row inline, card in the overlay); n/p only
    // move the highlight, unless the card overlay is already showing. Items from
    // feeds set to open on their site never open the overlay on j/k (it would
    // hold one sentence) and are left for o/Enter/click, which go to the site.
    const site = opensOnSite(target);
    const open = (isList() && read) || (usesOverlay() && (overlayOpen() || (read && !site)));
    select(target, { open, read });
  }

  // Items from feeds that only send summaries can be set to open on their site.
  const opensOnSite = (item) => item?.dataset.openSite === '1' && !!item.dataset.url;

  function toggleOpen(item) {
    if (!item) return;
    if (opensOnSite(item)) {
      // One step to the article: new tab, item marked read, highlight follows.
      if (overlayOpen()) closeOverlay();
      select(item, { read: false, scroll: false });
      openOriginal(item);
      return;
    }
    if (usesOverlay()) {
      if (overlayOpen() && item === current) closeOverlay();
      else select(item, { open: true });
      return;
    }
    if (!isList()) return;
    if (item !== current) { select(item, { open: true }); return; }
    if (item.classList.contains('open')) { collapse(item); scrollToItem(item); }
    else { expand(item); scrollToItem(item); setRead(item, true); }
  }

  function openOriginal(item) {
    if (!item?.dataset.url) return;
    window.open(item.dataset.url, '_blank', 'noopener,noreferrer');
    setRead(item, true);
  }

  function pageDown(up) {
    const pane = overlayOpen() ? $('#card-reader-scroll') : main;
    const covered = overlayOpen() ? 0 : headHeight();
    const step = Math.max(80, pane.clientHeight - covered - 60);
    const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 2;
    if (!up && atBottom) { move(1); return; }
    pane.scrollBy({ top: up ? -step : step });
  }

  // A thumbnail that fails to load (hotlink protection, 404) turns its card
  // into a text card instead of showing a broken image.
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img.tagName !== 'IMG') return;
    if (img.closest('.card-media')) {
      img.closest('.card')?.classList.add('card-text-only');
      img.closest('.card-media').remove();
    } else if (img.closest('.mag-media')) {
      // Magazine: fall back to the tinted backdrop (with the snippet on small cards).
      const media = img.closest('.mag-media');
      img.closest('.mag')?.classList.add('mag-noimg');
      for (const el of $$('img', media)) el.remove();
    }
  }, true);

  // Card and magazine pictures: landscape and square ones are cropped to fill the
  // card (CSS default). Portrait ones (at least 5% taller than wide) are shown
  // whole on a blurred copy of themselves instead.
  function fitPicture(img) {
    if (!img.classList?.contains('pic-img') || !img.naturalWidth) return;
    const portrait = img.naturalWidth / img.naturalHeight < 0.95;
    img.closest('.pic')?.classList.toggle('pic-portrait', portrait);
  }
  document.addEventListener('load', (e) => { if (e.target.tagName === 'IMG') fitPicture(e.target); }, true);
  const fitLoadedPictures = (root = document) => {
    for (const img of $$('img.pic-img', root)) if (img.complete) fitPicture(img);
  };

  // Expanded view: an item becomes current (and read) once its top scrolls
  // under the toolbar, the way Google Reader did it.
  let ticking = false;
  main.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; onScroll(); });
  }, { passive: true });

  function onScroll() {
    if (!stream() || !isExpanded()) return;
    const line = main.getBoundingClientRect().top + headHeight() + 16;
    let passed = null;
    for (const item of items()) {
      if (item.getBoundingClientRect().top > line) break;
      passed = item;
      if (markOnScroll()) setRead(item, true);
    }
    if (passed && passed !== current) select(passed, { scroll: false, read: false });
  }

  async function markAllRead() {
    const s = stream();
    if (!s || !$('[data-action="mark-all-read"]', s)) return;
    const scope = s.dataset.scope;
    const n = +($('.js-unread-count')?.dataset.n || 0);
    if (!n) { toast('Nothing to mark.'); return; }
    if (scope === 'all') {
      const ok = await appConfirm({
        title: `Mark all ${n} items as read?`,
        message: 'Every feed, not just one folder. Items that arrive after you loaded this page stay unread.',
        confirmLabel: 'Mark as read',
      });
      if (!ok) return;
    }
    try {
      const res = await post('/api/mark-all-read/', { scope, max_id: s.dataset.maxId });
      toast(`Marked ${res.marked} item${res.marked === 1 ? '' : 's'} as read.`);
      reload();
    } catch (e) {
      toast("Couldn't mark items as read.");
    }
  }

  // --- sidebar --------------------------------------------------------------

  function collapsedFolders() { return new Set(store.get('collapsed', [])); }

  function applyNavState() {
    const collapsed = collapsedFolders();
    for (const li of $$('#nav-tree li.folder')) {
      const isCollapsed = collapsed.has(li.dataset.folder);
      li.classList.toggle('collapsed', isCollapsed);
      $('.twisty', li)?.setAttribute('aria-expanded', String(!isCollapsed));
    }
    $('#nav-tree')?.classList.toggle('hide-read', !!store.get('hideRead', false));
    markSelected();
    updateTitle();
  }

  function toggleFolder(li) {
    if (!li) return;
    const collapsed = collapsedFolders();
    const id = li.dataset.folder;
    collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
    store.set('collapsed', [...collapsed]);
    applyNavState();
  }

  function markSelected() {
    const scope = stream()?.dataset.scope;
    for (const el of $$('#sidebar .selected')) el.classList.remove('selected');
    for (const el of $$('#sidebar .is-selected')) el.classList.remove('is-selected');
    if (!scope) return;
    const link = $(`#sidebar .nav-item[data-scope="${scope}"]`);
    if (!link) return;
    link.classList.add('selected');
    link.closest('li')?.classList.add('is-selected');
    link.closest('li.folder')?.classList.add('is-selected');
  }

  function visibleNavItems() {
    return $$('#sidebar .nav-item[data-scope]').filter((el) => el.offsetParent !== null);
  }

  function navMove(direction) {
    const list = visibleNavItems();
    if (!list.length) return;
    const start = navCursor && list.includes(navCursor) ? navCursor : $('#sidebar .nav-item.selected');
    let index = list.indexOf(start);
    index = index === -1 ? 0 : Math.min(list.length - 1, Math.max(0, index + direction));
    navCursor?.classList.remove('nav-cursor');
    navCursor = list[index];
    navCursor.classList.add('nav-cursor');
    navCursor.scrollIntoView({ block: 'nearest' });
  }

  function navOpen() {
    if (navCursor && document.contains(navCursor)) navCursor.click();
  }

  function toggleNav(force) {
    const open = typeof force === 'boolean' ? force : !document.body.classList.contains('nav-open');
    document.body.classList.toggle('nav-open', open);
  }

  function openSubscribe() {
    const form = $('.subscribe-form');
    const button = $('[data-action="subscribe"]');
    if (!form) return;
    const show = form.hidden;
    form.hidden = !show;
    button?.setAttribute('aria-expanded', String(show));
    if (show) {
      if (window.matchMedia('(max-width: 899px)').matches) toggleNav(true);
      $('#subscribe-url')?.focus();
    }
  }

  // --- drag feeds into folders ------------------------------------------------
  // Drop on a folder (its row or any feed inside it) to move the feed there; the
  // drop zones under the list take it out of its folder or into a new one.

  const sidebar = $('#sidebar');
  let dragged = null;       // { id, folder, name } of the feed being dragged
  let dropHighlight = null;

  function dropTargetFor(el) {
    if (!dragged || !el?.closest) return null;
    const folder = el.closest('#nav-tree li.folder');
    if (folder) {
      if (folder.dataset.folder === dragged.folder) return null;  // already there
      return { el: folder, folder: folder.dataset.folder, name: $(':scope > .nav-item .label', folder)?.textContent.trim() };
    }
    const zone = el.closest('#nav-tree .drop-zone');
    if (zone?.dataset.drop === 'new-folder') return { el: zone, newFolder: true };
    if (zone?.dataset.drop === 'unfiled' && dragged.folder) return { el: zone, folder: '' };
    return null;
  }

  function highlightDrop(el) {
    if (dropHighlight === el) return;
    dropHighlight?.classList.remove('drop-target');
    dropHighlight = el;
    el?.classList.add('drop-target');
  }

  sidebar.addEventListener('dragstart', (e) => {
    const link = e.target.closest?.('li.feed > a.nav-item');
    if (!link) return;
    const li = link.closest('li.feed');
    dragged = {
      id: link.dataset.scope.split(':')[1],
      folder: li.closest('li.folder')?.dataset.folder || '',
      name: $('.label', link)?.textContent.trim() || 'feed',
    };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('application/x-reader-feed', dragged.id);
    li.classList.add('dragging');
    document.body.classList.add('dragging-feed');
    document.body.classList.toggle('dragging-filed', !!dragged.folder);
  });

  sidebar.addEventListener('dragover', (e) => {
    const target = dropTargetFor(e.target);
    highlightDrop(target?.el || null);
    if (!target) return;
    e.preventDefault();  // allow the drop
    e.dataTransfer.dropEffect = 'move';
  });

  sidebar.addEventListener('dragleave', (e) => {
    if (!sidebar.contains(e.relatedTarget)) highlightDrop(null);
  });

  function endDrag() {
    dragged = null;
    highlightDrop(null);
    document.body.classList.remove('dragging-feed', 'dragging-filed');
    for (const el of $$('#nav-tree li.dragging')) el.classList.remove('dragging');
  }

  sidebar.addEventListener('drop', async (e) => {
    const target = dropTargetFor(e.target);
    const feed = dragged;
    // Clean up here too: if the sidebar refreshed mid-drag, dragend fires on a
    // detached element and never reaches the document.
    endDrag();
    if (!target || !feed) return;
    e.preventDefault();
    let data = { folder: target.folder };
    if (target.newFolder) {
      const name = await appPrompt({
        title: 'New folder',
        message: `Move ${feed.name} into a new folder called:`,
        placeholder: 'Folder name',
        confirmLabel: 'Create folder',
      });
      if (!name) return;
      data = { new_folder: name };
    }
    try {
      const res = await post(`/api/feeds/${feed.id}/move/`, data);
      // Items already on screen now count towards the new folder.
      for (const item of $$(`#items > .item[data-feed="${feed.id}"]`)) item.dataset.folder = res.folder ?? '';
      toast(res.folder ? `Moved ${feed.name} to ${res.folder_name}.` : `Moved ${feed.name} out of its folder.`);
      refreshNav();
    } catch (err) {
      toast("Couldn't move that feed.");
    }
  });

  document.addEventListener('dragend', endDrag);

  // --- help -----------------------------------------------------------------

  function toggleHelp() {
    const dialog = $('#help');
    if (dialog.open) dialog.close(); else dialog.showModal();
  }
  $('#help').addEventListener('click', (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });

  // --- clicks ---------------------------------------------------------------

  document.addEventListener('click', (e) => {
    const control = e.target.closest('[data-action]');
    // Inside the card overlay, the item is the card it is showing.
    const item = e.target.closest('#items .item') || (e.target.closest('#card-reader') ? current : null);
    const action = control?.dataset.action;

    switch (action) {
      case 'card-next': move(1); return;
      case 'card-prev': move(-1); return;
      case 'card-close': closeOverlay(); return;
      case 'star': e.preventDefault(); e.stopPropagation(); toggleStar(item); return;
      case 'read': toggleRead(item); return;
      case 'toggle': toggleOpen(item); return;
      case 'open-original': setRead(item, true); return;  // let the link open
      case 'view': setPrefs({ view_mode: control.dataset.value }); return;
      case 'unread-only': setPrefs({ unread_only: control.dataset.value }); return;
      case 'sort': control.closest('details')?.removeAttribute('open'); setPrefs({ oldest_first: control.dataset.value }); return;
      case 'scroll-mode': control.closest('details')?.removeAttribute('open'); setPrefs({ mark_read_on_scroll: control.dataset.value }); return;
      case 'mark-all-read': markAllRead(); return;
      case 'reload': reload(); return;
      case 'toggle-folder': toggleFolder(control.closest('li.folder')); return;
      case 'toggle-read-feeds': store.set('hideRead', !store.get('hideRead', false)); applyNavState(); return;
      case 'toggle-nav': toggleNav(); return;
      case 'subscribe': openSubscribe(); return;
      case 'help': toggleHelp(); return;
    }
    // Clicking anywhere in an expanded entry makes it the current one.
    if (item && item !== current && isExpanded()) select(item, { scroll: false });

    // Close open menus when clicking elsewhere.
    for (const menu of $$('details.menu[open]')) if (!menu.contains(e.target)) menu.removeAttribute('open');
  });

  // --- keyboard -------------------------------------------------------------

  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    const target = e.target;
    if (target.closest('input, textarea, select, [contenteditable="true"]')) {
      if (e.key === 'Escape') target.blur();
      return;
    }
    if ($('#app-dialog')?.open) return;  // the dialog handles its own keys
    if ($('#help').open && e.key !== '?') return;
    // Letters: Shift decides the case, not Caps Lock or how the event was synthesized.
    const key = /^[a-z]$/i.test(e.key) ? (e.shiftKey ? e.key.toUpperCase() : e.key.toLowerCase()) : e.key;
    if ((key === 'Enter' || key === ' ') && target.closest('button, a, summary, audio, video')) return;

    if (gPrefix) {
      gPrefix = false;
      clearTimeout(gTimer);
      if (key === 'a' || key === 'h') go('/all/');
      else if (key === 's') go('/starred/');
      else if (key === 'm') window.location.href = '/manage/';
      else return;
      e.preventDefault();
      return;
    }

    switch (key) {
      case 'j': move(1); break;
      case 'k': move(-1); break;
      case 'n': move(1, { read: false }); break;
      case 'p': move(-1, { read: false }); break;
      case 'o': case 'Enter': toggleOpen(current); break;
      case 's': toggleStar(current); break;
      case 'm': toggleRead(current); break;
      case 'v': openOriginal(current); break;
      case 'A': markAllRead(); break;
      case 'r': reload(); break;
      case 'u': document.body.classList.toggle('fullscreen'); break;
      case '1': if (!isExpanded()) { closeOverlay(); setPrefs({ view_mode: 'expanded' }); } break;
      case '2': if (!isList()) { closeOverlay(); setPrefs({ view_mode: 'list' }); } break;
      case '3': if (!isCards()) setPrefs({ view_mode: 'cards' }); break;
      case '4': if (!isMagazine()) setPrefs({ view_mode: 'magazine' }); break;
      case 'N': navMove(1); break;
      case 'P': navMove(-1); break;
      case 'O': navOpen(); break;
      case 'X': toggleFolder(navCursor?.closest('li.folder')); break;
      case 'g': gPrefix = true; gTimer = setTimeout(() => { gPrefix = false; }, 1500); break;
      case '/': $('#search-input')?.focus(); $('#search-input')?.select(); break;
      case 'a': openSubscribe(); break;
      case '?': toggleHelp(); break;
      case ' ': pageDown(e.shiftKey); break;
      case 'Escape':
        if (overlayOpen()) { closeOverlay(); break; }
        toggleNav(false);
        for (const m of $$('details.menu[open]')) m.removeAttribute('open');
        break;
      default: return;
    }
    e.preventDefault();
  });

  // --- htmx lifecycle ---------------------------------------------------------

  document.body.addEventListener('htmx:beforeSwap', (e) => {
    const url = e.detail.xhr?.responseURL || '';
    if (url.includes('/login/')) { window.location.href = url; e.detail.shouldSwap = false; }
  });

  document.body.addEventListener('htmx:afterSwap', (e) => {
    const target = e.detail.target;
    if (target === main) {
      closeOverlay();
      current = null;
      main.scrollTop = 0;
      toggleNav(false);
      markSelected();
      updateTitle();
      main.focus({ preventScroll: true });
    } else if (target.id === 'nav-tree') {
      navCursor = null;
      applyNavState();
    }
    // Cached pictures can finish loading before their load listener sees them.
    fitLoadedPictures(main);
  });

  document.body.addEventListener('htmx:responseError', () => toast('The server had a problem with that.'));
  document.body.addEventListener('htmx:sendError', () => toast("Can't reach the server — are you offline?"));

  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshNav(); });

  // Flash messages from full page loads fade out on their own.
  setTimeout(() => $('.flash')?.classList.add('gone'), 5000);

  applyNavState();
  fitLoadedPictures(main);
})();
