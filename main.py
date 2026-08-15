import sys
import os

def main():
    image = sys.argv[1]
    if os.path.exists(image):
        print(f"Found {image}")
    else:
        print(f"Can't find {image}")

if __name__ == "__main__":
    main()