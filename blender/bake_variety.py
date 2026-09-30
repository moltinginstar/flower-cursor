"""Bake the newer species into the layered runtime format."""

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
import create_variety_scene as variety
import study_shedding as study


def bake(kind, output, selected):
  output.mkdir(parents=True, exist_ok=True)
  count = variety.GROUP_COUNTS[kind]
  records = []
  for name, pose in baseline.poses():
    if selected and name not in selected:
      continue
    scene, contact = variety.build_scene(kind, pose, output / f"{name}_complete.png")
    bpy.context.view_layer.update()
    objects = [obj for obj in scene.objects if obj.type in {"MESH", "CURVE"}]
    head = world_to_camera_view(scene, scene.camera, contact)
    root = world_to_camera_view(scene, scene.camera, Vector())
    quaternion = scene.camera.rotation_euler.to_quaternion()
    right = quaternion @ Vector((1, 0, 0)) / scene.camera.data.ortho_scale
    down = quaternion @ Vector((0, -1, 0)) / scene.camera.data.ortho_scale
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
    for group in ("complete", "body", "head", *(f"petal_{i}" for i in range(count))):
      members = objects if group == "complete" else [o for o in objects if o["layer"] == group]
      vertices = [o.matrix_world @ v.co for o in members if o.type == "MESH" for v in o.data.vertices]
      center = sum(vertices, Vector()) / len(vertices) if vertices else study.head_origin(pose)
      record["centers"][group] = list(center)
      record["depths"][group] = world_to_camera_view(scene, scene.camera, center).z
      path = output / f"{name}_{group}.png"
      record["layers"][group] = path.name
      if not path.exists():
        for obj in objects:
          obj.hide_render = obj not in members
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
    records.append(record)
    print(f"VARIETY LAYERED {kind} {name} ({len(records)}/135)", flush=True)
  if not records:
    raise ValueError(f"no requested {kind} poses matched")
  order = list(range(1, count, 2)) + list(range(0, count, 2))
  support = 0.0015 if kind in {"crocus", "orchid"} else 0.004
  manifest = {
    "species": kind,
    "shedding_groups": [{"id": f"petal_{i}", "layer": f"petal_{i}", "order": i} for i in range(count)],
    "shedding_order": order,
    "landing_support_bounds": {"x": [-support, support], "y": [-support, support]},
    "poses": records,
    "root_anchor": [root.x, 1 - root.y],
    "projection_right": list(right),
    "projection_down": list(down),
  }
  temporary = output / "manifest.tmp"
  temporary.write_text(json.dumps(manifest, indent=2) + "\n")
  temporary.replace(output / "manifest.json")


def main():
  root = Path(os.environ.get("VARIETY_OUTPUT", "/tmp/flower-variety-atlas"))
  selected = set(filter(None, os.environ.get("VARIETY_POSES", "").split(",")))
  for kind in os.environ.get("VARIETY_SPECIES", ",".join(variety.GROUP_COUNTS)).split(","):
    bake(kind, root / kind, selected)


if __name__ == "__main__":
  main()
