import shutil
from pathlib import Path

path = Path("metrics")

def main():
    for item in path.iterdir():
        if not item.is_dir():
            continue
        shutil.rmtree(item)

if __name__ == "__main__":
    main()