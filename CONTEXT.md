# ChessBot — project context

Handoff notes from a Claude (chat) session. Read this before making changes
so you're not rediscovering decisions that were already made deliberately.

## What this is

A WhatsApp bot for a chess group chat. Someone posts a chessboard image,
replies to it with `fen` (or `chessbot link`), and the bot recognizes the
position and replies with a Lichess analysis link.

## Architecture

- `chessbot.mjs` — Node/Baileys process. Handles WhatsApp connection,
  downloads the replied-to image, talks to the Python process over
  stdin/stdout, sends the reply.
- `chessbot.py` — Python process. Crops the board, runs chessimg2pos,
  corrects orientation, compresses the FEN, builds the Lichess URL.
- `crop_board.py` — Isolates the actual 8x8 board from a screenshot before
  recognition. Three-tier: outer-contour detection, a sliding-window
  checkerboard-pattern search for boards with a sidebar/panel attached on
  one edge, and (last resort) a coarse-then-fine position search across
  the whole image for cases where nearby UI fused into the board's own
  contour and neither tier above applies.
- `predict_position.py` — Wraps chessimg2pos's recognition: loads
  `models/chess_piece_classifier.pt` (see below) instead of chessimg2pos's
  stock download, and applies a one-king-per-side correction pass on top.
- `models/chess_piece_classifier.pt` — Fine-tuned piece classifier, see
  "Piece-set recognition" below. `training/` holds the scripts that built
  it (not the model itself needing to be rebuilt to make sense of them).
- `start-chessbot.bat` — Restart-on-crash wrapper; the intended way to run
  this, not `node chessbot.mjs` directly.

## Deliberate decisions (don't "fix" these without reading why first)

- **Baileys, not whatsapp-web.js.** whatsapp-web.js's `downloadMedia()` had
  an open, unresolved upstream bug (throws `r: r` from inside Puppeteer's
  page.evaluate) as of Aug 2026. Baileys talks to WhatsApp's protocol
  directly instead of scraping a live browser session, so it isn't exposed
  to that bug class. The old whatsapp-web.js version has since been removed
  from the repo entirely.
- **Analysis triggers on a reply to the image, not "the latest image in the
  group."** An earlier version saved whatever image arrived most recently
  per-group and analyzed that on command. This silently broke when any
  unrelated image got posted in between and overwrote the target. Requiring
  a reply ties the analysis to a specific message via WhatsApp's own
  `contextInfo`, so nothing else in the group can interfere.
- **Crop tolerance (`0.92`–`1.08` aspect ratio) is intentionally strict.**
  A failed crop now raises a clear "couldn't confidently locate the board"
  error instead of silently guessing on an uncropped image. A wrong silent
  guess produces a confidently-wrong FEN, which is worse than an honest
  failure. If you loosen this, know what you're trading away.
- **Orientation correction is a heuristic, not a certainty.** It compares
  average row position of white vs. black pawns to detect a black's-
  perspective photo needing a 180-degree flip. Requires at least one pawn
  of each color on the board; late endgames with all pawns traded off can't
  be corrected this way and are left as predicted.
- **Requests to the Python process are queued (`analysisQueue` in
  chessbot.mjs), not fired concurrently.** Replies are now tagged with a
  request id and routed to the right caller regardless, but chessbot.py
  still only handles one line at a time, so the queue stays to keep
  `ANALYSIS_TIMEOUT_MS` timing the analysis instead of the wait. Don't
  remove it to "simplify" this.
- **Files download-then-delete per request.** Every analysis downloads its
  own timestamped image and cleans up (`original`, `_cropped`, `_nearmiss`)
  in a `finally` block after, success or failure. Don't reintroduce a fixed
  per-group filename, that's what caused the silent-overwrite bug above.

## Known limitations (not yet fixed, on purpose — deprioritized, not forgotten)

- No side-to-move detection from the image itself; `fen b`/`btp`/`black`
  is a manual flag on the command, default is white.
- No engine eval, no puzzle-solution hiding/DM feature. Discussed and
  deliberately deferred, see "Ideas not yet built" below.
- Recognition accuracy depends on `models/chess_piece_classifier.pt`
  (see "Piece-set recognition" below). find_position_problem catches
  misreads that happen to produce an impossible position (extra kings,
  etc.), but a misread that still looks legal -- e.g. a bishop read as
  a knight -- can still slip through with no warning.

## Piece-set recognition

chessimg2pos ships a fixed pretrained classifier trained on a narrow set
of piece styles -- a style far from those gets misread (confirmed cases:
a set whose queen/king tops looked alike to it, and one whose knight
wasn't recognized as a piece at all). Rather than accept that, the model
was fine-tuned on 39 of lichess's 41 piece sets (`monarchy`/`mono`
excluded -- lichess doesn't serve separate art for them; `disguised` was
excluded on purpose, since it exists specifically to hide piece identity
from the viewer, making it actively counterproductive as training data).

- `training/fetch_piece_sets.py` downloads each set's SVGs from
  lichess1.org's public asset CDN (no auth needed).
- `training/render_piece_pngs.py` rasterizes them via `resvg_py` --
  `cairosvg` needs a native cairo library that isn't installed on
  Windows here, and resvg_py's prebuilt wheel has no such dependency.
  Sets sized in physical units (e.g. `merida`'s `50mm`) need an explicit
  `dpi` passed to resvg, or it fails with "invalid size."
- `training/generate_synthetic_boards.py` composites random positions
  across every rendered set into labeled board images, using
  chessimg2pos's own filename convention (`<rank8>-...-<rank1>.png`, one
  FEN character per square) so its existing tile-labeling code can
  consume them directly.
- `training/finetune.py` continues training from chessimg2pos's
  published weights rather than from scratch -- there's no way to
  reconstruct its original training data, only its resulting weights,
  so starting fresh would risk losing everything it already handled.
  **Synthetic validation accuracy is not a trustworthy stopping
  signal here**: a first attempt hit 99.9% held-out synthetic accuracy
  after 10 epochs while badly regressing on real screenshots (an
  already-correctly-read image came out full of extra ghost pieces) --
  the model had overfit to pixel-level quirks of the synthetic renderer
  rather than learning genuinely general recognition. The script now
  checkpoints every epoch and gates acceptance on two real reference
  images that must keep matching their known-correct FEN exactly;
  it stops and rolls back to the last epoch that didn't regress on
  them, regardless of what synthetic accuracy says. In practice that
  was epoch 4 of 6 at a low learning rate (`3e-5`).
- The synthetic images/tiles themselves (`training/piece_svgs`,
  `piece_pngs`, `synthetic_boards`, `tiles/`, and per-epoch checkpoints)
  are gitignored -- multiple GB, regenerated by re-running the scripts
  above in order, not meant to be committed.

## Ideas discussed, not yet built

- Engine eval (Stockfish + python-chess) alongside the Lichess link.
- Puzzle-answer spoiler protection: reply "answer"/"hint" to a puzzle image
  and get the solution via WhatsApp DM instead of posted in the group, so
  it doesn't spoil it for others. This was the one idea that actually
  landed as worth building, over eval/leaderboard/rating-lookup ideas that
  were floated and passed on.
- Reverse direction: parse a pasted lichess.org/chess.com URL and reply
  with a rendered board, instead of only image-to-link.

## Workflow preference

Prefer small, individually reviewable commits over large batched changes,
one logical change per commit with an honest, specific message. This was an
explicit ask after a hallucination surfaced in an unrelated PR review — the
goal is diffs a human can actually check, not just working code.
