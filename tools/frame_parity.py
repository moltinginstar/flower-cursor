"""Hash every frame the layered compositor draws, so a refactor can prove it drew the same ones.

Drives each species headless through growth, a full idle cycle, a departure with
a return mid-wither, and a wither to the end, drawing every visual on every
step. Record a baseline before a change and compare after it:

    python tools/frame_parity.py --write baseline.json
    python tools/frame_parity.py --compare baseline.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flower_cursor.layered import LayeredCompositor, LayeredRuntime
from flower_cursor.runtime import CursorCell
from flower_cursor.settings import FLOWER_CATALOGUE

STEP = 1 / 60
HOME, AWAY = CursorCell(12, 20), CursorCell(12, 24)
# One idle cycle is 2*pi / 1.7 seconds of wall time.
IDLE = math.tau / 1.7


def frames(manifest: Path, species: str):
    runtime = LayeredRuntime(manifest, bee=False, species=species)
    compositor = LayeredCompositor(manifest, species=species)

    def run(seconds: float):
        for _ in range(round(seconds / STEP)):
            runtime.update(STEP)
            visuals = runtime.visuals()
            for visual in visuals:
                image = compositor.frame(visual)
                yield hashlib.sha256(f"{image.mode}{image.size}".encode() + image.tobytes())
            compositor.retain({v.identifier for v in visuals})

    runtime.move_to(HOME)
    yield from run(1.5 + IDLE)
    runtime.move_to(AWAY)
    yield from run(0.15)
    runtime.move_to(HOME)
    yield from run(0.5)
    runtime.move_to(AWAY)
    yield from run(3.0)


def digests() -> dict[str, dict[str, object]]:
    result = {}
    for spec in FLOWER_CATALOGUE:
        hashes = [h.hexdigest() for h in frames(spec.manifest, spec.name)]
        combined = hashlib.sha256("".join(hashes).encode()).hexdigest()
        result[spec.name] = {"frames": len(hashes), "digest": combined, "hashes": hashes}
        print(f"{spec.name}: {len(hashes)} frames {combined[:16]}", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path)
    group.add_argument("--compare", type=Path)
    args = parser.parse_args()
    current = digests()
    if args.write:
        args.write.write_text(json.dumps(current) + "\n")
        return 0
    baseline = json.loads(args.compare.read_text())
    failed = False
    for name, expected in baseline.items():
        actual = current.get(name)
        if actual is None or actual["digest"] != expected["digest"]:
            failed = True
            got = actual["hashes"] if actual else []
            first = next(
                (i for i, (a, b) in enumerate(zip(expected["hashes"], got)) if a != b),
                min(len(expected["hashes"]), len(got)),
            )
            print(f"MISMATCH {name}: first differing frame {first}")
    print("parity FAILED" if failed else "parity OK: every frame identical")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
