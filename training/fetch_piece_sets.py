"""
fetch_piece_sets.py

Downloads piece SVGs for every lichess piece set (public/piece/<set>/ in
the lila repo) except "disguised" -- that set exists specifically to
hide piece identity from the *viewer*, which makes it useless (actively
counterproductive) as recognition training data.

Usage:
    python fetch_piece_sets.py
Saves to piece_svgs/<set>/<code>.svg, e.g. piece_svgs/cburnett/wK.svg
"""

import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "piece_svgs")

PIECE_SETS = [
    "alpha", "anarcandy", "caliente", "california", "cardinal", "cburnett",
    "celtic", "chess7", "chessnut", "companion", "cooke", "dubrovny",
    "fantasy", "firi", "fresca", "gioco", "governor", "horsey", "icpieces",
    "kiwen-suwi", "kosal", "leipzig", "letter", "maestro", "merida",
    "monarchy", "mono", "mpchess", "papercut", "pirouetti", "pixel",
    "reillycraig", "rhosgfx", "riohacha", "shahi-ivory-brown", "shapes",
    "spatial", "staunty", "tatiana", "totoy", "xkcd",
]

PIECE_CODES = [
    color + kind
    for color in ("w", "b")
    for kind in ("P", "N", "B", "R", "Q", "K")
]

BASE_URL = "https://lichess1.org/assets/piece"


def fetch_all(piece_sets=PIECE_SETS):
    os.makedirs(OUT_DIR, exist_ok=True)
    ok, failed = 0, []

    for piece_set in piece_sets:
        set_dir = os.path.join(OUT_DIR, piece_set)
        os.makedirs(set_dir, exist_ok=True)

        for code in PIECE_CODES:
            dest = os.path.join(set_dir, f"{code}.svg")

            if os.path.exists(dest):
                ok += 1
                continue

            url = f"{BASE_URL}/{piece_set}/{code}.svg"

            try:
                urllib.request.urlretrieve(url, dest)
                ok += 1
            except Exception as e:
                failed.append((piece_set, code, str(e)))
                print(f"FAILED {piece_set}/{code}: {e}")

            time.sleep(0.05)  # be polite to lichess's CDN

    print(f"\n{ok} fetched, {len(failed)} failed")

    if failed:
        print("Failed sets (will be skipped during rendering):")
        for piece_set, code, err in failed:
            print(f"  {piece_set}/{code}: {err}")

    return failed


if __name__ == "__main__":
    fetch_all()
