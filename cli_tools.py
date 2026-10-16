import sys
import re
import json
import urllib.request
import urllib.parse
from pathlib import Path
from collections import Counter
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3


def fetch_album_lyrics(album_dir: Path) -> None:
    """Equivalente nativo a utils/fetch_album_lyrics.sh usando LRCLIB."""
    artist = album_dir.parent.name
    album = album_dir.name
    print(f"▶ Descargando letras (.lrc) en: {album_dir}\n  Artista: {artist} | Álbum: {album}")

    for mp3 in sorted(album_dir.glob("*.mp3")):
        lrc_file = mp3.with_suffix(".lrc")
        if lrc_file.exists():
            print(f"– Saltando '{mp3.stem}' (ya tiene .lrc)")
            continue

        for title_try in (mp3.stem, re.sub(r"\s*\([^)]*\)", "", mp3.stem)):
            params = urllib.parse.urlencode({
                "artist_name": artist, "track_name": title_try, "album_name": album
            })
            try:
                req = urllib.request.Request(
                    f"https://lrclib.net/api/get?{params}",
                    headers={"User-Agent": "jmpc-windows/1.0"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    synced = data.get("syncedLyrics")
                    if synced:
                        clean = "\n".join(
                            line for line in synced.splitlines()
                            if not re.match(r"^\[(ar|al|ti):", line)
                        )
                        lrc_file.write_text(clean, encoding="utf-8")
                        print(f"✔ Letra guardada: {lrc_file.name}")
                        break
            except Exception:
                continue
        else:
            print(f"✗ Sin letras para: {mp3.stem}")


def tag_genres_interactive(album_dir: Path) -> None:
    """Equivalente nativo a utils/tag_genres.sh."""
    for mp3 in sorted(album_dir.glob("*.mp3")):
        try:
            tags = EasyID3(mp3)
        except Exception:
            tags = EasyID3()
        title = tags.get("title", [mp3.stem])[0]
        cur_genre = tags.get("genre", ["None"])[0]
        print(f"\n🎵 Pista: {mp3.name} | Título: {title} | Género actual: {cur_genre}")
        new_genre = input("Ingresa género(s) separados por ';' (Enter para saltar): ").strip()
        if new_genre:
            tags["genre"] = [new_genre]
            tags.save(mp3)
            print(f"✅ Género actualizado a: {new_genre}")


def export_genre_counts(music_dir: Path, out_file: Path) -> None:
    """Equivalente a inspect_log.sh -> genera genre_counts.txt."""
    counts: Counter = Counter()
    for mp3 in music_dir.rglob("*.mp3"):
        try:
            tags = EasyID3(mp3)
            for raw in tags.get("genre", []):
                for g in raw.split(";"):
                    if g.strip():
                        counts[g.strip()] += 1
        except Exception:
            pass

    lines = [f"{genre} {cnt}" for genre, cnt in counts.most_common()]
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Archivo de frecuencias guardado en: {out_file}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso:")
        print("  python utils_win.py lyrics <ruta_album>")
        print("  python utils_win.py genres <ruta_album>")
        print("  python utils_win.py stats  <ruta_musica>")
        sys.exit(1)

    cmd, target = sys.argv[1], Path(sys.argv[2])
    if cmd == "lyrics":
        fetch_album_lyrics(target)
    elif cmd == "genres":
        tag_genres_interactive(target)
    elif cmd == "stats":
        export_genre_counts(target, Path("genre_counts.txt"))