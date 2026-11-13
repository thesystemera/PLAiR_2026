import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"E:\scratch\plair\reference_masters")
PREVIEWS = ROOT / "previews"
PREVIEWS.mkdir(parents=True, exist_ok=True)
AGENT = {"User-Agent": "Mozilla/5.0"}


def get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=timeout) as response:
        return response.read()


def chart(country, limit):
    url = f"https://rss.marketingtools.apple.com/api/v2/{country}/music/most-played/{limit}/songs.json"
    return json.loads(get(url))["feed"]["results"]


def main(countries, limit, cap):
    index_path = ROOT / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    fetched = 0
    for country in countries:
        try:
            songs = chart(country, limit)
        except Exception as error:
            print(f"chart {country}: {error}", flush=True)
            continue
        for song in songs:
            if fetched >= cap:
                break
            track_id = song["id"]
            if track_id in index:
                continue
            target = PREVIEWS / f"{track_id}.m4a"
            info = {"title": song["name"], "artist": song["artistName"], "country": country,
                    "release": song.get("releaseDate"), "genres": [g["name"] for g in song.get("genres", [])],
                    "file": target.name}
            if target.exists() and target.stat().st_size > 100000:
                index[track_id] = info
                continue
            try:
                found = json.loads(get(f"https://itunes.apple.com/lookup?id={track_id}&country={country}"))["results"]
                preview = found[0].get("previewUrl") if found else None
                if not preview:
                    continue
                target.write_bytes(get(preview, timeout=60))
                index[track_id] = info
                fetched += 1
                if fetched % 10 == 0:
                    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
                time.sleep(0.5)
            except Exception as error:
                print(f"{track_id}: {error}", flush=True)
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"fetched {fetched}, index now {len(index)} songs", flush=True)


if __name__ == "__main__":
    main(sys.argv[1].split(","), int(sys.argv[2]), int(sys.argv[3]))
