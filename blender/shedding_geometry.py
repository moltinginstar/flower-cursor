"""The wilting stalk and the canvas framing every bake shares."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import anemone_scene as anemone

from flower_cursor.lifecycle import FlowerPose
from flower_cursor.stalk_motion import wilt_point, wilt_progress

ORIGINAL_STEM_POINT = anemone.stem_point
# Stills such as the README banner want the same scenes far larger than the
# runtime's 512px canvas, and only the whole flower: a scale multiplies the
# render size without moving the camera, and a layer list skips the rest.
BAKE_SCALE = int(os.environ.get("FLOWER_BAKE_SCALE", "1"))
BAKE_LAYERS = set(filter(None, os.environ.get("FLOWER_BAKE_LAYERS", "").split(",")))


def bakes(layer):
  return not BAKE_LAYERS or layer in BAKE_LAYERS


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
  values = anemone.channels(pose.stem, pose.bloom, pose.vitality, 1.0 - pose.vitality)
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


def browned(color, amount, brown):
  """*color* mixed *amount* of the way toward *brown*, keeping its alpha."""
  mixed = tuple(c * (1.0 - amount) + b * amount for c, b in zip(color[:3], brown))
  return mixed + tuple(color[3:])


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
  scene.render.resolution_x *= BAKE_SCALE
  scene.render.resolution_y *= BAKE_SCALE
  for obj in scene.objects:
    if obj.name.startswith("Fallen_petal_") and obj.scale.x > 0.001:
      for vertex in obj.data.vertices:
        point = world_to_camera_view(scene, camera, obj.matrix_world @ vertex.co)
        assert 0.01 < point.x < 0.99 and 0.01 < point.y < 0.99, (obj.name, tuple(point))
