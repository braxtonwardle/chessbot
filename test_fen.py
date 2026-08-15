from chessimg2pos import predict_fen


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