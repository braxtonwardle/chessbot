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


def castling_rights(board):
    """
    Castling field inferred from the king and rook home squares.

    Optimistic by necessity -- a photo can't show whether anyone has already
    moved, so a king that castled and returned to e1 still counts.

    Takes the uncompressed board (rows of 8 chars, "1" per empty square);
    returns e.g. "KQkq", or "-".
    """

    rows = board.split("/")

    if len(rows) != 8 or any(len(row) != 8 for row in rows):
        return "-"

    # Row 0 = rank 8, row 7 = rank 1; column 0 = a-file, 4 = e-file, 7 = h-file.
    rights = ""

    if rows[7][4] == "K":
        if rows[7][7] == "R":
            rights += "K"
        if rows[7][0] == "R":
            rights += "Q"

    if rows[0][4] == "k":
        if rows[0][7] == "r":
            rights += "k"
        if rows[0][0] == "r":
            rights += "q"

    return rights or "-"


def find_position_problem(board_field):
    """
    Why board_field can't be a real position, or None if it looks playable.

    Takes the compressed board field -- exactly what goes into the URL.
    """

    ranks = board_field.split("/")

    if len(ranks) != 8:
        return f"the board has {len(ranks)} ranks instead of 8"

    for index, rank in enumerate(ranks):
        squares = sum(int(char) if char.isdigit() else 1 for char in rank)

        if squares != 8:
            return f"rank {8 - index} covers {squares} squares instead of 8"

    white_kings = board_field.count("K")
    black_kings = board_field.count("k")

    if white_kings != 1 or black_kings != 1:
        return (
            f"I read {white_kings} white king(s) and {black_kings} black "
            "king(s), and there should be exactly one of each"
        )

    if any(char in "Pp" for char in ranks[0] + ranks[7]):
        return "there's a pawn on the first or last rank, which can't happen"

    return None


def main():
    while True:
        try:
            line = input().strip()
        except EOFError:
            break

        if not line:
            continue

        # "request_id|image_path|side_to_move". The id is echoed back on the
        # reply so Node can match a result to the request that asked for it.
        request_id, separator, remainder = line.partition("|")

        if not separator:
            print(
                f"Malformed request {line!r}, expected 'id|image_path|side'",
                file=sys.stderr
            )
            continue

        parts = remainder.split("|")
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

            # Before compression, which collapses the empty squares.
            castling = castling_rights(fen)

            # chessimg2pos returns empty squares as literal "1" characters
            # repeated (e.g. 111k1111) rather than standard compressed FEN
            # (3k4). Compress before building the URL.
            fen = compress_fen(fen)

            problem = find_position_problem(fen)

            if problem:
                raise RuntimeError(
                    f"That board didn't come out as a legal position -- "
                    f"{problem}. Try a clearer or more tightly-framed photo."
                )

            # Add the standard fields needed by Lichess, using the
            # side-to-move passed in from the WhatsApp command (wtp/btp).
            # En passant stays "-": a photo can't show the previous move.
            full_fen = f"{fen} {side_to_move} {castling} - 0 1"

            lichess_url = (
                "https://lichess.org/analysis/standard/"
                + full_fen.replace(" ", "_")
            )

            print(f"{request_id}|{lichess_url}", flush=True)

        except Exception as e:
            print(f"{request_id}|ERROR: {e}", flush=True)


if __name__ == "__main__":
    print("READY", file=sys.stderr, flush=True)
    main()