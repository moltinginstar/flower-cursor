"""Render the experimental shedding model without replacing production assets."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import create_flower_scene as authored

from flower_cursor.idle_motion import IDLE_ANGULAR_SPEED, idle_pose
from flower_cursor.lifecycle import CursorCell, FlowerInstance, FlowerPose
from flower_cursor.shedding import PetalFlight, SheddingFlower, petal_decay, petal_displacement
from flower_cursor.stalk_motion import wilt_point, wilt_progress

OUTPUT = Path(os.environ.get("FLOWER_STUDY_DIR", "/tmp/flower-shedding-study"))
ORIGINAL_STEM_POINT = authored.stem_point


def stem_point(t, stem_length, bend, head_lean, collapse=0.0):
  """Withdraw authored length without changing the lifecycle's retained stem channel."""
  stem_length *= 1.0 - collapse
  standing = ORIGINAL_STEM_POINT(t, stem_length, bend, head_lean, 0.0)
  if collapse <= 0 or t <= 0:
    return standing
  side, forward, height = wilt_point(t, collapse)
  scale = height / t
  offset = Vector((0.866 * side + 0.5 * forward, 0.5 * side - 0.866 * forward, 0.0))
  return standing * scale + offset * (2.16 * stem_length)


def pose_channels(pose: FlowerPose):
  values = authored.channels(pose.stem, pose.bloom, pose.vitality, 1.0 - pose.vitality)
  for name in values:
    if hasattr(pose, name):
      values[name] = getattr(pose, name)
  values["collapse"] = wilt_progress(pose.vitality)
  values["petal_curl"] *= 0.30
  values["retraction"] = 0.0
  return values


def head_origin(pose):
  tip = stem_point(1.0, pose.stem, pose.stem_bend, pose.head_lean, wilt_progress(pose.vitality))
  return Vector((tip.x, tip.y - 0.03 - 0.025 * math.sin(math.pi * 1.4), tip.z))


def scale_mesh(obj, origin, amount):
  inverse = obj.matrix_world.inverted()
  for vertex in obj.data.vertices:
    world = obj.matrix_world @ vertex.co
    vertex.co = inverse @ (origin + (world - origin) * amount)


def capture_petal(pose, index, growth):
  authored.POSES["study_capture"] = pose_channels(pose)
  authored.build_scene("study_capture")
  obj = bpy.data.objects[f"Petal_{index:02d}"]
  scale_mesh(obj, head_origin(pose), growth)
  vertices = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
  center = sum(vertices, Vector()) / len(vertices)
  faces = [tuple(p.vertices) for p in obj.data.polygons]
  color = tuple(obj.data.materials[0].diffuse_color)
  return vertices, center, faces, color


def capture_flight(flight: PetalFlight):
  vertices, center, faces, color = capture_petal(flight.pose, flight.index, flight.growth)
  _, previous, _, _ = capture_petal(flight.previous_pose, flight.index, flight.growth)
  velocity = (center - previous) / flight.sample_duration
  return [tuple(v - center) for v in vertices], center, faces, color, velocity


def browned(color, amount, brown):
  """*color* mixed *amount* of the way toward *brown*, keeping its alpha.

  Dying tissue is the same operation wherever it happens - a fallen petal
  here, a withering stem in `bake_layered_flower` - and the two looked
  different only because each spelled the mix out again.
  """
  mixed = tuple(c * (1.0 - amount) + b * amount for c, b in zip(color[:3], brown))
  return mixed + tuple(color[3:])


def add_flight(flight, captured):
  vertices, center, faces, color, velocity = captured
  mesh = bpy.data.meshes.new(f"Fallen_petal_{flight.identifier}")
  mesh.from_pydata(vertices, [], faces)
  mesh.update()
  obj = bpy.data.objects.new(mesh.name, mesh)
  bpy.context.collection.objects.link(obj)
  age = flight.age
  decay = petal_decay(age)
  obj.scale = (1.0 - decay,) * 3
  obj.location = center + Vector(petal_displacement(flight.index, age, tuple(velocity)))
  obj.rotation_euler = (
    age * (2.0 + flight.index * 0.25),
    age * (1 if flight.index % 2 else -1) * 5.0,
    age * 0.8 * math.sin(flight.index),
  )
  color = browned(color, decay, (0.16, 0.07, 0.025))
  mat = authored.material(mesh.name, color, 0.48, transmission=0.16, subsurface=0.075)
  mesh.materials.append(mat)
  for polygon in mesh.polygons:
    polygon.use_smooth = True
  obj.modifiers.new("Thin petal tissue", "SOLIDIFY").thickness = 0.006


def expand_canvas(scene):
  bpy.context.view_layer.update()
  before = world_to_camera_view(scene, scene.camera, Vector())
  camera = scene.camera
  scale = camera.data.ortho_scale
  camera.location += camera.rotation_euler.to_quaternion() @ Vector((0.0, -scale / 2, 0.0))
  camera.data.ortho_scale *= 2
  scene.render.resolution_x *= 2
  scene.render.resolution_y *= 2
  bpy.context.view_layer.update()
  after = world_to_camera_view(scene, camera, Vector())
  assert abs(after.x * 512 - (before.x * 256 + 128)) < 0.001
  assert abs((1 - after.y) * 512 - (1 - before.y) * 256) < 0.001
  for obj in scene.objects:
    if obj.name.startswith("Fallen_petal_") and obj.scale.x > 0.001:
      for vertex in obj.data.vertices:
        point = world_to_camera_view(scene, camera, obj.matrix_world @ vertex.co)
        assert 0.01 < point.x < 0.99 and 0.01 < point.y < 0.99, (obj.name, tuple(point))


def render_frame(name, flower, captured):
  for flight in flower.flights:
    if flight.identifier not in captured:
      captured[flight.identifier] = capture_flight(flight)
  pose = flower.pose
  authored.POSES[name] = pose_channels(pose)
  scene = authored.build_scene(name)
  origin = head_origin(pose)
  rot = authored.smoothstep((0.10 - pose.vitality) / 0.08)
  stem_rot = authored.smoothstep((0.07 - pose.vitality) / 0.05)
  leaf_rot = authored.smoothstep((0.24 - pose.vitality) / 0.21)
  browning = authored.smoothstep((0.22 - pose.vitality) / 0.195)
  for obj in tuple(scene.objects):
    if obj.type == "CURVE":
      obj.data.bevel_depth *= (1.0 - stem_rot) ** 1.2
    if obj.type == "MESH" and obj.name.startswith("Petal_"):
      scale_mesh(obj, origin, flower.petals[int(obj.name.split("_")[1])].value)
    elif obj.type == "MESH" and obj.name.startswith(("Sepal_", "Bud_", "Flower_Center", "Filament_", "Pollen_")):
      scale_mesh(obj, origin, flower.head.value * (1.0 - rot))
    elif obj.name.startswith("Leaf_"):
      obj.scale *= 1.0 - leaf_rot
    if obj.type in {"MESH", "CURVE"} and flower.flower.dead:
      obj.hide_render = True
  for material_name in ("Living stem", "Leaf surface", "Sepal green"):
    mat = bpy.data.materials.get(material_name)
    if mat is not None:
      color = tuple(mat.diffuse_color)
      brown = (0.12, 0.075, 0.035)
      tinted = tuple(c * (1 - browning) + b * browning for c, b in zip(color[:3], brown))
      mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        *tinted,
        color[3],
      )
  for flight in flower.flights:
    add_flight(flight, captured[flight.identifier])
  expand_canvas(scene)
  scene.render.filepath = str(OUTPUT / f"{name}.png")
  bpy.ops.render.render(write_still=True)


def main():
  OUTPUT.mkdir(parents=True, exist_ok=True)
  authored.RENDER_DIR = OUTPUT
  for checkpoint in ("bloom_13", "mature_idle_06", "wilt_12"):
    scene = authored.build_scene(checkpoint)
    scene.render.filepath = str(OUTPUT / f"baseline_{checkpoint}.png")
    bpy.ops.render.render(write_still=True)
  mature = FlowerInstance.bud(1, CursorCell(0, 0))
  mature.update(8.0)
  mature.idle_phase = 0.0
  idle_records = []
  for index in range(24):
    name = f"study_idle_{index:02d}"
    pose = idle_pose(mature.pose, index * math.tau / (24 * IDLE_ANGULAR_SPEED))
    authored.POSES[name] = pose_channels(pose)
    scene = authored.build_scene(name)
    scene.render.filepath = str(OUTPUT / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    idle_records.append({"name": name, "file": f"{name}.png", **authored.POSES[name]})
  (OUTPUT / "idle-manifest.json").write_text(json.dumps({"poses": idle_records}, indent=2) + "\n")
  authored.stem_point = stem_point
  full = os.environ.get("FLOWER_STUDY_FULL", "0") == "1"
  frames = range(76) if full else (0, 6, 12, 18, 24, 30, 36, 45, 54, 60, 75)
  records = []
  for label, warmup, return_frame in (
    ("exit", 3.0, None),
    ("return", 3.0, 9),
    ("partial", 0.3, 6),
  ):
    flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)))
    flower.update(warmup)
    flower.begin_withering()
    captured = {}
    for index in range(max(frames) + 1):
      if index == return_frame:
        flower.restore()
      if index in frames:
        name = f"{label}_{index:03d}"
        render_frame(name, flower, captured)
        records.append(
          {
            "name": name,
            "wall_time": index / 60,
            "stalk_dead": flower.flower.dead,
            "finished": flower.finished,
            "petals": [p.value for p in flower.petals],
            "flights": [{"id": f.identifier, "age": f.age} for f in flower.flights],
          }
        )
        print(f"STUDY {name} wall={index / 60:.3f}s", flush=True)
      flower.update(2 / 60)
  (OUTPUT / "study.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
  main()
