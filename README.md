# Games Librarian

One app for your PC game libraries.

Games Librarian scans your installed launchers, builds a unified game list, and lets you search and launch from one desktop window.

---

## Screenshots

> Add your screenshots here after each release so users can quickly see the app UI.

- Main library view (search + filters + cards)
- Settings dialog (Steam API key)
- App console (scan/category refresh logs)

## What you get

- One combined library view across launchers
- Fast search and filtering
- Launch or install actions directly from the app
- Category enrichment (Steam + RAWG)
- Built-in console for scan/refresh logs

## Supported launchers

- Steam
- Epic Games Launcher
- EA app (Origin)
- GOG Galaxy
- Xbox / Microsoft Store ecosystem

## Install and run (dev/local)

```powershell
npm install
npm run start
```

If Electron launches but the library is empty, use **Scan PC Now** in the app to populate data.

## Build Windows installer

```powershell
npm run package:win
```

Installer output is written to `release/`.

Latest installer filename pattern:

- `Games Librarian Setup <version>.exe`

## First-time usage

1. Start the app.
2. Click **Scan PC Now** to detect your local libraries.
3. Use **Reload** when you want to re-read data and refresh categories.
4. Use the **Settings** button to manage Steam API key access.

Tip: **Reload** merges quick data reload with category refresh so you do not need separate buttons.

## Steam full library support

By default, Steam shows installed games only.

To include owned-but-not-installed games:

1. Open **Settings** in the app.
2. Add your Steam Web API key.
3. Run **Scan PC Now** again.

Notes:

- Keys are stored locally using Electron `safeStorage` encryption.
- Key entry is optional. You can cancel and continue with installed-only mode.
- Get a key at <https://steamcommunity.com/dev/apikey>.

If you cancel the prompt once, the app will not keep nagging on every startup. You can open Settings later and add/replace the key any time.

## Known limits

Games Librarian can only use data available locally on your machine unless an API integration is configured.

- Steam without API key: installed only
- Epic/EA cloud-owned libraries: not fully available from local data alone
- Xbox visibility depends on local cache/install metadata available to your Windows user

## Troubleshooting

### I only see installed Steam games

This is expected without a Steam Web API key.

Fix:

1. Open **Settings**.
2. Paste your Steam API key.
3. Click **Scan PC Now**.

### Categories are missing for some games

Category refresh uses Steam + RAWG and local cache. Some titles may still fail to match.

Try:

1. Click **Reload** (forces category refresh).
2. Open console and check missing-category reason summaries.
3. Run another refresh later (network/source availability can vary).

### Build fails on Windows packaging

Use:

```powershell
npm run package:win
```

This already runs the winCodeSign cache preparation script before electron-builder.

## Privacy and security

- Steam keys are stored locally and encrypted (Electron `safeStorage`).
- Do not commit secrets or tokens to source control.
- If you ever exposed a key, rotate it immediately in the provider portal.

## For developers

Python scanner entry point:

```powershell
python -m game_launcher_scraper --output out\games
```

Project structure:

- `electron/` desktop app (main, preload, renderer)
- `src/game_launcher_scraper/` scanner and launcher adapters
- `dist/game-library/` generated assets loaded by Electron
- `release/` packaged build outputs
