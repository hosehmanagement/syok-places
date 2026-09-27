#!/usr/bin/env python3
"""
Syok AI place packs — builds small "area files" of shops, food places and sights for 100 cities.

Sources (both free and legal to store and share):
  * OpenStreetMap via Overpass (c) OpenStreetMap contributors, ODbL 1.0
  * Wikivoyage "See"/"Eat"/"Drink"/"Do" listings, CC BY-SA 4.0 (editors' recommendations)

Usage:
  python harvest.py part N M   -> harvests cities N, N+M, N+2M ... into out/part-N.json
  python harvest.py retry F    -> harvests ONLY the cities listed in F (last run's missing.json) into out/part-retry.json
  python harvest.py merge      -> turns every out/part-*.json into site/ (index.json, t/<tile>.json, c/<city>.json)
                                  and writes site/missing.json = cities that failed this time (retried 6 hours later)

Only the Python standard library is used.
"""
import glob, json, math, os, re, sys, time, urllib.parse, urllib.request

UA = "SyokAI-PlacePacks/1.0 (+https://syok-ai.com; support@syok-ai.com)"
TILE = 0.02  # degrees ~ 2.2 km. The worker computes the same key: floor(lat/0.02)_floor(lon/0.02)
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]

exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cities.py")).read())  # defines C


def http(url, data=None, timeout=300):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def slug(s):
    s = s.lower()
    for a, b in (("ü", "u"), ("ã", "a"), ("ú", "u"), ("é", "e"), ("'", "")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def tile_key(lat, lon):
    return f"{math.floor(lat / TILE)}_{math.floor(lon / TILE)}"


def geocode(q):
    u = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=" + urllib.parse.quote(q)
    d = json.loads(http(u, timeout=60))
    time.sleep(1.2)  # Nominatim policy: max 1 request / second
    if not d:
        raise RuntimeError("not found: " + q)
    return float(d[0]["lat"]), float(d[0]["lon"])


def overpass(q):
    last = None
    for attempt in range(6):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            return json.loads(http(url, ("data=" + urllib.parse.quote(q)).encode(), timeout=900))
        except Exception as e:  # 429 / 504 / timeouts -> wait and try the next mirror
            last = e
            print("  overpass retry", attempt + 1, url, e, flush=True)
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"overpass failed: {last}")


def osm_query(s, w, n, e):
    b = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
    return (
        f"[out:json][timeout:900][maxsize:536870912];("
        f'nwr["name"]["shop"]({b});'
        f'nwr["name"]["amenity"~"^(restaurant|cafe|fast_food|food_court|bar|pub|ice_cream|pharmacy|marketplace)$"]({b});'
        f'nwr["name"]["tourism"~"^(attraction|museum|gallery|viewpoint|zoo|theme_park|aquarium)$"]({b});'
        f'nwr["name"]["historic"~"^(castle|monument|ruins|archaeological_site|fort|palace|city_gate)$"]({b});'
        f'nwr["name"]["leisure"~"^(park|garden|water_park)$"]["wikidata"]({b});'
        f");out tags center;"
    )


LATIN = re.compile(r"^[\x00-ɏ\s]+$")


def clean_name(t):
    n = (t.get("name") or "").strip()
    en = (t.get("name:en") or "").strip()
    if en and en.lower() != n.lower() and not LATIN.match(n):
        return f"{en} ({n})"
    return n


def join(parts):
    out, seen = [], set()
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return ", ".join(out)


def osm_records(data):
    recs = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        name = clean_name(t)
        if lat is None or lon is None or not name:
            continue
        if "shop" in t:
            kind, g = t["shop"], "s"
        elif t.get("amenity") in ("pharmacy", "marketplace"):
            kind, g = t["amenity"], "s"
        elif "amenity" in t:
            kind, g = t["amenity"], "f"
        elif "tourism" in t:
            kind, g = t["tourism"], "a"
        elif "historic" in t:
            kind, g = t["historic"], "a"
        else:
            kind, g = t.get("leisure", "place"), "a"
        street = f'{t["addr:housenumber"]} {t["addr:street"]}' if t.get("addr:housenumber") and t.get("addr:street") else t.get("addr:street")
        addr = join([t.get("addr:unit") and f'Unit {t["addr:unit"]}', street, t.get("addr:suburb") or t.get("addr:neighbourhood"), t.get("addr:city"), t.get("addr:postcode")])
        kind = kind.split(";")[0]
        recs.append([name[:120], kind.replace("_", " ")[:40], round(lat, 5), round(lon, 5), addr[:200], (t.get("opening_hours") or "")[:160],
                     (t.get("phone") or t.get("contact:phone") or "")[:40], t.get("diet:halal", ""), (t.get("cuisine") or "").replace("_", " ")[:60], g, ""])
    return recs


# ---------------- Wikivoyage ----------------
WV = "https://en.wikivoyage.org/w/api.php"


def wv(params):
    params = dict(params, format="json", formatversion="2")
    time.sleep(0.5)
    return json.loads(http(WV + "?" + urllib.parse.urlencode(params), timeout=120))


def templates(text):
    """Top-level {{...}} blocks (handles nesting)."""
    out, i, n = [], 0, len(text)
    while True:
        i = text.find("{{", i)
        if i < 0:
            return out
        depth, j = 0, i
        while j < n:
            if text.startswith("{{", j):
                depth += 1
                j += 2
            elif text.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        out.append(text[i + 2 : j - 2])
        i = j


def split_params(body):
    parts, depth, cur = [], 0, ""
    k = 0
    while k < len(body):
        ch = body[k]
        two = body[k : k + 2]
        if two in ("{{", "[["):
            depth += 1
            cur += two
            k += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            cur += two
            k += 2
            continue
        if ch == "|" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        k += 1
    parts.append(cur)
    return parts


def wikiclean(s):
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*>.*?</ref>|<[^>]+>", "", s, flags=re.S)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\]", "", s)
    s = s.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", s).strip()


def listings(text):
    out = []
    for body in templates(text):
        parts = split_params(body)
        head = parts[0].strip().lower()
        p = {}
        for x in parts[1:]:
            if "=" in x:
                k, v = x.split("=", 1)
                p[k.strip().lower()] = v.strip()
        typ = head if head in ("see", "do", "eat", "drink") else (p.get("type", "").lower() if head in ("listing", "marker") else "")
        if typ not in ("see", "do", "eat", "drink"):
            continue
        name = wikiclean(p.get("name", ""))
        if not name:
            continue
        try:
            lat = round(float(p.get("lat", "")), 5)
            lon = round(float(p.get("long", "")), 5)
        except ValueError:
            lat = lon = None
        blurb = wikiclean(p.get("content", "") or p.get("description", ""))
        if len(blurb) > 240:
            blurb = blurb[:237].rsplit(" ", 1)[0] + "…"
        out.append({"n": name[:120], "k": typ, "la": lat, "lo": lon, "a": wikiclean(p.get("address", ""))[:200],
                    "h": wikiclean(p.get("hours", ""))[:160], "pr": wikiclean(p.get("price", ""))[:80], "b": blurb})
    return out


def wikivoyage_city(title):
    titles = [title]
    try:
        ap = wv({"action": "query", "list": "allpages", "apprefix": title + "/", "apnamespace": 0, "aplimit": "max"})  # big cities have 50+ districts
        titles += [x["title"] for x in ap.get("query", {}).get("allpages", [])]
    except Exception as e:
        print("  wv subpages failed", e, flush=True)
    found = []
    for i in range(0, len(titles), 50):
        try:
            d = wv({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "redirects": 1, "titles": "|".join(titles[i : i + 50])})
        except Exception as e:
            print("  wv fetch failed", e, flush=True)
            continue
        for page in d.get("query", {}).get("pages", []):
            revs = page.get("revisions") or []
            if revs:
                found += listings(revs[0]["slots"]["main"]["content"])
    seen, uniq = set(), []
    for x in found:
        key = (x["n"].lower(), x["k"])
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    return uniq


def harvest_city(geo, title, rkm):
    lat, lon = geocode(geo)
    dlat = rkm / 111.0
    dlon = rkm / (111.0 * math.cos(math.radians(lat)))
    t0 = time.time()
    # 2 x 2 pieces keep each Overpass answer small enough for the public servers' memory limit.
    recs, seen = [], set()
    for qs, qw in ((lat - dlat, lon - dlon), (lat - dlat, lon), (lat, lon - dlon), (lat, lon)):
        for r in osm_records(overpass(osm_query(qs, qw, qs + dlat, qw + dlon))):
            k = (r[0].lower(), round(r[2], 4), round(r[3], 4))
            if k not in seen:
                seen.add(k)
                recs.append(r)
        time.sleep(3)
    wvl = wikivoyage_city(title)
    print(f"  {len(recs)} OSM places, {len(wvl)} Wikivoyage listings, {time.time() - t0:.0f}s", flush=True)
    return {"s": slug(title), "n": title, "geo": geo, "cc": geo.split(",")[-1].strip(), "la": round(lat, 4), "lo": round(lon, 4), "r": rkm, "osm": recs, "wv": wvl}


# GitHub stops a job after 6 hours. Stop starting new cities well before that, so the ones already done are always kept.
TIME_LIMIT_S = int(os.environ.get("HARVEST_TIME_LIMIT_S", str(300 * 60)))


def save(res, n):
    """Written after EVERY city, so a slow run or a crash never loses the cities already finished."""
    tmp = f"out/part-{n}.json.tmp"
    json.dump(res, open(tmp, "w"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, f"out/part-{n}.json")


def part(n, m):
    os.makedirs("out", exist_ok=True)
    res, t0, skipped = [], time.time(), []
    for i in range(n, len(C), m):
        geo, title, rkm = C[i]
        if time.time() - t0 > TIME_LIMIT_S:
            skipped.append(geo)
            continue
        print(f"[{i + 1}/{len(C)}] {geo}", flush=True)
        try:
            res.append(harvest_city(geo, title, rkm))
            save(res, n)
        except Exception as e:
            print("  FAILED", e, flush=True)
        time.sleep(10)
    save(res, n)
    if skipped:
        print(f"Out of time - skipped {len(skipped)} cities (they keep last month's data): {', '.join(skipped)}", flush=True)


def retry(path):
    """Harvest only the cities that failed last time. Every other city keeps its files from the data branch."""
    try:
        want = set(json.load(open(path)).get("geos", []))
    except (OSError, ValueError):
        want = set()
    if not want and os.path.exists("index.json"):  # no missing.json yet (older data): retry every city not in the index
        have = {c["s"] for c in json.load(open("index.json")).get("cities", [])}
        want = {geo for geo, title, _ in C if slug(title) not in have}
    os.makedirs("out", exist_ok=True)
    res = []
    for geo, title, rkm in C:
        if geo not in want:
            continue
        print(f"[retry] {geo}", flush=True)
        try:
            res.append(harvest_city(geo, title, rkm))
        except Exception as e:
            print("  FAILED again", e, flush=True)
        save(res, "retry")
        time.sleep(10)
    save(res, "retry")


RETRY_AFTER_S = 6 * 3600
MAX_RETRIES = 5  # after 5 failed retries, the city simply waits for next month's full run


def write_missing(fresh, prev="prev", now=None):
    """missing.json tells the hourly checker which cities to retry, and when (6 hours after this run)."""
    now = int(now if now is not None else time.time())
    try:
        old = json.load(open(f"{prev}/missing.json"))
    except (OSError, ValueError):
        old = {}
    retrying = os.environ.get("MODE") == "retry"
    # Full run: every city not downloaded. Retry run: only the ones retried that failed AGAIN (the rest are fine).
    asked = set(old.get("geos", [])) if retrying else None
    geos = [geo for geo, title, _ in C if slug(title) not in fresh and (asked is None or geo in asked)]
    attempt = old.get("attempt", 0) + 1 if retrying else 0
    info = {"geos": geos, "failed_at": now, "retry_after": now + RETRY_AFTER_S, "attempt": attempt, "give_up": attempt >= MAX_RETRIES}
    json.dump(info, open("site/missing.json", "w"), ensure_ascii=False, indent=1)
    return info


def trim(r):
    while r and r[-1] in ("", None):
        r = r[:-1]
    return r


def merge():
    cities = []
    for f in sorted(glob.glob("out/**/part-*.json", recursive=True)):
        cities += json.load(open(f))
    os.makedirs("site/t", exist_ok=True)
    os.makedirs("site/c", exist_ok=True)
    tiles, index, total = {}, [], 0
    for c in cities:
        keys = set()
        for r in c["osm"]:
            k = tile_key(r[2], r[3])
            tiles.setdefault(k, {})[(r[0].lower(), round(r[2], 3), round(r[3], 3))] = r
            keys.add(k)
        for x in c["wv"]:  # editors' picks with a map pin also go into the area files, flagged "r" (recommended)
            if x["la"] is None:
                continue
            k = tile_key(x["la"], x["lo"])
            t = tiles.setdefault(k, {})
            twin = next((r for (nm, _, _), r in t.items() if nm == x["n"].lower()), None)
            if twin:
                twin[10] = x["b"] or "Recommended by Wikivoyage"
                if not twin[4] and x["a"]:
                    twin[4] = x["a"]
                if not twin[5] and x["h"]:
                    twin[5] = x["h"]
            else:
                t[(x["n"].lower(), round(x["la"], 3), round(x["lo"], 3))] = [x["n"], {"see": "sight", "do": "activity", "eat": "restaurant", "drink": "bar"}[x["k"]], x["la"], x["lo"], x["a"], x["h"], "", "", "", "r", x["b"] or "Recommended by Wikivoyage"]
            keys.add(k)
        see = [x for x in c["wv"] if x["k"] in ("see", "do")][:80]
        eat = [x for x in c["wv"] if x["k"] in ("eat", "drink")][:80]
        json.dump({"s": c["s"], "n": c["n"], "cc": c["cc"], "see": see, "eat": eat}, open(f"site/c/{c['s']}.json", "w"), ensure_ascii=False, separators=(",", ":"))
        index.append({"s": c["s"], "n": c["n"], "cc": c["cc"], "la": c["la"], "lo": c["lo"], "r": c["r"], "t": sorted(keys), "places": len(c["osm"]), "picks": len(c["wv"])})
        total += len(c["osm"])
    for k, t in tiles.items():
        json.dump({"p": [trim(r) for r in t.values()]}, open(f"site/t/{k}.json", "w"), ensure_ascii=False, separators=(",", ":"))
    # Any city that failed or ran out of time this month keeps LAST month's files (from the previous "data" branch in prev/).
    fresh = {c["s"] for c in index}
    kept = carry_forward(index, set(tiles))
    json.dump({"v": 1, "built": time.strftime("%Y-%m-%d"), "tile": TILE, "cities": index,
               "credit": "Places (c) OpenStreetMap contributors (ODbL); picks from Wikivoyage (CC BY-SA)"},
              open("site/index.json", "w"), ensure_ascii=False, separators=(",", ":"))
    miss = write_missing(fresh)
    print(f"{len(index)} cities ({kept} kept from before), {total} new places, {len(tiles)} new area files")
    if miss["geos"]:
        print(f"Not downloaded this time: {', '.join(miss['geos'])} -> " + ("no more retries until next month" if miss["give_up"] else "retry in 6 hours"))


def carry_forward(index, new_tiles, prev="prev"):
    try:
        old = json.load(open(f"{prev}/index.json"))
    except (OSError, ValueError):
        return 0
    have = {c["s"] for c in index}
    kept = 0
    for c in old.get("cities", []):
        if c["s"] in have:
            continue
        if os.path.exists(f"{prev}/c/{c['s']}.json"):
            os.replace(f"{prev}/c/{c['s']}.json", f"site/c/{c['s']}.json")
        for k in c.get("t", []):
            if k not in new_tiles and os.path.exists(f"{prev}/t/{k}.json") and not os.path.exists(f"site/t/{k}.json"):
                os.replace(f"{prev}/t/{k}.json", f"site/t/{k}.json")
        # A retry run only refreshes the failed cities; everyone else is still this month's data, not "stale".
        index.append(c if os.environ.get("MODE") == "retry" else dict(c, stale=old.get("built", "")))
        kept += 1
    return kept


if __name__ == "__main__":
    if sys.argv[1] == "part":
        part(int(sys.argv[2]), int(sys.argv[3]))
    elif sys.argv[1] == "retry":
        retry(sys.argv[2])
    else:
        merge()
