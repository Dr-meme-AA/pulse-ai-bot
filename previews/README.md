# 404 chain edition previews

Two independent visual concepts based on the existing 404 identity character.

- `robinhood/`: **Off the Record** — green, rounded controls, open composition, illustrative signal waveform.
- `binance/`: **Black Vault** — gold, angular archive layout, interactive dossier, fictional vault terminal.

These are preview-only concepts. The root production website, hosting settings, DNS, and existing Solana design are unchanged. Both concepts explicitly disclose their independent status and use no exchange logos, invented market data, contract addresses, or inactive social links.

Shared CSS and JavaScript live in `shared/`. Preview Workers map `assets/base.css` and `assets/effects.js` to these files, `assets/theme.css` to the edition CSS, and the appropriate hero image to `assets/hood-green.webp` or `assets/hood-gold.webp`. Workers also provide an isolated `/__review/mobile` iframe for responsive review, with a `width` query parameter for 320–768px widths.

Interactions are entirely local: identity scan, alias rotation, terminal commands, dossier tabs, pause controls. Canvas effects stop when the tab is hidden and respect reduced-motion preferences. No wallet, trading, or personal-data collection is implemented.

The generated PNG sources were exported as WebP without altering their artwork. The final WebP assets are committed with each preview.

Build a self-contained Worker with `python3 previews/build-worker.py robinhood /tmp/404-robinhood-worker.mjs` (or `binance`). Upload the built module as `worker.js` with `main_module: "worker.js"` and compatibility date `2026-09-01`. No production routes or custom domains are required.
