"""Prepare already-rendered layers at the live compositor's working resolution.

Each layer name is also given its crop: the smallest rectangle holding that
layer's content in every pose, which is all of it the compositor keeps in
memory. `--crops-only` records the crops for layers that are already prepared,
without re-encoding them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def layer_crops(data: dict, directory: Path) -> dict[str, object]:
    """The union of every pose's content bounds, per layer name.

    One rectangle per name rather than per file keeps every pose of a layer the
    same size, which is what lets the compositor blend them pairwise. A layer
    empty in every pose, like the dahlia's head, gets an empty rectangle. The
    canvas the rectangles are measured on is recorded with them.
    """
    crops: dict[str, list[int]] = {
        layer: [0, 0, 0, 0] for pose in data["poses"] for layer in pose["layers"]
    }
    drawn: set[str] = set()
    sizes = set()
    for pose in data["poses"]:
        for layer, name in pose["layers"].items():
            with Image.open(directory / name) as image:
                sizes.add(image.size)
                bounds = image.getchannel("A").getbbox()
            if bounds is None:
                continue
            known = crops[layer]
            if layer not in drawn:
                drawn.add(layer)
                known[:] = bounds
            known[:] = [
                min(known[0], bounds[0]),
                min(known[1], bounds[1]),
                max(known[2], bounds[2]),
                max(known[3], bounds[3]),
            ]
    if len(sizes) != 1:
        raise SystemExit(f"layers disagree on their canvas: {sorted(sizes)}")
    return {"canvas": list(sizes.pop()), "layers": dict(sorted(crops.items()))}


def write_manifest(data: dict, directory: Path) -> None:
    temporary = directory / "manifest.tmp"
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(directory / "manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--output", type=Path)
    target.add_argument("--crops-only", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    if args.crops_only:
        data["crops"] = layer_crops(data, args.manifest.parent)
        write_manifest(data, args.manifest.parent)
        print(f"Recorded crops for {len(data['crops']['layers'])} layers in {args.manifest}")
        return
    args.output.mkdir(parents=True, exist_ok=True)
    files = {name for pose in data["poses"] for name in pose["layers"].values()}
    for name in sorted(files):
        with Image.open(args.manifest.parent / name) as image:
            image.convert("RGBa").resize((256, 256), Image.Resampling.LANCZOS).convert("RGBA").save(
                args.output / name
            )
    data["crops"] = layer_crops(data, args.output)
    write_manifest(data, args.output)
    print(f"Prepared {len(files)} layers in {args.output}")


if __name__ == "__main__":
    main()
