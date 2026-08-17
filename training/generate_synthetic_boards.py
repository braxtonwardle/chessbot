"""
generate_synthetic_boards.py

Composites random positions across every rendered piece set into full
board PNGs, using the exact naming convention chessimg2pos's own
generate_tiles.py expects: "<rank8>-<rank7>-...-<rank1>.png", each rank
a literal 8-character string ("1" per empty square).

Usage:
    python generate_synthetic_boards.py [boards_per_set]
"""

import io
import os
import random
import sys

from PIL import Image, ImageEnhance, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
PNG_DIR = os.path.join(HERE, "piece_pngs")
OUT_DIR = os.path.join(HERE, "synthetic_boards")

SQUARE_PX = 32
BOARD_PX = SQUARE_PX * 8
PIECE_FRACTION = 0.85  # piece occupies this fraction of its square

PIECE_CODES = [
    color + kind
    for color in ("w", "b")
    for kind in ("P", "N", "B", "R", "Q", "K")
]

# FEN uses a single character per square (uppercase = white, lowercase =
# black); asset filenames use lichess's two-character "wK"/"bK" style.
# The board *layout* (and the filename encoding it, which
# generate_tiles.py parses one character per square) must be FEN chars
# -- only the piece-PNG lookup uses the two-character asset code.
FEN_CHAR = {code: (code[1] if code[0] == "w" else code[1].lower()) for code in PIECE_CODES}

EMPTY_PROBABILITY = 0.55  # rough fraction of empty squares in a mid-game board

# A handful of light/dark square color pairs. chessimg2pos grayscales
# tiles before classifying, so hue matters less than having *some*
# spread in contrast/lightness to avoid overfitting to one exact pair.
BOARD_THEMES = [
    ((240, 217, 181), (181, 136, 99)),   # classic brown
    ((238, 238, 210), (118, 150, 86)),   # classic green
    ((234, 233, 210), (75, 115, 153)),   # blue
    ((220, 220, 220), (100, 100, 100)),  # gray
    ((255, 255, 255), (0, 0, 0)),        # max contrast
    ((222, 227, 230), (140, 162, 173)),  # low-contrast pastel
]


def _load_piece_set(piece_set):
    pieces = {}
    for code in PIECE_CODES:
        path = os.path.join(PNG_DIR, piece_set, f"{code}.png")
        img = Image.open(path).convert("RGBA")
        size = int(SQUARE_PX * PIECE_FRACTION)
        pieces[code] = img.resize((size, size), Image.LANCZOS)
    return pieces


def _random_board_layout():
    """64 codes, '1' or one of PIECE_CODES, rank8 first (row-major)."""
    layout = []
    for _ in range(64):
        if random.random() < EMPTY_PROBABILITY:
            layout.append("1")
        else:
            layout.append(random.choice(PIECE_CODES))
    return layout


def _composite_board(layout, pieces, light, dark):
    canvas = Image.new("RGB", (BOARD_PX, BOARD_PX))

    for row in range(8):
        for col in range(8):
            square_color = light if (row + col) % 2 == 0 else dark
            x0, y0 = col * SQUARE_PX, row * SQUARE_PX
            canvas.paste(square_color, (x0, y0, x0 + SQUARE_PX, y0 + SQUARE_PX))

            code = layout[row * 8 + col]
            if code == "1":
                continue

            piece_img = pieces[code]
            offset = (SQUARE_PX - piece_img.width) // 2
            canvas.paste(piece_img, (x0 + offset, y0 + offset), piece_img)

    return canvas


def _augment(canvas):
    """Light, random degradation so the model isn't only ever shown
    pixel-perfect vector renders -- real screenshots get resized,
    JPEG-compressed, and slightly blurred along the way."""

    board = canvas.resize((BOARD_PX * 4, BOARD_PX * 4), Image.LANCZOS)

    if random.random() < 0.3:
        board = board.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 1.2)))

    if random.random() < 0.5:
        board = ImageEnhance.Brightness(board).enhance(random.uniform(0.85, 1.15))
        board = ImageEnhance.Contrast(board).enhance(random.uniform(0.85, 1.15))

    if random.random() < 0.5:
        buffer = io.BytesIO()
        board.save(buffer, format="JPEG", quality=random.randint(55, 95))
        buffer.seek(0)
        board = Image.open(buffer).convert("RGB")

    return board


def _filename_for(layout):
    fen_chars = [FEN_CHAR.get(code, code) for code in layout]
    ranks = ["".join(fen_chars[r * 8:(r + 1) * 8]) for r in range(8)]
    return "-".join(ranks) + ".png"


def generate_for_set(piece_set, count, seed_offset=0):
    pieces = _load_piece_set(piece_set)
    set_dir = os.path.join(OUT_DIR, piece_set)

    for i in range(count):
        random.seed(hash((piece_set, i, seed_offset)))
        layout = _random_board_layout()
        light, dark = random.choice(BOARD_THEMES)

        canvas = _composite_board(layout, pieces, light, dark)
        canvas = _augment(canvas)

        # Index-numbered subdirectory guarantees a unique path even if
        # two random layouts collide (astronomically unlikely, but the
        # 8-segment filename format has no room for a disambiguating
        # suffix of its own).
        board_dir = os.path.join(set_dir, f"{i:05d}")
        os.makedirs(board_dir, exist_ok=True)
        canvas.save(os.path.join(board_dir, _filename_for(layout)))


def generate_all(boards_per_set=50):
    complete_sets = sorted(
        s for s in os.listdir(PNG_DIR)
        if len(os.listdir(os.path.join(PNG_DIR, s))) == 12
    )

    print(f"Generating {boards_per_set} boards each for {len(complete_sets)} piece sets...")

    for idx, piece_set in enumerate(complete_sets, 1):
        generate_for_set(piece_set, boards_per_set)
        print(f"[{idx}/{len(complete_sets)}] {piece_set}: {boards_per_set} boards")

    total = len(complete_sets) * boards_per_set
    print(f"\nDone: {total} synthetic boards ({total * 64:,} tiles once extracted)")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    generate_all(n)
