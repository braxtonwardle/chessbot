"""
render_piece_pngs.py

Rasterizes every downloaded piece SVG (piece_svgs/<set>/<code>.svg) to a
PNG with alpha (piece_pngs/<set>/<code>.png) at a fixed tile resolution,
using resvg_py -- cairosvg needs a native cairo library that isn't
installed on Windows here, and resvg_py ships a working prebuilt wheel
with no such dependency.

Rendered once and reused for every synthetic board composited from that
piece, rather than re-rendering the SVG per board (which would be the
same work repeated thousands of times).

Usage:
    python render_piece_pngs.py
"""

import os

import resvg_py

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, "piece_svgs")
PNG_DIR = os.path.join(HERE, "piece_pngs")

TILE_PX = 150  # rendered larger than the eventual 32x32 training tile
# so compositing/resizing downstream doesn't upscale a blurry source.


def render_all(tile_px=TILE_PX):
    os.makedirs(PNG_DIR, exist_ok=True)
    ok, failed = 0, []

    for piece_set in sorted(os.listdir(SVG_DIR)):
        svg_set_dir = os.path.join(SVG_DIR, piece_set)

        if not os.path.isdir(svg_set_dir):
            continue

        png_set_dir = os.path.join(PNG_DIR, piece_set)
        os.makedirs(png_set_dir, exist_ok=True)

        for svg_name in os.listdir(svg_set_dir):
            if not svg_name.endswith(".svg"):
                continue

            svg_path = os.path.join(svg_set_dir, svg_name)
            png_path = os.path.join(png_set_dir, svg_name.replace(".svg", ".png"))

            if os.path.exists(png_path):
                ok += 1
                continue

            try:
                # dpi must be explicit -- some sets (merida, etc.) size
                # their SVGs in mm rather than plain units, and resvg
                # can't resolve a pixel width/height override without a
                # DPI to convert through (dpi=0 means "use SVG default",
                # which fails the same way).
                png_bytes = resvg_py.svg_to_bytes(
                    svg_path=svg_path, width=tile_px, height=tile_px, dpi=96.0
                )
                with open(png_path, "wb") as f:
                    f.write(bytes(png_bytes))
                ok += 1
            except Exception as e:
                failed.append((piece_set, svg_name, str(e)))
                print(f"FAILED {piece_set}/{svg_name}: {e}")

    print(f"\n{ok} rendered, {len(failed)} failed")

    # A set is only usable for training if all 12 pieces rendered --
    # report which sets are incomplete so generate_synthetic_boards.py
    # can skip them cleanly rather than fail mid-composite.
    complete_sets = []
    for piece_set in sorted(os.listdir(PNG_DIR)):
        set_dir = os.path.join(PNG_DIR, piece_set)
        pngs = [f for f in os.listdir(set_dir) if f.endswith(".png")]
        if len(pngs) == 12:
            complete_sets.append(piece_set)
        else:
            print(f"INCOMPLETE SET (skip): {piece_set} has {len(pngs)}/12 pieces")

    print(f"\n{len(complete_sets)} complete piece sets ready for training")
    return complete_sets


if __name__ == "__main__":
    render_all()
