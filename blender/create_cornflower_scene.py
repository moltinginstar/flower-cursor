"""Author the cornflower without touching the anemone or bee assets."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import create_flower_scene as base
import study_shedding as study

from flower_cursor.lifecycle import CursorCell, FlowerInstance
from flower_cursor.shedding import SheddingFlower

GROUP_COUNT = 18
NORMAL = Vector((0.33, -0.57, 0.75)).normalized()
RIGHT = Vector((0.866, 0.5, 0.0)).normalized()
ACROSS = NORMAL.cross(RIGHT).normalized()


def mesh_object(name, vertices, faces, materials, layer, shades=None):
  mesh = bpy.data.meshes.new(name)
  mesh.from_pydata(vertices, [], faces)
  mesh.validate()
  mesh.update()
  obj = bpy.data.objects.new(name, mesh)
  bpy.context.collection.objects.link(obj)
  obj["layer"] = layer
  for material in materials:
    mesh.materials.append(material)
  for index, polygon in enumerate(mesh.polygons):
    polygon.use_smooth = True
    if shades:
      polygon.material_index = shades[index] % len(materials)
  return obj


def ribbon(name, start, direction, side, normal, length, width, curl, materials, layer):
  vertices, faces, shades = [], [], []
  rows, columns = 12, 5
  for row in range(rows):
    t = row / (rows - 1)
    profile = math.sin(math.pi * (0.10 + 0.895 * t)) ** 0.8
    center = start + direction * (length * t) + normal * (curl * t * t)
    for column in range(columns):
      across = 2 * column / (columns - 1) - 1
      ridge = 0.012 * math.sin(math.pi * t) * (1 - across * across)
      ripple = 0.008 * math.sin(t * 11 + across) * t * abs(across)
      vertices.append(center + side * (width * profile * across) + normal * (ridge + ripple))
  for row in range(rows - 1):
    for column in range(columns - 1):
      a = row * columns + column
      faces.append((a, a + 1, a + 1 + columns, a + columns))
      shades.append(1 if column in (1, 2) else 0)
  obj = mesh_object(name, vertices, faces, materials, layer, shades)
  obj.modifiers.new("Thin living tissue", "SOLIDIFY").thickness = 0.004
  return obj


def floret(name, start, axis, size, materials, layer, phase):
  side = axis.cross(NORMAL)
  if side.length < 0.1:
    side = RIGHT.copy()
  side.normalize()
  across = axis.cross(side).normalized()
  vertices, faces, shades = [], [], []
  rows, columns = 9, 20
  for row in range(rows):
    t = row / (rows - 1)
    radius = size * (0.019 + 0.059 * t**2.2)
    for column in range(columns):
      angle = math.tau * column / columns + phase
      rib = 1 + 0.065 * math.cos(5 * angle)
      ring = side * math.cos(angle) + across * math.sin(angle)
      vertices.append(start + axis * (0.30 * size * t) + ring * radius * rib)
  for row in range(rows - 1):
    for column in range(columns):
      a, b = row * columns + column, row * columns + (column + 1) % columns
      faces.append((a, b, b + columns, a + columns))
      shades.append(1 if column % 4 == 0 else 0)
  mesh_object(name + "_tube", vertices, faces, materials, layer, shades)
  for lobe in range(5):
    angle = math.tau * lobe / 5 + phase
    radial = side * math.cos(angle) + across * math.sin(angle)
    tangent = -side * math.sin(angle) + across * math.cos(angle)
    direction = (axis * 0.72 + radial * 0.70).normalized()
    ribbon(
      f"{name}_lobe_{lobe}",
      start + axis * (0.29 * size) + radial * (0.066 * size),
      direction,
      tangent,
      radial,
      size * (0.24 + 0.025 * math.sin(lobe * 3 + phase)),
      size * 0.038,
      size * (0.035 + 0.035 * math.sin(phase + lobe)),
      materials,
      layer,
    )


def build_scene(pose, path):
  base.clear_scene()
  values = study.pose_channels(pose)
  origin = study.head_origin(pose)
  opening = base.smoothstep(values["petal_open"])
  head_scale = 0.28 + 0.72 * base.smoothstep(pose.stem)
  vitality = pose.vitality
  browning = base.smoothstep((0.22 - vitality) / 0.195)

  def pigment(name, color, roughness=0.42, transmission=0.12):
    brown = (0.14, 0.08, 0.035)
    shaded = tuple(c * (1 - browning) + b * browning for c, b in zip(color, brown))
    return base.material(name, (*shaded, 1), roughness, transmission=transmission, subsurface=0.065)

  blues = [
    (
      pigment("Cobalt tissue", (0.035, 0.12, 0.64)),
      pigment("Cobalt veins", (0.045, 0.20, 0.85)),
    ),
    (pigment("Azure tissue", (0.045, 0.21, 0.74)), pigment("Azure veins", (0.10, 0.30, 0.90))),
    (pigment("Violet tissue", (0.11, 0.10, 0.54)), pigment("Violet veins", (0.15, 0.19, 0.73))),
  ]
  greens = (
    pigment("Cornflower foliage", (0.10, 0.25, 0.13), transmission=0.05),
    pigment("Cornflower leaf ridge", (0.19, 0.35, 0.19), transmission=0.04),
  )
  bracts = (
    pigment("Involucre olive", (0.15, 0.24, 0.09)),
    pigment("Involucre margins", (0.29, 0.23, 0.10)),
  )
  anther = pigment("Pale blue anthers", (0.39, 0.40, 0.67), transmission=0.02)
  base.stem_point = study.stem_point
  base.add_stem(greens[0], pose.stem, pose.stem_bend, pose.head_lean, values["collapse"])
  stem = bpy.data.objects["Living stem"]
  stem["layer"] = "body"
  stem_rot = base.smoothstep((0.07 - vitality) / 0.05)
  stem.data.bevel_depth = 0.037 * (1 - stem_rot) ** 1.2
  leaf_rot = base.smoothstep((0.24 - vitality) / 0.21)
  for index, (height, sign) in enumerate(((0.34, -1), (0.58, 1), (0.77, -1))):
    unfurl = base.clamp01((values["leaf_unfurl"] - index * 0.12) / (1 - index * 0.12))
    leaf_size = unfurl * pose.stem * (1 - leaf_rot)
    attachment = study.stem_point(height, pose.stem, pose.stem_bend, pose.head_lean, values["collapse"])
    direction = (RIGHT * sign * 0.84 + Vector((0, 0, 0.54 - values["droop"] * 1.1))).normalized()
    ribbon(
      f"Leaf_{index}",
      attachment,
      direction,
      ACROSS,
      NORMAL,
      (0.75 - index * 0.12) * leaf_size,
      (0.095 - index * 0.012) * leaf_size,
      -0.11 * leaf_size,
      greens,
      "body",
    )
  for row in range(3):
    for index in range(10):
      angle = math.tau * (index + row * 0.5) / 10
      radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
      radius = 0.13 + row * 0.025
      start = origin + NORMAL * (-0.19 + row * 0.075) + radial * radius
      ribbon(
        f"Bract_{row}_{index}",
        start,
        NORMAL,
        radial.cross(NORMAL),
        radial,
        0.23,
        0.061,
        -0.046,
        bracts,
        "head",
      )
  for index in range(12):
    angle = math.tau * index / 12 + 0.09 * math.sin(index * 2.4)
    radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
    flutter = 0.025 * math.sin(index * 2.1 + pose.head_lean * 12)
    axis = (radial * (0.86 * opening) + NORMAL * (0.92 - 0.43 * opening + flutter)).normalized()
    start = origin + radial * (0.05 + 0.14 * opening) + NORMAL * 0.04
    size = (0.25 + 0.75 * opening) * (0.93 + 0.07 * math.sin(index * 5))
    floret(f"Outer_{index:02d}", start, axis, size, blues[index % 3], f"petal_{index}", angle)
  for index in range(37):
    angle = index * 2.3999632297
    radius = 0.19 * math.sqrt(index / 37) * (0.25 + 0.75 * opening)
    radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
    start = origin + radial * radius + NORMAL * (0.10 + 0.04 * (1 - index / 37))
    axis = (NORMAL + radial * 0.24 * opening).normalized()
    size = (0.22 + 0.78 * opening) * (0.37 + 0.06 * math.sin(index * 3.1))
    layer = f"petal_{12 + index % 6}"
    floret(f"Disc_{index:02d}", start, axis, size, blues[(index + 2) % 3], layer, angle)
    tip = start + axis * (0.54 * size)
    stalk = base.add_cylinder_between(f"Anther_{index}", tip - axis * 0.035, tip, 0.008, anther, vertices=6)
    stalk["layer"] = layer
  rot = base.smoothstep((0.10 - vitality) / 0.08)
  for obj in bpy.context.scene.objects:
    if obj.type == "MESH" and obj.get("layer") != "body":
      factor = head_scale * (1 - rot if obj.get("layer") == "head" else 1)
      study.scale_mesh(obj, origin, factor)
  base.add_camera()
  base.add_lights()
  scene = base.configure_render(path)
  study.expand_canvas(scene)
  scene.render.threads_mode = "FIXED"
  scene.render.threads = 4
  contact = origin + NORMAL * (0.34 * head_scale)
  return scene, contact


def main():
  output = Path(os.environ.get("CORNFLOWER_OUTPUT", "/tmp/flower-cornflower-pilot"))
  output.mkdir(parents=True, exist_ok=True)
  flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)))
  flower.update(0.20)
  checkpoints = [("growth", flower.pose)]
  flower.update(8)
  checkpoints.append(("idle", flower.pose))
  flower.begin_withering()
  flower.update(0.55)
  checkpoints.append(("wilt", flower.pose))
  for name, pose in checkpoints:
    scene, _ = build_scene(pose, output / f"{name}.png")
    if name == "idle":
      bpy.ops.wm.save_as_mainfile(filepath=str(output / "cornflower.blend"))
    bpy.ops.render.render(write_still=True)
    print(f"CORNFLOWER PILOT {name}", flush=True)


if __name__ == "__main__":
  main()
