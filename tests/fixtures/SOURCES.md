# Fixture provenance

These are public pages, saved whole on 2026-10-27 with `curl -L -A "IRIS-SIG-08-Harvester/1.0 (bot)"`. Requests were 2 s apart. The pages are unedited unless a row below says otherwise.

| File | URL | Notes |
|---|---|---|
| sachsen_dresden_listing.html | https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen?format=Bauleitplan | Current (AKTUELLE, the default): 8 cards, 8 map markers |
| sachsen_dresden_listing_beendet.html | https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen?format=Bauleitplan&status=BEENDETE | Finished: 10 cards (page 1 of "1 - 10 von 52"), 52 map markers |
| sachsen_leipzig_listing.html | https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen?format=Bauleitplan | Current: 2 cards, 2 map markers |
| sachsen_leipzig_listing_beendet.html | https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen?format=Bauleitplan&status=BEENDETE | Finished: "1 - 10 von 21", 21 map markers |
| sachsen_dresden_detail.html | https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/1069133 | "Gewerbecampus Am Werk" |
| sachsen_leipzig_detail.html | https://buergerbeteiligung.sachsen.de/portal/leipzig/beteiligung/themen/1069513 | |
| leipzig_stadt_listing_im_verfahren.html | https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene?tx_lepurpose%5Bfilter%5D%5B89%5D%5B%5D=252 | "im Verfahren" only: 39 rows, no next-page button |
| leipzig_stadt_listing.html | https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene | 50 rows; "load more" button has `data-next="2"` |
| leipzig_stadt_listing_page2.html | same URL + `?tx_lepurpose%5Bfilter%5D%5Bpage%5D=2` | 50 rows; `data-next="3"` |
| leipzig_stadt_detail.html | https://www.leipzig.de/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene/detail/projekt/bebauungsplan-nr-10-gewerbe-und-industriepark-leipzig-nordost-3-aenderung | Thema "Gewerbe", status "rechtskräftig" |
