"""
predict_position.py

Wraps chessimg2pos's tile classifier with two things it doesn't have on
its own:

1. A locally fine-tuned model (models/chess_piece_classifier.pt) instead
   of chessimg2pos's stock download. The stock model was trained on a
   narrow set of piece styles (see CONTEXT.md) and misreads anything far
   from them. This one continues training from those same weights across
   39 lichess piece sets (see training/), so it keeps recognizing
   whatever the original model already handled while adding the rest.
   training/finetune.py picked this checkpoint specifically because it
   was the last one that didn't regress on two known-good reference
   images -- longer fine-tuning kept climbing on synthetic validation
   accuracy while actually getting worse on real screenshots.

2. One hard rule neither model knows about: a legal position has exactly
   one king per side. Outside a model's comfort zone, the king is the
   piece most often confused with something else -- a queen or rook
   shares its tall, ornamented silhouette -- producing a position with
   zero or two kings for a color. Rather than let that reach
   find_position_problem as an unrecoverable failure, this looks past
   each square's top guess to its full probability distribution and
   picks whichever resolution is consistent with the one-king rule.

Usage:
    from predict_position import predict_fen_corrected
    board = predict_fen_corrected("board.png")
"""

import os

import torch
from chessimg2pos import ChessPositionPredictor, DEFAULT_CLASSIFIER
from chessimg2pos.chessboard_image import get_chessboard_tiles

_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "chess_piece_classifier.pt")

_predictor = None


def _get_predictor():
    """Loads the model once and reuses it -- chessbot.py runs as one
    long-lived process, so there's no need to reload it per request."""

    global _predictor

    if _predictor is None:
        _predictor = ChessPositionPredictor(
            model_path=_MODEL_PATH, classifier=DEFAULT_CLASSIFIER
        )

    return _predictor


def _tile_probabilities(predictor, tile_img):
    """Full probability distribution over predictor.fen_chars for one
    tile, instead of chessimg2pos's own predict_tile which only returns
    the top choice."""

    tile = tile_img.copy()

    if predictor.use_grayscale:
        tile = tile.convert("L")

    tensor = predictor.transform(tile).unsqueeze(0).to(predictor.device)

    with torch.no_grad():
        outputs = predictor.model(tensor)
        probabilities = torch.nn.functional.softmax(outputs, dim=1)[0]

    return {
        char: probabilities[i].item()
        for i, char in enumerate(predictor.fen_chars)
    }


def _correct_king_count(squares, king_char, min_confidence=0.15, min_margin=0.15):
    """
    Enforces exactly one king_char among squares (each a dict with
    'char' and 'probs' keys), using every square's own probability for
    king_char rather than only its top guess.

    Only acts when the choice is clear-cut (the winning square beats
    the next-best alternative by at least min_margin, and clears
    min_confidence outright). Tried without that gate first: on a real
    misread, the two squares vying to be "the" king were 0.44 vs 0.41 --
    close enough that trusting the higher one picked the wrong square
    more often than not. When it's this close, leaving the extra/missing
    king in place and letting find_position_problem report an honest
    failure is better than confidently guessing wrong and silently
    producing an incorrect-but-legal-looking position.
    """

    king_squares = [s for s in squares if s["char"] == king_char]

    if len(king_squares) == 1:
        return

    if not king_squares:
        # No square's top guess was this king -- consider promoting
        # whichever square gave it the highest probability, even as a
        # runner-up guess there.
        ranked = sorted(squares, key=lambda s: s["probs"].get(king_char, 0), reverse=True)
        best, runner_up = ranked[0], ranked[1]
        best_score = best["probs"].get(king_char, 0)

        if (
            best_score >= min_confidence
            and best_score - runner_up["probs"].get(king_char, 0) >= min_margin
        ):
            best["char"] = king_char

        return

    # More than one square claimed to be this king: if one is clearly
    # more confident than the rest, trust it, and give the others back
    # their own runner-up guess instead of leaving them as an extra,
    # impossible king.
    king_squares.sort(key=lambda s: s["probs"][king_char], reverse=True)
    winner, runner_up = king_squares[0], king_squares[1]

    if winner["probs"][king_char] - runner_up["probs"][king_char] < min_margin:
        return

    for square in king_squares[1:]:
        runner_up_char = max(
            (char for char in square["probs"] if char != king_char),
            key=lambda char: square["probs"][char]
        )
        square["char"] = runner_up_char


def predict_fen_corrected(image_path):
    """
    Like chessimg2pos.predict_fen, but applies chess's one-king-per-side
    rule to recover from the king/queen or king/rook misreads a piece
    set outside chessimg2pos's training data tends to cause. Returns the
    board field only (uncompressed, "1" per empty square) -- the same
    format predict_fen's raw output has.
    """

    predictor = _get_predictor()
    tiles = get_chessboard_tiles(image_path, use_grayscale=predictor.use_grayscale)

    if len(tiles) != 64:
        raise ValueError(f"Expected 64 tiles, got {len(tiles)}")

    # Tiles arrive rank-major from rank 8 down to rank 1, a-file to
    # h-file within each rank -- the same row order compress_fen,
    # correct_orientation, and castling_rights all assume.
    squares = [
        {"char": max(probs, key=probs.get), "probs": probs}
        for probs in (_tile_probabilities(predictor, tile) for tile in tiles)
    ]

    _correct_king_count(squares, "K")
    _correct_king_count(squares, "k")

    rows = [
        "".join(square["char"] for square in squares[rank * 8:(rank + 1) * 8])
        for rank in range(8)
    ]

    return "/".join(rows)
