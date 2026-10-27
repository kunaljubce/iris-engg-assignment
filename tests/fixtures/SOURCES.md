# Fixture provenance

These are public pages, saved whole on 2026-10-27 with `curl -L -A "IRIS-SIG-08-Harvester/1.0 (bot)"`. Requests were 2 s apart, and the pages are unedited. There is one real page per markup the code parses. Edge cases (empty listing, malformed markers, next-page button, count mismatch, HTTP errors) are minimal HTML snippets inside the tests.

| File | URL | Notes |
|---|---|---|
| sachsen_dresden_listing_beendet.html | https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen?format=Bauleitplan&status=BEENDETE | Finished: 52 map markers (5 industrial titles); 10 cards with "1 - 10 von 52" |
| sachsen_dresden_detail.html | https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/1069133 | "Gewerbecampus Am Werk"; `li.nbp-statistik--laufzeit` = "Zeitraum 12.10.2026 bis 13.11.2026" |
| leipzig_stadt_listing_im_verfahren.html | https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene?tx_lepurpose%5Bfilter%5D%5B89%5D%5B%5D=252 | "im Verfahren" only: 39 rows (10 industrial), no next-page button |

## Checked on 2026-10-27, not kept

These pages were checked the same way but are not stored, because they use the same markup as a fixture above or aren't parsed at all.

| URL | Finding |
|---|---|
| https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen?format=Bauleitplan | Current (default AKTUELLE): 8 cards, 8 markers, no paginator |
| https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen?format=Bauleitplan | Current: 2 cards, 2 markers |
| https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen?format=Bauleitplan&status=BEENDETE | Finished: 21 markers, "1 - 10 von 21" |
| https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen/1069513 | Same detail markup as Dresden ("Zeitraum 27.10.2026 bis 26.11.2026") |
| https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene | Unfiltered: 50 rows per page; next-page button `<button class="btn btn-secondary start-ajax-pagination" ... data-next="2" ...>`; page 2 via `?tx_lepurpose%5Bfilter%5D%5Bpage%5D=2` has `data-next="3"` |
| https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene/detail/projekt/bebauungsplan-nr-10-gewerbe-und-industriepark-leipzig-nordost-3-aenderung | Detail page shows only district, Thema, status and plan PDFs: no dates, no coordinates |
