from chessimg2pos import predict_fen
from crop_board import crop_to_chessboard
import sys


def compress_fen(board):
    rows = board.split("/")
    compressed_rows = []

    for row in rows:
        compressed = ""
        empty_count = 0

        for char in row:
            if char == "1":
                empty_count += 1
            else:
                if empty_count:
                    compressed += str(empty_count)
                    empty_count = 0
                compressed += char

        if empty_count:
            compressed += str(empty_count)

        compressed_rows.append(compressed)

    return "/".join(compressed_rows)


def correct_orientation(board):
    """
    chessimg2pos reads the image top-left to bottom-right and always
    assumes that's rank 8 / file a, with no idea whether the photo was
    actually taken from white's or black's side of the board. If it
    was black's perspective, the true board is rotated 180 degrees
    from what got predicted.

    Detects this using pawn positions: in a real game, White's pawns
    and Black's pawns are almost always still on their own side of
    the board relative to each other (pawns rarely travel the full
    board length). If the predicted layout shows White's pawns
    sitting further toward "rank 8" than Black's pawns on average,
    that's backwards, and a strong sign of a black's-perspective
    photo -- so it gets flipped back.

    Board is the uncompressed board string (rows separated by "/",
    empty squares as literal "1" characters), same format compress_fen
    expects. Returns the corrected board string in the same format.

    If either side has no pawns on the board, orientation can't be
    inferred this way, and the board is returned unchanged.
    """

    rows = board.split("/")

    white_pawn_rows = [
        row_index
        for row_index, row in enumerate(rows)
        for char in row
        if char == "P"
    ]

    black_pawn_rows = [
        row_index
        for row_index, row in enumerate(rows)
        for char in row
        if char == "p"
    ]

    if not white_pawn_rows or not black_pawn_rows:
        print(
            "correct_orientation: one side has no pawns on the board, "
            "can't infer orientation from pawn positions -- leaving as-is",
            file=sys.stderr
        )
        return board

    avg_white_row = sum(white_pawn_rows) / len(white_pawn_rows)
    avg_black_row = sum(black_pawn_rows) / len(black_pawn_rows)

    # Row 0 = predicted rank 8. White's pawns should sit at a *larger*
    # row index (closer to predicted rank 1) than Black's on average.
    # If that's inverted, the image was captured from black's side.
    if avg_white_row < avg_black_row:
        print(
            f"correct_orientation: white pawns averaged row "
            f"{avg_white_row:.1f}, black pawns {avg_black_row:.1f} -- "
            f"looks like a black's-perspective photo, flipping 180",
            file=sys.stderr
        )
        # 180-degree rotation: reverse row order, and reverse each
        # row's characters.
        rows = [row[::-1] for row in reversed(rows)]
        return "/".join(rows)

    return board


def main():
    while True:
        line = input().strip()

        if not line:
            continue

        # Node sends "image_path|side_to_move" -- "|" delimited since
        # the path itself never contains one. side_to_move is "w" or
        # "b"; defaults to "w" if missing or unrecognized.
        parts = line.split("|")
        image_path = parts[0]
        side_to_move = parts[1] if len(parts) > 1 else "w"

        if side_to_move not in ("w", "b"):
            print(
                f"Unrecognized side_to_move '{side_to_move}', defaulting to 'w'",
                file=sys.stderr
            )
            side_to_move = "w"

        try:
            # Isolate the actual chessboard before recognition -- screenshots
            # often include rank/file labels, move lists, or UI chrome
            # around the board, which throws off chessimg2pos's row/column
            # slicing and misreads pieces onto the wrong squares.
            cropped_path, board_found = crop_to_chessboard(image_path)

            if not board_found:
                raise RuntimeError(
                    "Couldn't confidently locate the board in that image. "
                    "Try a clearer or more tightly-framed photo/screenshot."
                )

            # Ask chessimg2pos for the board position.
            fen = predict_fen(cropped_path)

            # Correct for a board photographed from black's perspective,
            # before compressing (needs the literal "1" placeholders to
            # index rows/columns correctly).
            fen = correct_orientation(fen)

            # chessimg2pos returns empty squares as literal "1" characters
            # repeated (e.g. 111k1111) rather than standard compressed FEN
            # (3k4). Compress before building the URL.
            fen = compress_fen(fen)

            # Add the standard fields needed by Lichess, using the
            # side-to-move passed in from the WhatsApp command (wtp/btp).
            full_fen = f"{fen} {side_to_move} - - 0 1"

            lichess_url = (
                "https://lichess.org/analysis/standard/"
                + full_fen.replace(" ", "_")
            )

            print(lichess_url, flush=True)

        except Exception as e:
            print(f"ERROR: {e}", flush=True)


if __name__ == "__main__":
    print("READY", file=sys.stderr, flush=True)
    main()