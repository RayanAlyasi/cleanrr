# cleanrr logo pack

An open-source brand pack for **cleanrr**, the Telegram bot that fixes media-request issues across your Plex/Jellyfin homelab stack.

The mark is an octopus — eight arms because cleanrr juggles Sonarr, Radarr, Overseerr, qBittorrent, Plex, Jellyfin, your impatient family, and itself, all at once.

## What's in the pack

```
logo/
├── svg/                     # vector sources (edit these)
│   ├── mark-*.svg           # primary octopus, multiple color variants
│   ├── mark-small-*.svg     # simplified mark for small sizes (avatars, favicons)
│   ├── wordmark-*.svg       # text only: "cleanrr" with bold rr
│   ├── lockup-horizontal-*  # mark + wordmark side-by-side
│   └── lockup-stacked-*     # mark above wordmark
├── png/                     # rasterized exports at multiple sizes
├── avatar/                  # square avatar tiles (Telegram bot avatar etc.)
└── favicon/                 # favicon.ico + apple-touch-icon.png + logo.png
```

## Color variants

Each artwork comes in four variants:

| Suffix          | Background  | Foreground | Use when                                       |
|-----------------|-------------|------------|------------------------------------------------|
| `-on-dark`      | #111827     | #FFFFFF    | Self-contained tile for use on a light page    |
| `-on-light`     | #FFFFFF     | #111827    | Self-contained tile for use on a dark page     |
| `-mono-white`   | transparent | #FFFFFF    | Place on any dark/colored background           |
| `-mono-black`   | transparent | #111827    | Place on any light/colored background          |

The `mono-*` variants have **real cutouts** for the eyes and smile (transparent pixels), so they composite correctly over any background. The `on-*` variants have the cutouts knocked out to the tile color.

## Recommended uses

| Where                                 | File                                    |
|---------------------------------------|-----------------------------------------|
| Telegram bot avatar (BotFather)       | `avatar/avatar-v2-dark.svg` or 512 PNG  |
| GitHub repo social preview            | `png/lockup-horizontal-on-dark-1600.png`|
| GitHub README hero                    | `favicon/logo.png` or stacked lockup    |
| Website favicon                       | `favicon/favicon.ico`                   |
| iOS home screen icon                  | `favicon/apple-touch-icon.png`          |
| Docs / web header on light bg         | `svg/lockup-horizontal-on-light.svg`    |
| Tiny contexts (16-28px)               | `svg/mark-small-*.svg`                  |

## Wordmark

The wordmark is "cleanrr" with the trailing `rr` in **bold weight (700)**, the rest in regular weight (400). This is a quiet nod to the `*arr` ecosystem (Sonarr, Radarr, etc.) without making it the dominant feature.

Typeface: rendered with a system sans-serif fallback chain (`-apple-system, Inter, Segoe UI, Helvetica Neue, Arial`). The PNG exports bake the font in. If you'd prefer a specific typeface (Inter, Manrope, IBM Plex Sans Variable, etc.), open the wordmark SVG and change the `font-family` attribute.

## Re-generating

If you want different sizes, colors, or to tweak the artwork:

1. Edit `build_svgs.py` (artwork + variants)
2. Run `python3 build_svgs.py` to regenerate SVGs
3. Run `python3 build_pngs.py` to re-rasterize PNGs (needs Playwright/Chromium)
4. Run `python3 build_favicon.py` to refresh favicons (needs Pillow)

## License

Recommended: release under **CC BY 4.0** so contributors and forks can reuse the brand with attribution. Add to your repo's `LICENSE-brand.md` or similar.
