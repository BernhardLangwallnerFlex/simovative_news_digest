# TODO — Parked Sources

Sources from the NEGZ Verwaltungsdigitalisierung request (2026-06) that were
intentionally deferred. Not forgotten — revisit when capacity allows.

## Parked

- **Digitale Verwaltung (BMI / CIO Bund)** — `https://www.digitale-verwaltung.de/`
  Government GSB CMS behind a cookie-check wall; the RSS XML endpoint is not
  resolvable via plain HTTP. Needs Playwright cookie-wall handling (the existing
  `dismiss_cookies` helper may suffice — try registering the "Meldungen" node as
  a crawl domain and confirm the cookie wall is dismissed). Article listing:
  `https://www.digitale-verwaltung.de/Webs/DV/DE/aktuelles-service/aktuelles-service-node.html`.

- **Innovative Verwaltung** — Springer Professional
  (`https://www.springerprofessional.de/innovative-verwaltung/5030910`).
  Teaser paywall; no public RSS feed found. Only title+abstract are public.
  Revisit only if a teaser-level feed is confirmed.

- **Tagesspiegel Background (Digitalisierung & KI)** —
  `https://background.tagesspiegel.de/digitalisierung-und-ki`. Full paywall +
  Cloudflare 403; ~€139–199/license/month. The free *Wissenschaft* RSS feed is
  already in `RSS_FEEDS`.

- **VITAKO aktuell (print magazine PDFs)** — `https://vitako.de/archive/category/aktuelles/vitako-aktuell`.
  Quarterly magazine published as PDF (distinct from the Vitako podcast, which is
  already ingested). Optional future text source; would need PDF text extraction.
