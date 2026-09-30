"""Animate the species studies through the shared continuous pose channels."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import create_flower_scene as base
import render_flower_options as art
import study_shedding as study

from flower_cursor.lifecycle import FlowerInstance
from flower_cursor.runtime import CursorCell
from flower_cursor.shedding import SheddingFlower

GROUP_COUNTS = {"daisy": 17, "crocus": 6, "poppy": 4, "dahlia": 45, "orchid": 4}
PREFIXES = {
  "daisy": "Daisy ray ",
  "crocus": "Crocus tepal ",
  "poppy": "Poppy petal ",
  "dahlia": "Dahlia ring ",
}


def mature_pose():
  flower = FlowerInstance.bud(1, CursorCell(0, 0))
  flower.update(8)
  return flower.pose


def mesh_world(obj):
  bpy.ops.object.select_all(action="DESELECT")
  obj.select_set(True)
  bpy.context.view_layer.objects.active = obj
  if obj.type == "CURVE":
    bpy.ops.object.convert(target="MESH")
  matrix = obj.matrix_world.copy()
  for vertex in obj.data.vertices:
    vertex.co = matrix @ vertex.co
  obj.matrix_world = Matrix.Identity(4)
  return obj


def head_point(point, origin, destination, opening, scale):
  offset = point - origin
  axial = art.NORMAL * offset.dot(art.NORMAL)
  radial = offset - axial
  folded = radial * (0.10 + 0.90 * opening)
  folded += axial + art.NORMAL * (radial.length * 0.65 * (1 - opening))
  return destination + folded * scale


def wilt_materials(objects, vitality):
  amount = base.smoothstep((0.22 - vitality) / 0.195)
  if amount <= 0:
    return
  brown = (0.14, 0.08, 0.035)
  materials = {mat for obj in objects for mat in obj.data.materials if mat}
  for mat in materials:
    for node in mat.node_tree.nodes:
      if node.type == "BSDF_PRINCIPLED" and not node.inputs["Base Color"].is_linked:
        color = node.inputs["Base Color"].default_value
        color[:3] = [c * (1 - amount) + b * amount for c, b in zip(color[:3], brown)]
      elif node.type == "VALTORGB":
        for element in node.color_ramp.elements:
          color = element.color
          color[:3] = [c * (1 - amount) + b * amount for c, b in zip(color[:3], brown)]


def build_scene(kind, pose, path):
  if kind not in GROUP_COUNTS:
    raise ValueError(f"unknown animated species: {kind}")
  base.clear_scene()
  bpy.data.orphans_purge(do_recursive=True)
  reference = mature_pose()
  values = study.pose_channels(pose)
  source_origin = study.head_origin(reference)
  stalk_pose = replace(pose, stem=pose.stem * 0.955) if kind == "crocus" else pose
  origin = study.head_origin(stalk_pose)
  stem_mat = art.leaves(kind, reference)
  leaves = list(bpy.context.scene.objects)
  leaf_rot = base.smoothstep((0.24 - pose.vitality) / 0.21)
  leaf_size = base.clamp01(values["leaf_unfurl"]) * pose.stem * (1 - leaf_rot)
  source_stem = [
    study.stem_point(t / 200, reference.stem, reference.stem_bend, reference.head_lean) for t in range(201)
  ]
  for obj in leaves:
    if kind == "dahlia":
      # The node index is read from the property the art module stamps on
      # each leaf part, not from the name: the lateral leaflets end in a
      # branch token, so the old name[-2] lookup read the sign instead and
      # lifted every node-0 lateral leaflet off its own rachis.
      height = (0.32, 0.57)[obj["leaf_node"]]
      anchor = study.stem_point(height, reference.stem, reference.stem_bend, reference.head_lean)
    else:
      anchor = Vector(obj["leaf_anchor"])
      height = min(range(201), key=lambda i: (source_stem[i] - anchor).length_squared) / 200
    destination = study.stem_point(height, stalk_pose.stem, pose.stem_bend, pose.head_lean, values["collapse"])
    reference_attachment = study.stem_point(height, reference.stem, reference.stem_bend, reference.head_lean)
    obj = mesh_world(obj)
    obj["layer"] = "body"
    for vertex in obj.data.vertices:
      offset = vertex.co - reference_attachment
      droop = Vector((0, 0, -values["droop"] * 0.24 * offset.length_squared))
      vertex.co = destination + (offset + droop) * leaf_size
  base.stem_point = study.stem_point
  base.add_stem(stem_mat, stalk_pose.stem, pose.stem_bend, pose.head_lean, values["collapse"])
  stem = bpy.data.objects["Living stem"]
  stem["layer"] = "body"
  stem_rot = base.smoothstep((0.07 - pose.vitality) / 0.05)
  stem.data.bevel_depth = 0.038 * (1 - stem_rot) ** 1.2
  before = set(bpy.context.scene.objects)
  getattr(art, kind)(source_origin)
  bloom = [obj for obj in bpy.context.scene.objects if obj not in before]
  opening = base.smoothstep(values["petal_open"])
  head_scale = 0.28 + 0.72 * base.smoothstep(pose.stem)
  rot = base.smoothstep((0.10 - pose.vitality) / 0.08)
  index = 0
  contact = (
    source_origin + art.NORMAL * {"daisy": 0.13, "crocus": 0.60, "poppy": 0.19, "dahlia": 0.36, "orchid": 0.15}[kind]
  )
  for obj in bloom:
    layer = "head"
    if kind != "orchid" and obj.name.startswith(PREFIXES[kind]):
      layer = f"petal_{index}"
      index += 1
    elif kind == "orchid":
      orchid_groups = {
        "Striped dorsal sepal": 0,
        "Orchid lateral petal -1": 1,
        "Orchid lateral petal 1": 2,
        "Hollow slipper pouch": 3,
        "Rolled slipper rim": 3,
      }
      if obj.name in orchid_groups:
        layer = f"petal_{orchid_groups[obj.name]}"
    obj = mesh_world(obj)
    if kind == "crocus" and obj.name == "Crocus anther 0":
      contact = sum((v.co for v in obj.data.vertices), Vector()) / len(obj.data.vertices)
    elif kind == "orchid" and obj.name == "Orchid staminode":
      contact = sum((v.co for v in obj.data.vertices), Vector()) / len(obj.data.vertices)
    obj["layer"] = layer
    scale = head_scale * (1 - rot if layer == "head" else 1)
    for vertex in obj.data.vertices:
      vertex.co = head_point(vertex.co, source_origin, origin, opening, scale)
  if kind != "orchid" and index != GROUP_COUNTS[kind]:
    raise ValueError(f"{kind}: expected {GROUP_COUNTS[kind]} petal groups, got {index}")
  objects = [obj for obj in bpy.context.scene.objects if obj.type in {"MESH", "CURVE"}]
  wilt_materials(objects, pose.vitality)
  base.add_camera()
  base.add_lights()
  scene = base.configure_render(path)
  study.expand_canvas(scene)
  scene.render.threads_mode = "FIXED"
  scene.render.threads = 4
  contact = head_point(contact, source_origin, origin, opening, head_scale)
  if kind in {"dahlia", "orchid"}:
    camera_up = scene.camera.rotation_euler.to_quaternion() @ Vector((0, 1, 0))
    for obj in bloom:
      for vertex in obj.data.vertices:
        vertex.co += camera_up * ((vertex.co - origin).dot(camera_up) * 0.125)
    contact += camera_up * ((contact - origin).dot(camera_up) * 0.125)
  return scene, contact


def main():
  output = Path(os.environ.get("VARIETY_OUTPUT", "/tmp/flower-variety-pilot"))
  output.mkdir(parents=True, exist_ok=True)
  selected = os.environ.get("VARIETY_SPECIES", ",".join(GROUP_COUNTS)).split(",")
  flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)))
  flower.update(0.20)
  checkpoints = [("growth", flower.pose)]
  flower.update(8)
  checkpoints.append(("idle", flower.pose))
  flower.begin_withering()
  flower.update(0.55)
  checkpoints.append(("wilt", flower.pose))
  for kind in selected:
    for name, pose in checkpoints:
      scene, _ = build_scene(kind, pose, output / f"{kind}-{name}.png")
      scene.render.resolution_x = scene.render.resolution_y = 1024
      if name == "idle":
        bpy.ops.wm.save_as_mainfile(filepath=str(output / f"{kind}.blend"))
      bpy.ops.render.render(write_still=True)
      print(f"VARIETY PILOT {kind} {name}", flush=True)


if __name__ == "__main__":
  main()
