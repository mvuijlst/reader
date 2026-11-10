# Release notes

Newest first. Each entry lists what was added or changed, and under
**Fixed** the issues that were resolved. Add new entries at the top, and
append every fix to the running list at the bottom.

## 2026-11-10

### Added
- `manage.py upgrade_https` (one-off, with `--dry-run`): switches http://
  subscriptions to https:// when the https address serves the feed, for
  feeds that came from an old OPML file. Items and read state are kept.
- Mobile: a "Mark all as read" button under "You've reached the end."
  (`4d46ac0`)

### Fixed
- The same article appearing several times when it is in more than one feed
  (for example BBC section feeds). A new item is skipped if another feed
  already has an item with the same URL. Existing duplicates are not
  removed. (`6451d6c`)

## 2026-11-09

### Added
- Manage page: row checkboxes with shift-click range select, and a floating
  bar to refresh or unsubscribe the selection. (`5c1dbcd`)
- `manage.py prune_opml`: checks every feed in an OPML file and writes a
  pruned copy plus a CSV report, without touching the original. (`5c1dbcd`)
- Art-deco patterns and tints as backdrops for magazine posts without a
  picture, stable per post. (`fb681e4`)

### Changed
- Toolbar: "Mark all as read" moved to the right-hand group. (`7ad2168`)
- Backdrop patterns drawn larger, with thinner lines and lower contrast.
  (`7ad2168`)

### Fixed
- Switching between "new" and "all" could hit "Mark all as read" by accident.
  (`7ad2168`)
- Quotes on picture-less magazine posts were hard to read over the busy
  backdrop. (`7ad2168`)

## 2026-11-08

### Added
- First release: a self-hosted, Google Reader-style feed reader (Django,
  SQLite, htmx) with folders and unread counts, expanded and list views,
  stars, search, keyboard shortcuts, feed discovery, favicons, podcast
  enclosures, parallel polling with conditional requests and backoff, 30-day
  auto-read, pruning of old items, OPML import/export, sanitised HTML with a
  strict CSP, and a VPS deployment runbook. (`55e772f`)
- MIT license. (`b0c7191`)
- Automatic deploy to the VPS on every push to `main`, after tests pass.
  (`298bc05`)
- Card view. (`41b9b34`)
- Deploy-setup script for PowerShell, and SSH config aliases in the setup
  scripts. (`b337b6e`)
- Drag and drop feeds into folders in the sidebar. (`befe022`)
- Magazine view, in-app dialogs in place of browser alerts, and
  portrait-aware pictures. (`fc70ef4`)
- Per-feed "Open articles on the site" for feeds that only send summaries.
  (`f574544`)
- Explorer-style sidebar. (`43c4655`)
- Clear message when a site's bot protection blocks the reader. (`76bd0a3`)
- "Disregard" (eye) button to mark a post read without opening it.
  (`0ca5469`)

### Fixed
- Bot-protection blocks showed only as a bare "HTTP 403". (`76bd0a3`)
- Items opened on their site stayed in the "new" list. They now count as
  read, including middle-clicks, and leave the list. (`43c4655`)
- The star on magazine cards could not be reached on phones. (`0ca5469`)
- A starred post's yellow styling bled into the new read button. (`0ca5469`)
- Dropping a feed could leave drag state stuck after a sidebar refresh.
  (`befe022`)
- The PowerShell setup failed against current OpenSSH ("unsupported KEX
  method") and the window closed before the output could be read.
  (`b337b6e`)

---

## Running list of issues fixed

| Date | Issue | Commit |
| --- | --- | --- |
| 2026-11-08 | Setup script window closed before output could be read; bundled `ssh-keyscan` failed on current OpenSSH | `b337b6e` |
| 2026-11-08 | Drag state stuck after a sidebar refresh during a drag | `befe022` |
| 2026-11-08 | Bot-protection blocks reported as a bare "HTTP 403" | `76bd0a3` |
| 2026-11-08 | Items opened on their site stayed in the "new" list; middle-clicks not counted as read | `43c4655` |
| 2026-11-08 | Magazine star unreachable on phones | `0ca5469` |
| 2026-11-08 | Starred styling bled into the read button | `0ca5469` |
| 2026-11-09 | "Mark all as read" could be hit by accident when switching new/all | `7ad2168` |
| 2026-11-09 | Magazine quotes hard to read over backdrop patterns | `7ad2168` |
| 2026-11-10 | Same article repeated when present in several feeds | `6451d6c` |
