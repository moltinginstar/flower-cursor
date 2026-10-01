"""Bake species-specific cornflower layers without changing the coral atlas."""

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

import bake_layered_flower as baseline
import create_cornflower_scene as cornflower
import study_shedding as study

ORDER = (1, 7, 3, 9, 5, 11, 0, 6, 2, 8, 4, 10, 12, 15, 13, 16, 14, 17)


def main():
  output = Path(os.environ.get("CORNFLOWER_OUTPUT", "/tmp/flower-cornflower-atlas"))
  output.mkdir(parents=True, exist_ok=True)
  selected = set(filter(None, os.environ.get("CORNFLOWER_POSES", "").split(",")))
  records = []
  for name, pose in baseline.poses():
    if selected and name not in selected:
      continue
    scene, contact = cornflower.build_scene(pose, output / f"{name}_complete.png")
    bpy.context.view_layer.update()
    objects = [o for o in scene.objects if o.type in {"MESH", "CURVE"}]
    head = world_to_camera_view(scene, scene.camera, contact)
    root = world_to_camera_view(scene, scene.camera, Vector())
    quaternion = scene.camera.rotation_euler.to_quaternion()
    right = quaternion @ Vector((1, 0, 0)) / scene.camera.data.ortho_scale
    down = quaternion @ Vector((0, -1, 0)) / scene.camera.data.ortho_scale
    origin = study.head_origin(pose)
    record = {
      "name": name,
      "file": f"{name}_body.png",
      **study.pose_channels(pose),
      **asdict(pose),
      "collapse": pose.collapse,
      "petal_curl": pose.petal_curl,
      "retraction": pose.retraction,
      "head": [head.x, 1 - head.y],
      "world_head": list(contact),
      "layers": {},
      "centers": {},
      "depths": {},
    }
    groups = (
      "complete",
      "body",
      "head",
      *(f"petal_{i}" for i in range(cornflower.GROUP_COUNT)),
    )
    for group in groups:
      members = objects if group == "complete" else [o for o in objects if o["layer"] == group]
      vertices = [o.matrix_world @ v.co for o in members if o.type == "MESH" for v in o.data.vertices]
      center = sum(vertices, Vector()) / len(vertices) if vertices else origin
      record["centers"][group] = list(center)
      record["depths"][group] = world_to_camera_view(scene, scene.camera, center).z
      path = output / f"{name}_{group}.png"
      record["layers"][group] = path.name
      if not path.exists() and study.bakes(group):
        for obj in objects:
          obj.hide_render = obj not in members
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
    records.append(record)
    print(f"CORNFLOWER LAYERED {name}", flush=True)
  if not records:
    raise ValueError("no requested cornflower poses matched")
  manifest = {
    "species": "cornflower",
    "shedding_groups": [{"id": f"petal_{i}", "layer": f"petal_{i}", "order": i} for i in range(cornflower.GROUP_COUNT)],
    "shedding_order": list(ORDER),
    "landing_support_bounds": {"x": [-0.008, 0.008], "y": [-0.006, 0.006]},
    "poses": records,
    "root_anchor": [root.x, 1 - root.y],
    "projection_right": list(right),
    "projection_down": list(down),
  }
  temporary = output / "manifest.tmp"
  temporary.write_text(json.dumps(manifest, indent=2) + "\n")
  temporary.replace(output / "manifest.json")


if __name__ == "__main__":
  main()
