# Reader

A self-hosted, single-user feed reader that works the way Google Reader did:
subscriptions in folders with unread counts, an expanded view that marks items
read as you scroll past them, a compact list view, stars, and the whole keyboard
vocabulary: `j`/`k`, `s`, `m`, `v`, `shift+a`, `g a`…

It's a small Django app on SQLite with htmx and one vanilla JS file. There's no
build step, no JavaScript framework and no background worker beyond a systemd
timer. One person, one server, all your reading history in one file.

## Features

**Reading**
- All items, Starred items, and per-folder / per-feed streams.
- **Expanded view**: full articles one after another. An item becomes read when its
  top scrolls under the toolbar, like Google Reader.
- **List view**: one line per item (source, title, snippet, date); items open inline.
- Show new items or all items, newest or oldest first, with infinite scroll.
- Search across every item you've ever fetched.
- Podcast enclosures get an audio player, and YouTube entries show their thumbnail.
- Dark theme by default with a light toggle. Works on a phone, with a drawer sidebar.

**Behaving like Google Reader**
- Unread items older than 30 days are marked read automatically, so a neglected
  feed doesn't sit at "1000+" forever.
- "Mark all as read" only marks what had arrived when you loaded the page. Anything
  newer stays unread.
- "Keep unread" sticks: scrolling past an item you kept unread won't mark it read.

**Subscriptions**
- Paste a feed URL or any web page. The feed is found through
  `<link rel="alternate">`, then common paths (`/feed`, `/rss`, `/atom.xml`, …). Sites
  that offer the same feed as RSS, Atom and JSON don't make you pick.
- YouTube channels, `@handles` and playlists work, including from EU servers that
  get YouTube's consent page.
- Folders, renaming, and per-feed stats on the Manage page (items per week, latest
  item, unread/total, fetch errors).
- OPML import and export, which works with Google Takeout's `subscriptions.xml`,
  Feedly, Inoreader, NetNewsWire, Miniflux and FreshRSS.

**Polling**
- A systemd timer runs `manage.py fetch_feeds` every 10 minutes. Each run fetches
  the feeds that are due (every 30 minutes by default), in parallel.
- Conditional requests (ETag / Last-Modified). Failing feeds back off exponentially,
  up to 24 hours, and `Retry-After` is honoured.
- Permanent redirects update the stored feed URL. Favicons are fetched and stored.
- Read, unstarred items are pruned after 180 days, but only once they've dropped
  out of the feed, so they can't come back as new. Starred items are kept forever.

**Safety**
- Feed HTML is sanitized with [nh3](https://github.com/messense/nh3): no scripts,
  iframes, inline styles or event handlers, and only `http`/`https`/`mailto` links.
- A strict Content-Security-Policy as a second line of defence. Links open in a new
  tab, and no referrer is sent to other sites.
- Login is required everywhere: one account, made with `createsuperuser`.

## Keyboard shortcuts

Press `?` in the app for this list.

| Key | Action | Key | Action |
|---|---|---|---|
| `j` / `k` | next / previous item | `s` | star / unstar |
| `n` / `p` | move without marking read | `m` | toggle read / keep unread |
| `space` | page down, then next item | `v` | open original in a new tab |
| `o` / `enter` | open / close item (list view) | `shift+a` | mark all as read |
| `shift+n` / `shift+p` | next / previous subscription | `r` | refresh |
| `shift+o` | open highlighted subscription | `1` / `2` | expanded / list view |
| `shift+x` | expand / collapse folder | `u` | hide / show sidebar |
| `g` `a` | go to all items | `a` | add a subscription |
| `g` `s` | go to starred items | `/` | search |
| `g` `m` | go to manage subscriptions | `?` | help |

## Running it locally

Requires Python 3.12+.

```bash
git clone https://github.com/mvuijlst/reader.git
cd reader
python -m venv venv
venv/bin/pip install -r requirements.txt     # Windows: venv\Scripts\pip
cp .env.example .env                          # then set DJANGO_DEBUG=true
venv/bin/python manage.py migrate
venv/bin/python manage.py createsuperuser
venv/bin/python manage.py runserver
```

Sign in at http://127.0.0.1:8000/, then subscribe to something or import an OPML
file on the Manage page. New items arrive when the fetcher runs:

```bash
venv/bin/python manage.py fetch_feeds            # feeds that are due
venv/bin/python manage.py fetch_feeds --all      # everything, now
venv/bin/python manage.py import_opml subscriptions.opml
```

Tests: `venv/bin/python manage.py test apps.feeds`

## Deploying

[deploy/RUNBOOK.md](deploy/RUNBOOK.md) walks through a Debian/Ubuntu VPS: gunicorn
as a systemd service behind nginx, certbot for TLS, and a systemd timer for polling.

Once it's set up, every push to `main` deploys itself. A GitHub Actions workflow runs
the tests, then SSHes in with a key the server pins to `deploy/update.sh`. That script
updates the code, migrates, gracefully reloads gunicorn and checks the site is up.
`deploy/setup-github-deploy.sh` does the one-time key and secrets setup.

Back up `db.sqlite3` (for example with `sqlite3 db.sqlite3 ".backup …"`). It holds
everything.

## Configuration

Settings come from environment variables, read from `.env` (see `.env.example`).

| Key | Default | |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | required when `DJANGO_DEBUG` is off |
| `DJANGO_DEBUG` | `false` | |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | comma-separated |
| `TIME_ZONE` | `Europe/Brussels` | for displayed dates |
| `READER_FETCH_INTERVAL_MINUTES` | `30` | how often a healthy feed is polled |
| `READER_FETCH_WORKERS` | `8` | parallel downloads per run |
| `READER_AUTO_READ_DAYS` | `30` | auto-mark old unread items read (`0` = never) |
| `READER_KEEP_DAYS` | `180` | prune read, unstarred items older than this (`0` = keep all) |

## Design notes

- **Single user on purpose.** There's no per-user data model. Preferences, read
  state and stars belong to whoever logs in. That keeps the queries and the code
  simple.
- **SQLite in WAL mode** lets the fetch timer write while the web app reads. One
  person's feeds, even a few hundred of them, are nowhere near its limits.
- **Server-rendered.** Streams are Django templates. htmx swaps the main pane and
  loads more items on scroll. `static/js/reader.js` adds keyboard handling, read and
  star state, and live unread counts on top.
- **Keyset pagination** (by published date and id) instead of offsets, so marking
  items read while you scroll never makes the next page skip items.
- Downloads run in a thread pool, and every database write happens on one thread.

## Project layout

```
reader/                 settings, urls, wsgi
apps/feeds/
  models.py             Folder, Feed, Item, Preferences
  views.py              streams, item-state API, subscribe, manage, OPML
  services/
    fetcher.py          parallel polling, storing, auto-read and pruning
    discovery.py        find the feed behind a URL
    sanitize.py         nh3 policy, text snippets
    opml.py             import / export
    http.py             GET with a size cap and redirect tracking
  management/commands/  fetch_feeds, import_opml
  templates/feeds/      reader shell, stream and item partials, manage page
  tests/
static/js/reader.js     keyboard, read/star state, counts, scroll-to-read
static/css/reader.css
deploy/                 systemd units, nginx vhost, runbook, update script
```

## Not (yet) included

- The Google Reader sync API, which would let native apps such as Reeder or
  NetNewsWire sync with it.
- Full-text search: search is a plain substring match, which is fine at one
  person's scale.
- Fetching full article text for feeds that publish only summaries.
- Sharing, comments, multiple users.

## License

[MIT](LICENSE)
