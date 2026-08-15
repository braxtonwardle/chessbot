from chessbot import compress_fen
from chessimg2pos import predict_fen


# Get the board from the image
board = predict_fen("test_position.png")

# Convert 1s into proper FEN numbers
position = compress_fen(board)

# Complete FEN
fen = f"{position} w - - 0 1"

# Lichess analysis URL
lichess_url = f"https://lichess.org/analysis/standard/{fen.replace(' ', '_')}"

print("FEN:")
print(fen)

print("\nLichess analysis:")
print(lichess_url)