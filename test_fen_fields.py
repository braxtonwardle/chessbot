"""
Tests for the FEN fields chessbot.py builds:  python test_fen_fields.py

Imports chessbot, so unlike test_crop_board.py this needs the requirements.
"""

from chessbot import castling_rights, compress_fen, find_position_problem


def board(*ranks):
    """Uncompressed board from 8 rank strings, "." for empty."""
    assert len(ranks) == 8, "a board needs 8 ranks"
    rows = [rank.replace(".", "1") for rank in ranks]
    for row in rows:
        assert len(row) == 8, f"rank {row!r} is not 8 squares"
    return "/".join(rows)


STARTING = board(
    "rnbqkbnr",
    "pppppppp",
    "........",
    "........",
    "........",
    "........",
    "PPPPPPPP",
    "RNBQKBNR",
)


def test_full_rights_when_nothing_has_moved():
    assert castling_rights(STARTING) == "KQkq"


def test_no_rights_once_the_kings_have_left_home():
    moved = board(
        "rnbq.bnr",
        "pppppppp",
        "....k...",
        "........",
        "........",
        "....K...",
        "PPPPPPPP",
        "RNBQ.BNR",
    )
    assert castling_rights(moved) == "-"


def test_rights_follow_the_individual_rooks():
    # White keeps only h1; Black only a8.
    partial = board(
        "r...k...",
        "pppppppp",
        "........",
        "........",
        "........",
        "........",
        "PPPPPPPP",
        "....K..R",
    )
    assert castling_rights(partial) == "Kq"


def test_a_realistic_middlegame_keeps_its_castling():
    middlegame = board(
        "r.bqkb.r",
        "pppp.ppp",
        "..n..n..",
        "....p...",
        "..B.P...",
        ".....N..",
        "PPPP.PPP",
        "RNBQK..R",
    )
    assert castling_rights(middlegame) == "KQkq"


def test_malformed_board_falls_back_to_no_rights():
    assert castling_rights("garbage") == "-"
    assert castling_rights("") == "-"


def test_a_legal_position_reports_no_problem():
    assert find_position_problem(compress_fen(STARTING)) is None


def test_missing_king_is_reported():
    no_white_king = board(
        "....k...",
        "........",
        "........",
        "........",
        "........",
        "........",
        "........",
        "........",
    )
    problem = find_position_problem(compress_fen(no_white_king))
    assert problem is not None
    assert "king" in problem


def test_doubled_king_is_reported():
    two_white_kings = board(
        "....k...",
        "........",
        "........",
        "........",
        "........",
        "........",
        "...KK...",
        "........",
    )
    problem = find_position_problem(compress_fen(two_white_kings))
    assert problem is not None
    assert "king" in problem


def test_pawn_on_the_back_rank_is_reported():
    pawn_on_rank_eight = board(
        "P...k...",
        "........",
        "........",
        "........",
        "........",
        "........",
        "........",
        "....K...",
    )
    problem = find_position_problem(compress_fen(pawn_on_rank_eight))
    assert problem is not None
    assert "pawn" in problem


def test_wrong_square_count_is_reported():
    # A rank claiming 9 squares.
    problem = find_position_problem("4k4/8/8/8/8/8/8/4K3")
    assert problem is not None
    assert "squares" in problem


def test_wrong_rank_count_is_reported():
    problem = find_position_problem("4k3/8/8/4K3")
    assert problem is not None
    assert "ranks" in problem


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]

    failures = 0

    for test in tests:
        try:
            test()
        except AssertionError as error:
            failures += 1
            print(f"FAIL  {test.__name__}\n      {error}")
        else:
            print(f"ok    {test.__name__}")

    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
