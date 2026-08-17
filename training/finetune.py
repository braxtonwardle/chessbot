"""
finetune.py

Fine-tunes chessimg2pos's pretrained "enhanced" classifier on the
synthetic multi-piece-set dataset generate_synthetic_boards.py builds,
instead of training a fresh model from scratch. There's no way to
reconstruct chessimg2pos's original training data (only its resulting
weights are published), so continuing from those weights is what keeps
already-working piece sets working while adding the new ones, instead
of risking catastrophic forgetting from training on new-styles-only
data.

Usage:
    python finetune.py [epochs]
Saves to model_finetuned.pt
"""

import glob
import os
import sys

import numpy as np
import torch
from torch.utils.data import DataLoader

from chessimg2pos import ChessPositionPredictor, DEFAULT_CLASSIFIER
from chessimg2pos.chessclassifier import EnhancedChessPieceClassifier
from chessimg2pos.chessdataset import ChessTileDataset, create_image_transforms
from chessimg2pos.constants import DEFAULT_FEN_CHARS, DEFAULT_USE_GRAYSCALE
from chessimg2pos.generate_tiles import generate_tiles_from_all_chessboards
from chessimg2pos.model_loader import download_pretrained_model
from chessimg2pos.trainer import FocalLoss, LabelSmoothingLoss

HERE = os.path.dirname(os.path.abspath(__file__))
BOARDS_DIR = os.path.join(HERE, "synthetic_boards")
TILES_DIR = os.path.join(HERE, "tiles")
OUTPUT_MODEL = os.path.join(HERE, "model_finetuned.pt")

SEED = 1
TRAIN_TEST_RATIO = 0.85
BATCH_SIZE = 64
# Much lower than trainer.py's from-scratch default (0.001). A first
# attempt at 0.0003 for 10 epochs hit 99.9% accuracy on held-out
# *synthetic* tiles while badly regressing on real screenshots (extra
# ghost pieces on an already-correctly-read cburnett image) -- the
# model had overfit to pixel-level quirks of this synthetic renderer
# rather than learning genuinely general piece recognition. Synthetic
# val_acc is not a trustworthy stopping signal here; REAL_CHECK_IMAGES
# below is.
LEARNING_RATE = 0.00003
DEFAULT_EPOCHS = 6

# Real screenshots to sanity-check after every epoch, since synthetic
# validation accuracy proved misleading. The two "clean" ones must keep
# matching their known-correct baseline exactly -- any change there is
# a regression, not an improvement. The two "problem" ones are only
# reported for visibility (whether their king-count issue resolves),
# never used to justify accepting a regression on the clean ones.
REAL_CHECK_IMAGES = {
    "position.png (clean, must not change)": (
        os.path.join(os.path.dirname(HERE), "position_cropped.png"),
        "11Q11111/k11K1111/11111111/1111111p/111P111P/111111P1/11111111/1q111111",
    ),
    "test_position.png (clean, must not change)": (
        os.path.join(os.path.dirname(HERE), "test_position_cropped.png"),
        "r1111111/1111k1pp/11111n11/11111111/111Q1111/11111N11/1K111111/11111111",
    ),
}
REAL_PROGRESS_IMAGES = {
    "image1 (red/tan, was: 2 white kings + back-rank pawns)":
        os.path.join(os.path.dirname(HERE), "scratch_pieceset_cropped.jpeg"),
    "image2 (kosal-ish, was: king/rook + king/queen confusion)":
        os.path.join(os.path.dirname(HERE), "scratch_pieceset2_cropped.jpeg"),
}


def _check_clean_images(model_path):
    """True only if every clean reference image still matches its known
    -correct baseline exactly under this checkpoint."""

    predictor = ChessPositionPredictor(model_path=model_path, classifier=DEFAULT_CLASSIFIER)
    all_match = True

    for label, (path, expected_fen) in REAL_CHECK_IMAGES.items():
        if not os.path.exists(path):
            print(f"  (skipping {label}: {path} not found)")
            continue

        actual_fen = predictor.predict_chessboard(path)["fen"]
        matches = actual_fen == expected_fen
        all_match = all_match and matches
        status = "ok" if matches else "REGRESSION"
        print(f"  [{status}] {label}")

        if not matches:
            print(f"           expected: {expected_fen}")
            print(f"           got:      {actual_fen}")

    return all_match


def _report_progress_images(model_path):
    """Visibility only -- never gates whether a checkpoint is accepted."""

    predictor = ChessPositionPredictor(model_path=model_path, classifier=DEFAULT_CLASSIFIER)

    for label, path in REAL_PROGRESS_IMAGES.items():
        if not os.path.exists(path):
            continue

        fen = predictor.predict_chessboard(path)["fen"]
        white_kings, black_kings = fen.count("K"), fen.count("k")
        print(f"  [progress] {label}: {white_kings}W/{black_kings}B kings -- {fen}")


def _prepare_tiles():
    if not os.path.isdir(TILES_DIR) or not os.listdir(TILES_DIR):
        print("Generating tiles from synthetic boards...")
        generate_tiles_from_all_chessboards(
            chessboards_dir=BOARDS_DIR, tiles_dir=TILES_DIR,
            use_grayscale=DEFAULT_USE_GRAYSCALE, overwrite=False,
        )
    else:
        print(f"Reusing existing tiles in {TILES_DIR}")


def finetune(epochs=DEFAULT_EPOCHS):
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    _prepare_tiles()

    all_paths = np.array(
        glob.glob(os.path.join(TILES_DIR, "**", "*.png"), recursive=True)
    )

    if len(all_paths) == 0:
        raise RuntimeError(f"No tiles found in {TILES_DIR}")

    print(f"{len(all_paths):,} tiles total")

    np.random.shuffle(all_paths)
    split = int(len(all_paths) * TRAIN_TEST_RATIO)
    train_paths, test_paths = all_paths[:split], all_paths[split:]

    transform = create_image_transforms(DEFAULT_USE_GRAYSCALE)
    train_dataset = ChessTileDataset(train_paths, DEFAULT_FEN_CHARS, DEFAULT_USE_GRAYSCALE, transform)
    test_dataset = ChessTileDataset(test_paths, DEFAULT_FEN_CHARS, DEFAULT_USE_GRAYSCALE, transform)

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = EnhancedChessPieceClassifier(
        num_classes=len(DEFAULT_FEN_CHARS), use_grayscale=DEFAULT_USE_GRAYSCALE
    ).to(device)

    pretrained_path = download_pretrained_model()
    state_dict = torch.load(pretrained_path, map_location=device, weights_only=True)
    model.load_state_dict(state_dict)
    print(f"Loaded pretrained weights from {pretrained_path}")

    criterion_ce = torch.nn.CrossEntropyLoss()
    criterion_smooth = LabelSmoothingLoss(classes=len(DEFAULT_FEN_CHARS), smoothing=0.1)
    criterion_focal = FocalLoss(alpha=1, gamma=2)

    def combined_criterion(pred, target):
        return (
            0.5 * criterion_ce(pred, target)
            + 0.3 * criterion_smooth(pred, target)
            + 0.2 * criterion_focal(pred, target)
        )

    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=3, T_mult=2, eta_min=1e-6
    )

    last_good_epoch = None  # highest epoch that still passes the clean-image gate

    for epoch in range(epochs):
        model.train()
        running_loss, correct, total = 0.0, 0, 0

        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = combined_criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            running_loss += loss.item()
            _, predicted = torch.max(outputs.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()

        train_loss, train_acc = running_loss / len(train_loader), correct / total

        model.eval()
        val_loss, val_correct, val_total = 0.0, 0, 0

        with torch.no_grad():
            for inputs, labels in test_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs)
                loss = combined_criterion(outputs, labels)
                val_loss += loss.item()
                _, predicted = torch.max(outputs.data, 1)
                val_total += labels.size(0)
                val_correct += (predicted == labels).sum().item()

        val_loss, val_acc = val_loss / len(test_loader), val_correct / val_total
        scheduler.step()

        print(
            f"Epoch {epoch+1}/{epochs}  train_loss={train_loss:.4f} "
            f"train_acc={train_acc:.4f}  val_loss={val_loss:.4f} val_acc={val_acc:.4f}"
        )

        checkpoint_path = os.path.join(HERE, f"model_epoch{epoch + 1}.pt")
        torch.save(model.state_dict(), checkpoint_path)

        clean_ok = _check_clean_images(checkpoint_path)
        _report_progress_images(checkpoint_path)

        if clean_ok:
            last_good_epoch = epoch + 1
            print(f"  -> clean-image gate PASSED at epoch {epoch + 1}")
        else:
            print(f"  -> clean-image gate FAILED at epoch {epoch + 1} (regression -- stopping)")
            break

    if last_good_epoch is None:
        print("\nNo epoch passed the clean-image gate -- not saving a model. "
              "The pretrained weights are unchanged; try a lower learning rate.")
        return

    best_checkpoint = os.path.join(HERE, f"model_epoch{last_good_epoch}.pt")
    import shutil
    shutil.copyfile(best_checkpoint, OUTPUT_MODEL)
    print(f"\nSaved epoch {last_good_epoch}'s weights to {OUTPUT_MODEL} "
          f"(last epoch that didn't regress on the clean reference images)")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EPOCHS
    finetune(n)
