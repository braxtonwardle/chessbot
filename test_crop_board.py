"""
Tests for crop_board.crop_to_chessboard:  python test_crop_board.py

Needs only opencv and numpy; the recognition model is never loaded.
"""

import os
import shutil
import tempfile

import cv2
import numpy as np

from crop_board import crop_to_chessboard

HERE = os.path.dirname(os.path.abspath(__file__))


def _board_image(size=600):
    cropped, found = crop_to_chessboard(
        os.path.join(HERE, "position.png"),
        output_path=os.path.join(tempfile.mkdtemp(), "board.png"),
    )
    assert found, "position.png should contain a detectable board"
    return cv2.resize(cv2.imread(cropped), (size, size))


def _flat_card(canvas, x, y, side):
    """A square, high-contrast panel that is not a board."""
    cv2.rectangle(canvas, (x, y), (x + side, y + side), (240, 240, 240), -1)
    cv2.rectangle(canvas, (x, y), (x + side, y + side), (90, 90, 90), 6)


def test_finds_board_in_committed_screenshots():
    for name in ("position.png", "test_position.png"):
        path, found = crop_to_chessboard(os.path.join(HERE, name))
        assert found, f"{name}: board should be found"

        cropped = cv2.imread(path)
        height, width = cropped.shape[:2]
        aspect = width / height
        assert 0.92 <= aspect <= 1.08, f"{name}: crop not square ({aspect:.3f})"


def test_prefers_the_board_over_a_larger_square_panel():
    """Ranking candidates by area alone picks the panel here."""
    board = _board_image(600)
    canvas = np.full((1000, 1500, 3), 30, np.uint8)
    canvas[350:950, 60:660] = board
    _flat_card(canvas, 760, 130, 700)

    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "board_and_panel.png")
        cv2.imwrite(source, canvas)

        path, found = crop_to_chessboard(source)
        assert found, "board should still be found alongside a larger panel"

        cropped = cv2.imread(path)
        height, width = cropped.shape[:2]
        # Board is 600x600 here; the panel is 700x700.
        assert width < 680 and height < 680, (
            f"picked a {width}x{height} region -- looks like the panel, not the board"
        )


def test_reports_failure_when_no_board_is_present():
    canvas = np.full((1000, 1500, 3), 30, np.uint8)
    _flat_card(canvas, 400, 130, 700)

    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "no_board.png")
        cv2.imwrite(source, canvas)

        path, found = crop_to_chessboard(source)
        assert not found, "a panel with no checkerboard must not count as a board"
        assert path == source, "on failure the original path is returned unchanged"


def _cleanup_generated_crops():
    for name in ("position", "test_position"):
        for suffix in ("_cropped.png", "_nearmiss.png"):
            stale = os.path.join(HERE, name + suffix)
            if os.path.exists(stale):
                os.remove(stale)


if __name__ == "__main__":
    tests = [
        test_finds_board_in_committed_screenshots,
        test_prefers_the_board_over_a_larger_square_panel,
        test_reports_failure_when_no_board_is_present,
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

    _cleanup_generated_crops()

    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
