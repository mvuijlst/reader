// Reader's service worker, served at /sw.js so it covers every page.
//
// It is here so that phones offer to install Reader as an app, and so the
// installed app says it can't reach the server instead of showing a browser
// error page. It caches nothing: every page still comes from the server, and
// htmx requests, images and the rest don't pass through here at all.
'use strict';

var OFFLINE_PAGE = [
  '<!doctype html><html lang="en"><head><meta charset="utf-8">',
  '<meta name="viewport" content="width=device-width, initial-scale=1">',
  '<meta name="theme-color" content="#141414">',
  '<title>Reader</title>',
  '<style>',
  'body{margin:0;min-height:100vh;display:grid;place-items:center;background:#141414;color:#ece9e4;',
  'font:16px/1.5 system-ui,sans-serif;text-align:center;padding:24px;box-sizing:border-box}',
  'h1{font-size:20px;margin:0 0 8px}p{margin:0 0 20px;color:#bab6af}',
  'a{display:inline-block;padding:10px 18px;border-radius:7px;background:#f08a4b;color:#1c0f05;',
  'text-decoration:none;font-weight:600}',
  '</style></head><body><main>',
  '<h1>Can’t reach Reader</h1>',
  '<p>Check your connection and try again.</p>',
  '<a href="">Try again</a>',
  '</main></body></html>'
].join('');

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil((async function () {
    // Let the browser start loading the page while this worker boots.
    if (self.registration.navigationPreload) {
      await self.registration.navigationPreload.enable();
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', function (event) {
  if (event.request.mode !== 'navigate') return;
  event.respondWith((async function () {
    try {
      var preloaded = await event.preloadResponse;
      return preloaded || await fetch(event.request);
    } catch (e) {
      return new Response(OFFLINE_PAGE, {
        status: 503,
        headers: { 'Content-Type': 'text/html; charset=utf-8' }
      });
    }
  })());
});
