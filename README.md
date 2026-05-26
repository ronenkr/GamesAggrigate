# Game Launcher Scraper

Scan local launcher data for Steam, Epic Games Launcher, EA app, and GOG Galaxy, then generate a searchable HTML library.

## Usage

```powershell
python -m game_launcher_scraper --output out\games
```

## Desktop app (Electron)

The repository now includes an Electron desktop shell under `electron/`.
It loads the generated game data from `dist/game-library/assets/games-data.js`
and routes Launch clicks through Electron's main process so custom protocols
and `shell:AppsFolder` targets are actually executed on Windows.

```powershell
npm install
npm run start
```

To build a Windows installer/exe:

```powershell
npm run package:win
```

The generator writes an `index.html` file plus an `assets` folder containing CSS, JavaScript, game data, and icons.

If a game does not have a local launcher icon, the exporter will try to download a cover image from Wikipedia and cache it under `assets/icons/remote/`. When that lookup fails, it falls back to a generated SVG tile.

## Coverage limits

The scraper only reads launcher data that is stored on the local disk in a parseable form. This means:

- **GOG Galaxy**: full owned library is read from the local SQLite store (installed and not installed).
- **Steam**:
  - *Without an API key*: only games that are **installed locally** (have an `appmanifest_*.acf` under any `steamapps` library) are discovered.
  - *With a Steam Web API key*: the full owned library is fetched from `IPlayerService/GetOwnedGames` and merged with the installed manifests. Cover art is pulled directly from `cdn.cloudflare.steamstatic.com` (no Wikipedia round-trip).
  - In the Electron app, if no key is configured, a prompt lets the user enter a key or Cancel (installed-only mode).
  - Keys saved from the Electron app are stored encrypted in local app data using Electron `safeStorage`.
  - Supply the key via the `STEAM_API_KEY` env var (recommended) or `--steam-api-key`. The SteamID is auto-detected from `loginusers.vdf`; override with `STEAM_ID` or `--steam-id` if multiple accounts are present.
  - Obtain a key at <https://steamcommunity.com/dev/apikey>. **Never commit the key to source control.**
- **Epic Games Launcher**: only games that are **installed locally** (have a `.item` manifest under `Saved\Manifests`) are discovered. The Epic owned-but-not-installed library lives only in the Epic cloud account and is not cached in plaintext on disk — surfacing it would require authenticating to Epic's GraphQL store API, which is out of scope.
- **EA app (formerly Origin)**: only games that are **installed locally** are discovered, via the per-game folders under `C:\ProgramData\EA Desktop\InstallData\<Game Name>\`. The full EA owned library is only available through EA's Juno cloud API and requires authenticated calls, which is out of scope.
- **GOG noise filter**: promotional vouchers, discount codes, and add-ons (e.g. "Game Overlay 2", "CDPR Gear - discount code") are filtered out using GOG Galaxy's `ReleaseProperties.isDlc` flag.

## Extending

Add a new launcher by implementing `LauncherAdapter` in `src/game_launcher_scraper/launchers/base.py` and registering it in `src/game_launcher_scraper/launchers/__init__.py`.

Third-party adapters can also be discovered through the `game_launcher_scraper.launchers` entry-point group.
