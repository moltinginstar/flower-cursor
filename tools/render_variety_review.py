"""Inspect real species lifecycle output; these composites are not terminal screenshots."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flower_cursor.graphics_renderer import DOWNSCALE
from flower_cursor.layered import LayeredCompositor, LayeredRuntime, LayeredVisual
from flower_cursor.runtime import CursorCell


def scenario(manifest: Path, label: str, output: Path) -> dict:
    runtime = LayeredRuntime(manifest)
    compositor = LayeredCompositor(manifest)
    home, away = CursorCell(12, 20), CursorCell(12, 24)
    runtime.move_to(home)
    identifier = runtime.active.flower.identifier
    if label != "growth":
        runtime.update(2.0)
        runtime.move_to(away)
        if label == "recovery":
            runtime.update(0.16)
            runtime.move_to(home)
    checkpoints = (0, 0.07, 0.13, 0.20, 0.35, 0.50, 0.75, 1.20)
    sheet = Image.new("RGBA", (256 * len(checkpoints), 425), "#20242b")
    draw = ImageDraw.Draw(sheet)
    timings = []
    elapsed = 0.0
    for column, target in enumerate(checkpoints):
        image = Image.new("RGBA", (256, 256))
        while True:
            started = time.perf_counter()
            if elapsed < target:
                step = min(1 / 60, target - elapsed)
                runtime.update(step)
                elapsed += step
            visual = next(
                (
                    v
                    for v in runtime.visuals()
                    if isinstance(v, LayeredVisual) and v.identifier == identifier
                ),
                None,
            )
            image = (
                compositor.frame(visual) if visual is not None else Image.new("RGBA", (256, 256))
            )
            timings.append((time.perf_counter() - started) * 1000)
            if elapsed >= target:
                break
        x = column * 256
        draw.text((x + 12, 8), f"{label} / {target:.2f}s", fill="white")
        sheet.alpha_composite(image, (x, 30))
        native = image.convert("RGBa").resize((144, 128), DOWNSCALE).convert("RGBA")
        sheet.alpha_composite(native, (x + 56, 284))
        image.save(output / f"{label}-{column:02d}.png")
    sheet.convert("RGB").save(output / f"{label}.png")
    return {
        "frames": len(timings),
        "median_ms": statistics.median(timings),
        "p95_ms": sorted(timings)[int(0.95 * (len(timings) - 1))],
        "max_ms": max(timings),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    timings = {
        label: scenario(args.manifest, label, args.output)
        for label in ("growth", "shedding", "recovery")
    }
    (args.output / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")
    print(json.dumps(timings, indent=2))


if __name__ == "__main__":
    main()
