"""Bake the anemone's layers for the runtime."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import shedding_geometry as geometry
from entry_poses import growth_idle_poses

from flower_cursor.lifecycle import CursorCell, FlowerInstance
from flower_cursor.shedding import SheddingFlower

OUTPUT = Path(os.environ.get("FLOWER_LAYER_DIR", "/tmp/flower-cursor/anemone"))
# Saves the scene of each pose baked, e.g. blender/anemone.blend with FLOWER_LAYER_POSES=idle_00.
SCENE = os.environ.get("FLOWER_SCENE")


def poses():
  yield from growth_idle_poses()
  for label, warmup in (("young", 0.25), ("half", 0.5), ("full", 3.0)):
    flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)))
    flower.update(warmup)
    flower.begin_withering()
    for index in range(25):
      yield f"exit_{label}_{index:02d}", flower.pose
      flower.update(1 / 24)


def group_name(obj):
  if obj.name.startswith("Petal_"):
    return f"petal_{int(obj.name.split('_')[1])}"
  if obj.name.startswith(("Sepal_", "Bud_", "Flower_Center", "Filament_", "Pollen_")):
    return "head"
  return "body"


def main():
  OUTPUT.mkdir(parents=True, exist_ok=True)
  anemone = geometry.anemone
  anemone.RENDER_DIR = OUTPUT
  anemone.stem_point = geometry.stem_point
  selection = set(filter(None, os.environ.get("FLOWER_LAYER_POSES", "").split(",")))
  records = []
  for name, pose in poses():
    if selection and name not in selection:
      continue
    anemone.POSES[name] = geometry.pose_channels(pose)
    scene = anemone.build_scene(name)
    rot = anemone.smoothstep((0.10 - pose.vitality) / 0.08)
    stem_rot = anemone.smoothstep((0.07 - pose.vitality) / 0.05)
    leaf_rot = anemone.smoothstep((0.24 - pose.vitality) / 0.21)
    browning = anemone.smoothstep((0.22 - pose.vitality) / 0.195)
    objects = [o for o in scene.objects if o.type in {"MESH", "CURVE"}]
    origin = geometry.head_origin(pose)
    for obj in objects:
      if obj.type == "CURVE":
        obj.data.bevel_depth *= (1.0 - stem_rot) ** 1.2
      if group_name(obj) == "head":
        geometry.scale_mesh(obj, origin, 1.0 - rot)
      elif obj.name.startswith("Leaf_"):
        obj.scale *= 1.0 - leaf_rot
    for material_name in ("Living stem", "Leaf surface", "Sepal green"):
      mat = bpy.data.materials.get(material_name)
      if mat is not None:
        color = tuple(mat.diffuse_color)
        tinted = geometry.browned(color, browning, (0.12, 0.075, 0.035))
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = tinted
    geometry.expand_canvas(scene)
    bpy.context.view_layer.update()
    if SCENE:
      anemone.save_scene(Path(SCENE).resolve())
    head = world_to_camera_view(scene, scene.camera, origin)
    root = world_to_camera_view(scene, scene.camera, Vector())
    quaternion = scene.camera.rotation_euler.to_quaternion()
    right = quaternion @ Vector((1, 0, 0)) / scene.camera.data.ortho_scale
    down = quaternion @ Vector((0, -1, 0)) / scene.camera.data.ortho_scale
    record = {
      "name": name,
      "file": f"{name}_body.png",
      **anemone.POSES[name],
      **asdict(pose),
      "collapse": pose.collapse,
      "petal_curl": pose.petal_curl,
      "retraction": pose.retraction,
      "head": [head.x, 1 - head.y],
      "world_head": list(origin),
      "layers": {},
      "centers": {},
      "depths": {},
    }
    for group in ("complete", "body", "head", *(f"petal_{i}" for i in range(6))):
      members = objects if group == "complete" else [o for o in objects if group_name(o) == group]
      vertices = [o.matrix_world @ v.co for o in members if o.type == "MESH" for v in o.data.vertices]
      center = sum(vertices, Vector()) / len(vertices) if vertices else origin
      record["centers"][group] = list(center)
      record["depths"][group] = world_to_camera_view(scene, scene.camera, center).z
      path = OUTPUT / f"{name}_{group}.png"
      record["layers"][group] = path.name
      if not path.exists() and geometry.bakes(group):
        for obj in objects:
          obj.hide_render = obj not in members
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
    records.append(record)
    print(f"anemone {name}", flush=True)
  if not records:
    raise ValueError(
      f"FLOWER_LAYER_POSES={','.join(sorted(selection))} matched no pose; "
      f"known poses are {', '.join(name for name, _ in poses())}"
    )
  # A selective bake merges into the manifest, so the other poses stay reachable.
  destination = OUTPUT / "manifest.json"
  previous = json.loads(destination.read_text()) if selection and destination.exists() else {}
  rebaked = {record["name"]: record for record in records}
  merged = [rebaked.pop(pose["name"], pose) for pose in previous.get("poses", [])]
  merged += [record for record in records if record["name"] in rebaked]
  manifest = {
    "species": "anemone",
    "shedding_groups": [{"id": f"petal_{i}", "layer": f"petal_{i}", "order": i} for i in range(6)],
    "shedding_order": [1, 4, 0, 5, 2, 3],
    "poses": merged,
    "root_anchor": [root.x, 1 - root.y],
    "projection_right": list(right),
    "projection_down": list(down),
  }
  temporary = OUTPUT / "manifest.tmp"
  temporary.write_text(json.dumps(manifest, indent=2) + "\n")
  temporary.replace(destination)


if __name__ == "__main__":
  main()
