"""Prepare already-rendered layers at the live compositor's working resolution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    files = {name for pose in data["poses"] for name in pose["layers"].values()}
    for name in sorted(files):
        with Image.open(args.manifest.parent / name) as image:
            image.convert("RGBa").resize((256, 256), Image.Resampling.LANCZOS).convert("RGBA").save(
                args.output / name
            )
    temporary = args.output / "manifest.tmp"
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(args.output / "manifest.json")
    print(f"Prepared {len(files)} layers in {args.output}")


if __name__ == "__main__":
    main()
