# RigBrain

[rigbrain.io](https://rigbrain.io) — Dealer-facing permit lead-gen SaaS for heavy-equipment dealers.

Scrapes commercial building permits across the top 10 US metros (Austin, Houston, Dallas, Phoenix, Atlanta, Charlotte, Nashville, Denver, Tampa, Orlando), scores for equipment-purchase intent, delivers contractor name + phone + project details to subscribing dealers weekly. $299/mo · Founding-dealer cohort open (first 10 nationwide).

This repo hosts the live marketing + dashboard site (GitHub Pages).

## Goal

Land the first paying Austin dealer ($299/mo) by **2026-06-16** — 30 days after the earliest realistic cold-send date of 2026-05-17 (gated by Mailwarm's 14-day inbox warmup).

## Next Actions

1. **Sign up for Mailwarm** ($30/mo) — 14-day warmup is the long pole on launch.
2. **Sign up for Instantly.ai** ($37/mo) — load dealer list + 5-email sequence.
3. **Apollo enrichment** to fill the 16 missing dealer names.
4. **Verify DNS** (SPF/DKIM/DMARC) via mxtoolbox.
5. **2026-05-17** — fire email 1 to first 30 dealers.

Operational status, campaign artifacts, and dealer list live in the founder's working folder, not this repo.

## Site pages

- `index.html` — marketing landing
- `dealers.html` — dealer-side pitch
- `permit-dashboard.html` — sample dashboard view
- `rigbrain-signup.html` — pilot signup
- `scanner.html` — permit scanner demo
- `book.html` — call booking

Contact: nick@rigbrain.io
