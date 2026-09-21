"""Offline checkpoints through the real bee and flower runtime/compositor."""

from pathlib import Path

from PIL import Image, ImageDraw

from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST

OUTPUT = Path(__file__).resolve().parents[1] / "assets/review/bee"


def advance(runtime, seconds):
    for _ in range(round(seconds * 60)):
        runtime.update(1 / 60)


def tile(runtime, compositor, title):
    canvas = Image.new("RGBA", (256, 256))
    for visual in runtime.visuals():
        if visual.cell == CursorCell(8, 12):
            canvas.alpha_composite(compositor.frame(visual))
    result = Image.new("RGB", (320, 270), "#191c25")
    result.paste(canvas, (32, 30), canvas)
    draw = ImageDraw.Draw(result)
    draw.text((10, 10), title, fill="white")
    native = canvas.convert("RGBa").resize((144, 128), Image.Resampling.LANCZOS).convert("RGBA")
    result.paste(native, (160, 135), native)
    draw.text((164, 245), "terminal scale", fill="#bac2ce")
    return result


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    compositor = LayeredCompositor(DEFAULT_MANIFEST)
    runtime = LayeredRuntime(DEFAULT_MANIFEST)
    runtime.move_to(CursorCell(8, 12))
    advance(runtime, 10.8)
    entrance = tile(runtime, compositor, "Entrance / 0.8s into visit")
    advance(runtime, 1.5)
    hover = tile(runtime, compositor, "Hover / legs deploying")
    advance(runtime, 2.0)
    rest = tile(runtime, compositor, "Settled / folded wings")
    before = compositor.frame(next(v for v in runtime.visuals() if isinstance(v, BeeVisual)))
    runtime.move_to(CursorCell(8, 22))
    after = compositor.frame(next(v for v in runtime.visuals() if isinstance(v, BeeVisual)))
    assert before.tobytes() == after.tobytes()
    advance(runtime, 0.2)
    mature = tile(runtime, compositor, "Departure / mature shedding")
    advance(runtime, 0.55)
    late = tile(runtime, compositor, "Departure / stalk gone")
    runtime = LayeredRuntime(DEFAULT_MANIFEST)
    runtime.move_to(CursorCell(8, 12))
    advance(runtime, 10.8)
    runtime.move_to(CursorCell(8, 22))
    advance(runtime, 0.1)
    runtime.move_to(CursorCell(8, 12))
    advance(runtime, 0.1)
    runtime.move_to(CursorCell(8, 22))
    advance(runtime, 0.12)
    partial = tile(runtime, compositor, "Aborted approach / partial return exit")
    tiles = [entrance, hover, rest, mature, late, partial]
    sheet = Image.new("RGB", (960, 540), "#191c25")
    for i, image in enumerate(tiles):
        image.save(OUTPUT / f"checkpoint_{i + 1}.png")
        sheet.paste(image, ((i % 3) * 320, (i // 3) * 270))
    sheet.save(OUTPUT / "checkpoints.png")
    runtime = LayeredRuntime(DEFAULT_MANIFEST)
    runtime.move_to(CursorCell(8, 12))
    advance(runtime, 9.8)
    frames = []
    for i in range(390):
        if i == 230:
            runtime.move_to(CursorCell(8, 22))
        runtime.update(1 / 30)
        image = Image.new("RGBA", (256, 256), "#191c25")
        for visual in runtime.visuals():
            if visual.cell == CursorCell(8, 12):
                image.alpha_composite(compositor.frame(visual))
        frames.append(image.convert("RGB"))
    frames[0].save(
        OUTPUT / "visit.webp",
        save_all=True,
        append_images=frames[1:],
        duration=33,
        loop=0,
        lossless=True,
    )
    print(OUTPUT / "checkpoints.png")


if __name__ == "__main__":
    main()
