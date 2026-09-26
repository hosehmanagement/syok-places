# Syok AI place packs

Small "area files" of shops, restaurants, cafés, markets, pharmacies, museums and sights for 100 major cities,
plus Wikivoyage editors' food and sightseeing picks. Syok AI reads these instead of doing a slow web search.

* `index.json` – the cities, their centre, radius and area-file list
* `t/<lat>_<lon>.json` – one file per ~2.2 km square (key = floor(lat/0.02)_floor(lon/0.02))
* `c/<city>.json` – Wikivoyage "See / Do" and "Eat / Drink" picks for the city

Rebuilt monthly by `.github/workflows/harvest.yml` (branch `main` holds the code, branch `data` holds the files).

## Credits and licences
* Place data © OpenStreetMap contributors, available under the Open Database License (ODbL 1.0) – https://www.openstreetmap.org/copyright
* Recommendations from Wikivoyage (https://en.wikivoyage.org), available under CC BY-SA 4.0
This compiled database is shared under the same terms (ODbL for the place data, CC BY-SA 4.0 for the Wikivoyage text).
