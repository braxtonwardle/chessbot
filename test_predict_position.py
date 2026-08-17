"""
Tests for predict_position._correct_king_count:  python test_predict_position.py

Operates on fabricated probability dicts, not real images -- no model
load needed. Real-world numbers behind these thresholds came from two
actual misreads: a clear-cut one (0.81 vs 0.43, a queen misread as a
second king -- should correct) and a genuine coin-flip one (0.44 vs
0.41, two squares each plausibly the king -- should NOT correct, since
guessing wrong there would turn a caught illegal position into a
silent, wrong-but-legal-looking one).
"""

from predict_position import _correct_king_count


def _square(char, probs):
    """A square with an explicit top guess and full probability dict."""
    return {"char": char, "probs": probs}


def test_leaves_a_single_king_alone():
    squares = [
        _square("K", {"K": 0.9, "Q": 0.05}),
        _square("q", {"q": 0.8, "k": 0.1}),
    ]

    _correct_king_count(squares, "K")

    assert squares[0]["char"] == "K"


def test_demotes_the_clearly_weaker_of_two_kings():
    # Modeled on a real misread: e1 (the real king) at 0.81, d1 (the
    # real queen, misread) at 0.43 -- a decisive gap.
    real_king = _square("K", {"K": 0.81, "R": 0.07, "1": 0.03})
    misread_queen = _square("K", {"K": 0.43, "R": 0.22, "B": 0.07, "Q": 0.06})
    squares = [real_king, misread_queen]

    _correct_king_count(squares, "K")

    assert real_king["char"] == "K"
    assert misread_queen["char"] == "R", "should fall back to its own runner-up guess"


def test_leaves_two_kings_alone_when_confidence_is_a_coin_flip():
    # Modeled on a real misread: 0.44 vs 0.41 -- trusting the higher
    # one picked the wrong square. Better to leave both as king and let
    # find_position_problem report an honest failure.
    squares = [
        _square("k", {"k": 0.44, "b": 0.20, "r": 0.07}),
        _square("k", {"k": 0.41, "r": 0.29, "b": 0.09}),
    ]

    _correct_king_count(squares, "k")

    assert squares[0]["char"] == "k"
    assert squares[1]["char"] == "k"


def test_promotes_a_clear_runner_up_when_no_square_guessed_king():
    # Modeled on a real misread: the true king square scored 0.32 for
    # "K" as its runner-up guess, clearly ahead of every other square.
    real_king = _square("R", {"R": 0.49, "K": 0.32, "1": 0.06})
    other = _square("1", {"1": 0.9, "K": 0.01})
    squares = [real_king, other]

    _correct_king_count(squares, "K")

    assert real_king["char"] == "K"


def test_leaves_position_alone_when_no_square_is_confidently_the_king():
    squares = [
        _square("R", {"R": 0.5, "K": 0.09}),
        _square("1", {"1": 0.6, "K": 0.08}),
    ]

    _correct_king_count(squares, "K")

    assert squares[0]["char"] == "R"
    assert squares[1]["char"] == "1"


if __name__ == "__main__":
    tests = [
        test_leaves_a_single_king_alone,
        test_demotes_the_clearly_weaker_of_two_kings,
        test_leaves_two_kings_alone_when_confidence_is_a_coin_flip,
        test_promotes_a_clear_runner_up_when_no_square_guessed_king,
        test_leaves_position_alone_when_no_square_is_confidently_the_king,
    ]

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
