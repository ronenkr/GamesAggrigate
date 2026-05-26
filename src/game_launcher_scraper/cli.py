from __future__ import annotations

import argparse
import os
from pathlib import Path

from .catalog import scan_launchers
from .models import ScanConfig
from .renderer import render_site


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape local game launchers and generate a searchable HTML library.")
    parser.add_argument("--output", type=Path, default=Path("dist/game-library"), help="Output directory for the generated site.")
    parser.add_argument("--steam-root", type=Path, help="Override the Steam install root.")
    parser.add_argument("--epic-manifest-dir", type=Path, help="Override the Epic manifest directory.")
    parser.add_argument("--ea-manifest-dir", type=Path, help="Override the EA manifest directory.")
    parser.add_argument("--gog-root", type=Path, help="Override the GOG Galaxy storage root.")
    parser.add_argument(
        "--steam-api-key",
        default=os.environ.get("STEAM_API_KEY"),
        help="Steam Web API key (also read from STEAM_API_KEY). Enables full owned-library fetch.",
    )
    parser.add_argument(
        "--steam-id",
        default=os.environ.get("STEAM_ID"),
        help="64-bit SteamID (also read from STEAM_ID). Auto-detected from loginusers.vdf when omitted.",
    )
    parser.add_argument("--no-external-adapters", action="store_true", help="Disable entry-point based launcher adapters.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = ScanConfig(
        steam_root=args.steam_root,
        epic_manifest_dir=args.epic_manifest_dir,
        ea_manifest_dir=args.ea_manifest_dir,
        gog_root=args.gog_root,
        output_dir=args.output,
        include_external_adapters=not args.no_external_adapters,
        steam_api_key=args.steam_api_key,
        steam_id=args.steam_id,
    )

    records = scan_launchers(config)
    index_path = render_site(records, args.output)
    print(f"Wrote {len(records)} games to {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
