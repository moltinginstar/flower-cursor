"""Decode real renderer transmissions into viewport-sized checkpoints, not screenshots."""

import argparse
import base64
import fcntl
import io
import json
import os
import random
import re
import struct
import sys
import termios
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flower_cursor.geometry import ViewportGeometry
from flower_cursor.graphics_renderer import FlowerRenderer
from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST


def advance(runtime, seconds):
  for _ in range(round(seconds * 60)):
    runtime.update(1 / 60)


def decode(output, viewport):
  canvas = Image.new("RGBA", (viewport.width, viewport.height))
  cursor = (0, 0)
  parameters = {}
  payload = b""
  placements = []
  layers = []
  pattern = rb"\x1b\[(\d+);(\d+)H|\x1b_G([^;]*);(.*?)\x1b\\"
  for match in re.finditer(pattern, output, re.S):
    row, column, header, chunk = match.groups()
    if row is not None:
      cw, ch = viewport.cell_size
      cursor = (int(column) - 1) * cw, (int(row) - 1) * ch
      continue
    fields = dict(item.split("=", 1) for item in header.decode().split(","))
    if fields.get("a") == "T":
      parameters = fields
      payload = b""
    payload += chunk
    if fields.get("m", "0") == "0" and parameters:
      image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
      x = cursor[0] + int(parameters.get("X", "0"))
      y = cursor[1] + int(parameters.get("Y", "0"))
      z, image_id = int(parameters.get("z", "0")), int(parameters["i"])
      layers.append((z, image_id, image, (x, y)))
      placements.append(
        {
          "id": int(parameters["p"]),
          "image_id": image_id,
          "z": z,
          "origin": [x, y],
          "size": image.size,
        }
      )
      parameters = {}
  for _, _, image, origin in sorted(layers, key=lambda layer: layer[:2]):
    canvas.alpha_composite(image, origin)
  return canvas, placements


def render_routes(viewport, output, seeds):
  output.mkdir(parents=True, exist_ok=True)
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  strip = Image.new("RGB", (240 * 6, 240 * len(seeds)), "#191c25")
  maps = Image.new("RGB", (640 * 2, 420 * ((len(seeds) + 1) // 2)), "#191c25")
  records = []
  for row, seed in enumerate(seeds):
    runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(seed))
    cell = CursorCell(viewport.rows // 2, viewport.columns // 2)
    layout = FlowerRenderer(runtime.pose_library, lambda _: None, canvas_scale=2).layout
    runtime.set_geometry(viewport, layout)
    runtime.move_to(cell)
    advance(runtime, 10)
    bee = runtime.bee
    record = {"seed": seed, "source": [bee.x, bee.y], "samples": [], "checkpoints": []}
    stages = ["approach", "curve", "touchdown", "settled", "lift", "departure"]
    captured = set()
    backdrop = None
    for _ in range(1800):
      runtime.update(1 / 60)
      record["samples"].append([bee.age, bee.x, bee.y, bee.pose.heading, bee.departure])
      if not bee.visible:
        break
      stage = None
      if bee.departure is None:
        if bee.age >= bee.approach_time + 0.5:
          stage = "settled"
        elif bee.age >= bee.touchdown_time:
          stage = "touchdown"
        elif bee.age >= bee.hover_time * 0.8:
          stage = "curve"
        elif bee.age >= bee.hover_time * 0.35:
          stage = "approach"
      elif bee.departure >= bee.departure_time * 0.65:
        stage = "departure"
      elif bee.departure >= 0.20:
        stage = "lift"
      if stage is None or stage in captured:
        continue
      captured.add(stage)
      writes = []
      renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor, canvas_scale=2)
      renderer.render(runtime.visuals(), cell, viewport=viewport)
      stream = b"".join(writes)
      image, placements = decode(stream, viewport)
      backdrop = Image.new("RGBA", image.size, "#191c25")
      backdrop.alpha_composite(image)
      name = f"seed-{seed:03d}-{stage}"
      backdrop.convert("RGB").save(output / f"{name}.png")
      (output / f"{name}.apc").write_bytes(stream)
      x, y = round(bee.x), round(bee.y)
      tile = backdrop.crop((x - 60, y - 50, x + 60, y + 70)).convert("RGB")
      tile = tile.resize((240, 240), Image.Resampling.NEAREST)
      ImageDraw.Draw(tile).text((6, 6), f"seed {seed} / {stage} / 2x", fill="white")
      strip.paste(tile, (stages.index(stage) * 240, row * 240))
      record["checkpoints"].append({"stage": stage, "pose": asdict(bee.pose), "placements": placements})
    record["destination"] = [bee.x, bee.y]
    record["landing_offset"] = bee.landing_offset
    record["timings"] = {
      name: getattr(bee, name)
      for name in (
        "hover_time",
        "landing_time",
        "touchdown_time",
        "approach_time",
        "rest_time",
        "departure_time",
      )
    }
    records.append(record)
    tile = Image.new("RGB", (640, 420), "#191c25")
    draw = ImageDraw.Draw(tile)
    sx, sy = 560 / viewport.width, 330 / viewport.height
    draw.rectangle((40, 45, 600, 375), outline="#6b7482")
    for departing, color in ((False, "#b8bddd"), (True, "#d5ae71")):
      points = [(40 + p[1] * sx, 45 + p[2] * sy) for p in record["samples"] if (p[4] is not None) == departing]
      if len(points) > 1:
        draw.line(points, fill=color, width=2)
    for point, color in ((record["source"], "#b8bddd"), (record["destination"], "#d5ae71")):
      x, y = 40 + point[0] * sx, 45 + point[1] * sy
      draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
    x, y = bee.head
    draw.ellipse((40 + x * sx - 4, 45 + y * sy - 4, 40 + x * sx + 4, 45 + y * sy + 4), fill="#e993a2")
    draw.text(
      (16, 10),
      f"seed {seed} / measured {viewport.width}x{viewport.height} / blue entry, amber exit",
      fill="white",
    )
    draw.text(
      (16, 397),
      "Diagnostic trajectory from actual runtime; dots mark offscreen endpoints",
      fill="white",
    )
    maps.paste(tile, (row % 2 * 640, row // 2 * 420))
  strip.save(output / "seeded-route-strip.png")
  maps.save(output / "seeded-trajectories.png")
  (output / "routes.json").write_text(json.dumps(records, indent=2) + "\n")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--tty", type=Path, required=True, help="live Kitty PTY; only reads winsize")
  parser.add_argument("--output", type=Path, default=Path("/tmp/flower-cursor-bee-viewport"))
  parser.add_argument("--seed", type=int, default=31)
  parser.add_argument("--routes", default="0,1,3,8,12,13")
  args = parser.parse_args()
  fd = os.open(args.tty, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
  try:
    measured = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
  finally:
    os.close(fd)
  viewport = ViewportGeometry(*measured)
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(args.seed))
  cell = CursorCell(min(8, viewport.rows - 1), min(12, viewport.columns - 1))
  runtime.move_to(cell)
  layout = FlowerRenderer(runtime.pose_library, lambda data: None, canvas_scale=2).layout
  runtime.set_geometry(viewport, layout)
  args.output.mkdir(parents=True, exist_ok=True)
  records = {
    "viewport": asdict(viewport),
    "tty": str(args.tty),
    "seed": args.seed,
    "checkpoints": {},
  }
  zooms = []

  def checkpoint(name):
    writes = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append, compositor=compositor, canvas_scale=2)
    renderer.render(runtime.visuals(), runtime.active.flower.cell, viewport=viewport)
    output = b"".join(writes)
    canvas, placements = decode(output, viewport)
    backdrop = Image.new("RGBA", canvas.size, "#191c25")
    backdrop.alpha_composite(canvas)
    backdrop.convert("RGB").save(args.output / f"{name}.png")
    (args.output / f"{name}.apc").write_bytes(output)
    records["checkpoints"][name] = {
      "bee": asdict(runtime.bee.pose),
      "visible": runtime.bee.visible,
      "velocity": [runtime.bee.vx, runtime.bee.vy],
      "head": runtime.bee.head,
      "landing": runtime.bee.landing,
      "landing_offset": runtime.bee.landing_offset,
      "placements": placements,
    }
    bee = next((v for v in runtime.visuals() if isinstance(v, BeeVisual)), None)
    if bee is not None:
      x, y = round(bee.bee.x), round(bee.bee.y)
      patch = backdrop.crop((x - 70, y - 55, x + 70, y + 55)).convert("RGB")
      patch = patch.resize((560, 440), Image.Resampling.NEAREST)
      ImageDraw.Draw(patch).text((8, 8), name + " / 4x diagnostic crop", fill="white")
      zooms.append(patch)

  advance(runtime, 10)
  checkpoint("01-outside")
  for _ in range(240):
    runtime.update(1 / 60)
    # The bee is only among the visuals while it is visible, and a faded or
    # off-screen frame has an empty alpha channel. Either one means there is
    # nothing on screen to have entered yet, so keep looking rather than
    # dying with StopIteration or a None subscript.
    visual = next((v for v in runtime.visuals() if isinstance(v, BeeVisual)), None)
    if visual is None:
      continue
    image = compositor.frame(visual)
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
      continue
    left = round(runtime.bee.x - image.width / 2) + bounds[0]
    top = round(runtime.bee.y - image.height / 2) + bounds[1]
    right = round(runtime.bee.x - image.width / 2) + bounds[2]
    bottom = round(runtime.bee.y - image.height / 2) + bounds[3]
    if left < viewport.width - 4 and right > 4 and top < viewport.height - 4 and bottom > 4:
      break
  checkpoint("02-edge-entrance")
  advance(runtime, 0.8)
  checkpoint("03-mid-flight")
  advance(runtime, max(0, runtime.bee.approach_time + 0.7 - runtime.bee.age))
  checkpoint("04-landing")
  runtime.move_to(CursorCell(cell.row, min(viewport.columns - 1, cell.column + 10)))
  checkpoint("05-interruption-boundary")
  advance(runtime, 0.08)
  checkpoint("06-mature-wither-lift")
  advance(runtime, 0.15)
  checkpoint("07-partial-wither-lift")
  runtime.move_to(cell)
  advance(runtime, 0.10)
  checkpoint("08-return-during-departure")
  for _ in range(600):
    runtime.update(1 / 120)
    if not runtime.bee.visible or (
      runtime.bee.x < 6
      or runtime.bee.x > viewport.width - 6
      or runtime.bee.y < 6
      or runtime.bee.y > viewport.height - 6
    ):
      break
  checkpoint("09-edge-departure")
  while runtime.bee.visible:
    runtime.update(1 / 120)
  checkpoint("10-gone")
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(args.seed))
  runtime.move_to(cell)
  runtime.set_geometry(viewport, layout)
  advance(runtime, 10)
  advance(runtime, (runtime.bee.landing_time + runtime.bee.touchdown_time) / 2)
  checkpoint("11-landing-approach")
  runtime.move_to(CursorCell(cell.row, min(viewport.columns - 1, cell.column + 2)))
  checkpoint("12-abort-boundary")
  advance(runtime, 0.12)
  runtime.move_to(cell)
  advance(runtime, 0.08)
  checkpoint("13-abort-and-partial-flower-wither")
  sheet = Image.new("RGB", (560 * 3, 440 * ((len(zooms) + 2) // 3)), "#191c25")
  for index, image in enumerate(zooms):
    sheet.paste(image, ((index % 3) * 560, (index // 3) * 440))
  sheet.save(args.output / "detail-4x.png")
  (args.output / "geometry.json").write_text(json.dumps(records, indent=2) + "\n")
  render_routes(viewport, args.output / "routes", [int(s) for s in args.routes.split(",")])
  print(args.output)
  print(json.dumps(records["viewport"]))


if __name__ == "__main__":
  main()
