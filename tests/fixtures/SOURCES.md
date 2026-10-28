# Fixture provenance

These are public pages, saved whole on 2026-10-27 with `curl -L -A "IRIS-SIG-08-Harvester/1.0 (bot)"`. Requests were 2 s apart, and the pages are unedited. There is one real page per markup the code parses. Edge cases (empty listing, malformed markers, count mismatch, HTTP errors) are minimal HTML snippets inside the tests.

| File | URL | Notes |
|---|---|---|
| sachsen_dresden_listing_beendet.html | https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen?format=Bauleitplan&status=BEENDETE | Finished: 52 map markers (5 industrial titles); 10 cards with "1 - 10 von 52" |
| sachsen_dresden_detail.html | https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/1069133 | "Gewerbecampus Am Werk"; `li.nbp-statistik--laufzeit` = "Zeitraum 12.10.2026 bis 13.11.2026" |

## Checked on 2026-10-27, not kept

These pages were checked the same way but are not stored, because they use the same markup as a fixture above or aren't parsed at all.

| URL | Finding |
|---|---|
| https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen?format=Bauleitplan | Current (default AKTUELLE): 8 cards, 8 markers, no paginator |
| https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen?format=Bauleitplan | Current: 2 cards, 2 markers |
| https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen?format=Bauleitplan&status=BEENDETE | Finished: 21 markers, "1 - 10 von 21" |
| https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen/1069513 | Same detail markup as Dresden ("Zeitraum 27.10.2026 bis 26.11.2026") |
