"""
crop_board.py

Detects and crops the chessboard square out of a screenshot before
handing the image to chessimg2pos. chessimg2pos assumes the entire
input image IS the 8x8 board -- if there's extra height/width from
rank/file labels, a move list, arrows, or nav buttons around it, the
row/column slicing goes out of alignment and pieces get misread onto
the wrong squares (this is what was happening with rank 1 disappearing
and rank 8 bleeding into rank 7).

Usage:
    from crop_board import crop_to_chessboard
    cropped_path = crop_to_chessboard("received_12345.png")
    fen = predict_fen(cropped_path)
"""

import cv2
import numpy as np
import os
import sys


# Real boards measure in the 50s-60s; flat UI panels measure ~0.
CHESSBOARD_MIN_SCORE = 12.0

# Checkerboard parity (which corner counts as "light") doesn't actually
# affect the score below -- abs(light_mean - dark_mean) comes out
# identical either way abs() is parity-invariant. Computed once here
# instead of inside _chessboard_likeness, which crop_board's whole-image
# fallback search calls thousands of times per image.
_CHECKER_PATTERN = np.indices((8, 8)).sum(axis=0) % 2


def _chessboard_likeness(gray_region, grid_size=8, cell_px=20):
    """
    Scores how much a region looks like an occupied chessboard by
    checking for the 8x8 alternating light/dark pattern, rather than
    just relying on the region's outer edges. This is what lets us
    tell "the board" apart from "the board plus an attached sidebar",
    which have the same outer bounding box shape but very different
    internal structure.

    Returns a score where higher = more checkerboard-like. Not an
    absolute probability, just a relative ranking used to compare
    candidate crops against each other.
    """

    size = grid_size * cell_px
    resized = cv2.resize(gray_region, (size, size))

    # Cell means via reshape+mean instead of a Python loop over 64
    # cells -- same numbers, but this runs thousands of times per image
    # in the whole-image fallback search, where per-cell Python-level
    # slicing/indexing overhead adds up fast on weaker CPUs.
    cell_means = resized.reshape(grid_size, cell_px, grid_size, cell_px).mean(axis=(1, 3))

    if grid_size == 8:
        pattern = _CHECKER_PATTERN
    else:
        pattern = np.indices((grid_size, grid_size)).sum(axis=0) % 2

    light_group = cell_means[pattern == 1]
    dark_group = cell_means[pattern == 0]
    # Larger gap between the two groups' average brightness = stronger
    # checkerboard signal. Pieces sitting on squares add noise but
    # rarely erase the alternation entirely.
    best_score = abs(light_group.mean() - dark_group.mean())

    return best_score


def _refine_via_sliding_square(gray, box, min_score=CHESSBOARD_MIN_SCORE, steps=15):
    """
    Given a candidate box that's the right height but too wide (or
    right width but too tall) -- typically a board with a sidebar or
    panel still attached on one edge -- slides a square window across
    the long axis looking for the sub-region with the strongest
    chessboard pattern, instead of assuming the board fills the whole
    detected box.

    Returns (x, y, size, size) if a confident square is found, else None.
    """

    x, y, w, h = box
    size = min(w, h)

    if size <= 0:
        return None

    if w >= h:
        # Too wide: board height is trustworthy, slide along width.
        max_offset = w - size
    else:
        # Too tall: board width is trustworthy, slide along height.
        max_offset = h - size

    if max_offset <= 0:
        return None

    best_offset = None
    best_score = 0

    for i in range(steps + 1):
        offset = int(max_offset * i / steps)

        if w >= h:
            candidate = gray[y:y + size, x + offset:x + offset + size]
        else:
            candidate = gray[y + offset:y + offset + size, x:x + size]

        if candidate.shape[0] != size or candidate.shape[1] != size:
            continue

        score = _chessboard_likeness(candidate)

        if score > best_score:
            best_score = score
            best_offset = offset

    if best_offset is None or best_score < min_score:
        return None

    if w >= h:
        return (x + best_offset, y, size, size), best_score
    else:
        return (x, y + best_offset, size, size), best_score


def _best_square_in_range(gray, x_min, x_max, y_min, y_max, sizes, position_steps):
    """
    Scans square candidates of the given sizes across [x_min, x_max] x
    [y_min, y_max] for the strongest checkerboard pattern. Shared by the
    coarse whole-image search and its local fine-refinement pass below.

    Returns (x, y, size, size), score for the best candidate found (even
    if weak) -- callers are responsible for applying a min_score cutoff.
    """

    height, width = gray.shape[:2]
    x_min = max(x_min, 0)
    y_min = max(y_min, 0)

    best_box = None
    best_score = 0

    for size in sizes:

        if size <= 0:
            continue

        local_x_max = min(x_max, width - size)
        local_y_max = min(y_max, height - size)

        if local_x_max < x_min or local_y_max < y_min:
            continue

        for xi in range(position_steps + 1):
            x = int(x_min + (local_x_max - x_min) * xi / position_steps) if local_x_max > x_min else x_min

            for yi in range(position_steps + 1):
                y = int(y_min + (local_y_max - y_min) * yi / position_steps) if local_y_max > y_min else y_min

                candidate = gray[y:y + size, x:x + size]

                if candidate.shape[0] != size or candidate.shape[1] != size:
                    continue

                score = _chessboard_likeness(candidate)

                if score > best_score:
                    best_score = score
                    best_box = (x, y, size, size)

    if best_box is None:
        return None

    return best_box, best_score


def _search_whole_image(gray, min_score=CHESSBOARD_MIN_SCORE,
                         size_steps=6, position_steps=10,
                         min_size_fraction=0.25):
    """
    Last resort when the outer contour isn't the board's own shape at
    all -- e.g. a screenshot where nearby message bubbles above/below
    fused with the board into one tall blob under edge dilation, so
    neither the blob's width nor height is actually the board's size.
    _refine_via_sliding_square can't help there since it trusts one of
    those two dimensions.

    Coarsely scans square windows across the whole image at multiple
    sizes and positions for the strongest checkerboard pattern, then
    refines locally around the best hit with finer position steps and a
    narrower band of nearby sizes -- the coarse grid alone lands close to
    the board but rarely on its exact edges, and an off-by-a-rank crop is
    exactly what this module exists to prevent. A contour-based refine
    was tried here first, but re-running Canny/dilate on a tight crop
    just rediscovers the same problem this function exists to route
    around (the crop boundary itself gets picked up as a false edge, or
    a slightly-too-generous margin drags the same clutter back in) --
    plain position-grid refinement is the more reliable of the two.

    Returns (x, y, size, size), score if a confident square is found,
    else None.
    """

    height, width = gray.shape[:2]
    min_dim = min(height, width)

    sizes = [
        int(min_dim * (min_size_fraction + (1 - min_size_fraction) * i / max(size_steps - 1, 1)))
        for i in range(size_steps)
    ]

    coarse = _best_square_in_range(gray, 0, width, 0, height, sizes, position_steps)

    if coarse is None:
        return None

    (cx, cy, csize, _), coarse_score = coarse

    # Local refinement: the coarse pass tends to land on a slightly
    # undersized, roughly-correctly-positioned square (a smaller crop
    # nested inside the true board still scores well, since most of its
    # cells still alternate correctly). Search a band of larger sizes
    # centered near the coarse hit, at much finer position steps, to
    # converge on the true edges.
    radius = csize // 4
    fine_sizes = [
        int(csize * factor)
        for factor in (1.0, 1.03, 1.06, 1.09, 1.12, 1.15)
    ]

    fine = _best_square_in_range(
        gray, cx - radius, cx + csize + radius, cy - radius, cy + csize + radius,
        fine_sizes, position_steps=60
    )

    best_box, best_score = fine if fine and fine[1] >= coarse_score else coarse

    if best_score < min_score:
        return None

    return best_box, best_score


def crop_to_chessboard(image_path, output_path=None, min_area_fraction=0.15,
                       min_score=CHESSBOARD_MIN_SCORE):
    """
    Detects the roughly-square region whose contents look most like a
    checkerboard and crops to it.

    Returns a tuple: (path_to_use, board_found)

    If no confident square region is found, board_found is False and
    path_to_use is the original image_path unchanged. Callers should
    treat board_found=False as a reason to stop and ask for a clearer
    image rather than attempt recognition on an unconfirmed crop --
    running chessimg2pos on a screenshot that still has labels/UI
    around the board produces a confidently wrong FEN, which is worse
    than an honest failure.

    Args:
        image_path: path to the source screenshot
        output_path: where to save the cropped image. Defaults to
            "<original>_cropped.png" alongside the source.
        min_area_fraction: the candidate square must cover at least
            this fraction of the total image area to be considered
            the board (filters out small false-positive squares like
            icons or buttons).
        min_score: the candidate must also score at least this on
            _chessboard_likeness, so a large square region that isn't a
            board can't win on size alone.
    """

    image = cv2.imread(image_path)

    if image is None:
        print(f"crop_to_chessboard: could not read {image_path}, skipping crop", file=sys.stderr)
        return image_path, False

    height, width = image.shape[:2]
    total_area = height * width

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)

    # Close small gaps in the board's outer edge so it forms one
    # continuous contour instead of broken segments.
    kernel = np.ones((5, 5), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(
        edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    best_box = None
    best_score = 0

    # Track the best near-miss too, purely for diagnostics, so a failed
    # detection tells us *why* it failed instead of just that it did.
    near_miss = None
    near_miss_area_frac = 0

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        area_frac = area / total_area if total_area else 0
        aspect_ratio = w / h if h > 0 else 0

        if area_frac > near_miss_area_frac:
            near_miss_area_frac = area_frac
            near_miss = (x, y, w, h, aspect_ratio, area_frac)

        if area < total_area * min_area_fraction:
            continue

        # Chessboards are square. Allow a little tolerance for
        # screenshot compression artifacts / anti-aliasing.
        if not 0.92 <= aspect_ratio <= 1.08:
            continue

        score = _chessboard_likeness(gray[y:y + h, x:x + w])

        if score >= min_score and score > best_score:
            best_score = score
            best_box = (x, y, w, h)

    if best_box is None:

        if near_miss:
            nx, ny, nw, nh, n_aspect, n_frac = near_miss

            # Before giving up: this rectangle might be the board with a
            # sidebar/panel attached on one edge. Try sliding a square
            # window across it looking for the actual checkerboard pattern.
            refined = _refine_via_sliding_square(gray, (nx, ny, nw, nh))

            if refined:
                (rx, ry, rsize, _), score = refined
                cropped = image[ry:ry + rsize, rx:rx + rsize]

                if output_path is None:
                    base, ext = os.path.splitext(image_path)
                    output_path = f"{base}_cropped{ext}"

                cv2.imwrite(output_path, cropped)

                print(
                    f"crop_to_chessboard: outer contour was {nw}x{nh} "
                    f"(aspect={n_aspect:.3f}, likely board+sidebar), "
                    f"refined via checkerboard pattern search to "
                    f"{rsize}x{rsize} at ({rx},{ry}) [score={score:.1f}] "
                    f"-> {output_path}",
                    file=sys.stderr
                )

                return output_path, True

            # Still no luck -- the near-miss box's shape itself may not
            # relate to the board at all (e.g. it swallowed message
            # bubbles above/below a screenshot into one tall blob, so
            # neither its width nor height is trustworthy). Fall back to
            # scanning the whole image for the board directly.
            searched = _search_whole_image(gray)

            if searched:
                (sx, sy, ssize, _), score = searched
                cropped = image[sy:sy + ssize, sx:sx + ssize]

                if output_path is None:
                    base, ext = os.path.splitext(image_path)
                    output_path = f"{base}_cropped{ext}"

                cv2.imwrite(output_path, cropped)

                print(
                    f"crop_to_chessboard: outer contour ({nw}x{nh}, "
                    f"aspect={n_aspect:.3f}) didn't match the board's own "
                    f"shape, found it via a whole-image search instead at "
                    f"{ssize}x{ssize} ({sx},{sy}) [score={score:.1f}] "
                    f"-> {output_path}",
                    file=sys.stderr
                )

                return output_path, True

            # Save the near-miss region itself so it can be inspected
            # visually -- numbers alone (aspect ratio, area) don't show
            # *what* the algorithm actually found.
            near_miss_crop = image[ny:ny + nh, nx:nx + nw]
            base, ext = os.path.splitext(image_path)
            near_miss_path = f"{base}_nearmiss{ext}"
            cv2.imwrite(near_miss_path, near_miss_crop)

            print(
                f"crop_to_chessboard: no confident board region found in "
                f"{image_path} (best candidate: {nw}x{nh} at ({nx},{ny}), "
                f"aspect={n_aspect:.3f}, area_frac={n_frac:.3f}, "
                f"saved as {near_miss_path} for inspection), "
                f"using original image uncropped",
                file=sys.stderr
            )
        else:
            print(
                f"crop_to_chessboard: no confident board region found in "
                f"{image_path} (no contours detected at all), "
                f"using original image uncropped",
                file=sys.stderr
            )
        return image_path, False

    x, y, w, h = best_box
    cropped = image[y:y + h, x:x + w]

    if output_path is None:
        base, ext = os.path.splitext(image_path)
        output_path = f"{base}_cropped{ext}"

    cv2.imwrite(output_path, cropped)

    print(
        f"crop_to_chessboard: cropped {image_path} "
        f"({width}x{height}) -> {output_path} ({w}x{h}) "
        f"[score={best_score:.1f}]",
        file=sys.stderr
    )

    return output_path, True


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python crop_board.py <image_path>")
        sys.exit(1)

    result_path, found = crop_to_chessboard(sys.argv[1])
    print(f"Result: {result_path} (board_found={found})")