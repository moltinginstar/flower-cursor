"""The anemone, built in Blender from the lifecycle's pose channels."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import bpy
from mathutils import Vector


@dataclass(frozen=True, slots=True)
class FlowerStyle:
  name: str
  petal_colors: tuple[tuple[float, float, float, float], ...]
  center_color: tuple[float, float, float, float]
  filament_color: tuple[float, float, float, float]
  stem_color: tuple[float, float, float, float]
  leaf_color: tuple[float, float, float, float]
  leaf_profile: str = "lance"
  petal_count: int = 8
  petal_length: float = 0.82
  petal_width: float = 0.34
  leaf_length: float = 0.78
  leaf_width: float = 0.34


STYLE = FlowerStyle(
  name="anemone",
  petal_colors=(
    (0.78, 0.12, 0.19, 0.94),
    (0.95, 0.29, 0.27, 0.90),
    (0.59, 0.055, 0.12, 0.96),
  ),
  center_color=(0.16, 0.055, 0.028, 1.0),
  filament_color=(1.00, 0.76, 0.18, 1.0),
  stem_color=(0.06, 0.26, 0.09, 1.0),
  leaf_color=(0.10, 0.42, 0.13, 0.94),
  leaf_profile="lance",
  petal_count=6,
  petal_length=0.61,
  petal_width=0.42,
  leaf_length=0.76,
  leaf_width=0.34,
)
# The bakes point this at their output and fill POSES before each build_scene.
RENDER_DIR = Path("/tmp/flower-cursor/anemone")
POSES: dict[str, dict[str, float]] = {}


def clamp01(value: float) -> float:
  return max(0.0, min(1.0, value))


def smoothstep(value: float) -> float:
  value = clamp01(value)
  return value * value * (3.0 - 2.0 * value)


def channels(
  stem: float,
  bloom: float,
  vitality: float,
  droop: float,
  *,
  idle_phase: float = 0.0,
  idle_lean: float = 0.0,
) -> dict[str, float]:
  leaf_unfurl = smoothstep((stem - 0.36) / 0.54) * (0.88 + 0.12 * vitality)
  bud_size = clamp01(0.18 + 0.78 * stem - 0.50 * bloom)
  return {
    "stem": stem,
    "bloom": bloom,
    "vitality": vitality,
    "droop": droop,
    "bud_size": bud_size,
    "leaf_unfurl": leaf_unfurl,
    "petal_open": bloom,
    "head_lean": idle_lean + droop * 0.35,
    "stem_bend": droop,
    "idle_phase": idle_phase,
    "idle_lean": idle_lean,
    "maturity": bloom,
    "collapse": smoothstep((0.50 - vitality) / 0.46),
    "petal_curl": smoothstep((0.86 - vitality) / 0.76),
    "retraction": smoothstep((0.038 - vitality) / 0.018),
  }


def clear_scene() -> None:
  bpy.ops.object.select_all(action="SELECT")
  bpy.ops.object.delete(use_global=False)
  datablocks_to_clean = (
    bpy.data.meshes,
    bpy.data.curves,
    bpy.data.materials,
    bpy.data.cameras,
    bpy.data.lights,
  )
  for datablocks in datablocks_to_clean:
    for datablock in list(datablocks):
      if datablock.users == 0:
        datablocks.remove(datablock)


def material(
  name: str,
  color: tuple[float, float, float, float],
  roughness: float,
  *,
  transmission: float = 0.0,
  subsurface: float = 0.0,
) -> bpy.types.Material:
  mat = bpy.data.materials.new(name)
  mat.diffuse_color = color
  mat.use_nodes = True
  principled = mat.node_tree.nodes.get("Principled BSDF")
  assert principled is not None
  principled.inputs["Base Color"].default_value = color
  principled.inputs["Roughness"].default_value = roughness
  principled.inputs["IOR"].default_value = 1.38
  if "Alpha" in principled.inputs:
    principled.inputs["Alpha"].default_value = color[3]
  if "Transmission Weight" in principled.inputs:
    principled.inputs["Transmission Weight"].default_value = transmission
  if "Subsurface Weight" in principled.inputs:
    principled.inputs["Subsurface Weight"].default_value = subsurface
  if "Subsurface Radius" in principled.inputs:
    principled.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.25)
  if "Coat Weight" in principled.inputs:
    principled.inputs["Coat Weight"].default_value = 0.07
  if "Coat Roughness" in principled.inputs:
    principled.inputs["Coat Roughness"].default_value = 0.32
  if hasattr(mat, "surface_render_method"):
    mat.surface_render_method = "DITHERED"
  elif hasattr(mat, "blend_method"):
    mat.blend_method = "BLEND"
  return mat


def add_cylinder_between(
  name: str,
  start: Vector,
  end: Vector,
  radius: float,
  mat: bpy.types.Material,
  *,
  vertices: int = 14,
) -> bpy.types.Object:
  direction = end - start
  midpoint = (start + end) / 2
  bpy.ops.mesh.primitive_cylinder_add(
    vertices=vertices,
    radius=radius,
    depth=direction.length,
    location=midpoint,
  )
  obj = bpy.context.object
  obj.name = name
  obj.rotation_mode = "QUATERNION"
  obj.rotation_quaternion = direction.to_track_quat("Z", "Y")
  obj.data.materials.append(mat)
  for polygon in obj.data.polygons:
    polygon.use_smooth = True
  return obj


def stem_point(t: float, stem_length: float, bend: float, head_lean: float, collapse: float = 0.0) -> Vector:
  natural_curve = -0.14 * math.sin(math.pi * t) * smoothstep(t / 0.45) + 0.14 * t**3
  standing = Vector(
    (
      stem_length * (natural_curve + head_lean * t**1.45 + 0.5 * bend * 1.15 * t**2.25),
      0.025 * math.sin(t * math.pi * 1.4) - math.sqrt(3.0) / 2.0 * stem_length * bend * 1.15 * t**2.25,
      2.16 * stem_length * t * (1.0 - 0.55 * bend * t**3),
    )
  )
  if collapse <= 0.0:
    return standing
  onset = 0.30 * (1.0 - t)
  fold = smoothstep((collapse - onset) / (1.0 - onset))
  folded = stem_length * Vector(
    (
      0.075 * math.sin(math.tau * t),
      0.02 * math.sin(math.pi * t),
      0.18 * t + 0.04 * math.sin(math.pi * t),
    )
  )
  return standing.lerp(folded, fold)


def add_stem(
  mat: bpy.types.Material,
  stem_length: float,
  bend: float,
  head_lean: float,
  collapse: float,
) -> None:
  points = []
  for index in range(8):
    t = index / 7
    points.append(stem_point(t, stem_length, bend, head_lean, collapse))
  curve_data = bpy.data.curves.new("Living stem curve", type="CURVE")
  curve_data.dimensions = "3D"
  curve_data.resolution_u = 16
  curve_data.bevel_depth = 0.052
  curve_data.bevel_resolution = 4
  spline = curve_data.splines.new("BEZIER")
  spline.bezier_points.add(len(points) - 1)
  for index, (point, coordinate) in enumerate(zip(spline.bezier_points, points)):
    point.co = coordinate
    point.handle_left_type = "AUTO"
    point.handle_right_type = "AUTO"
    t = index / (len(points) - 1)
    point.radius = 1.0 - 0.38 * t + 0.045 * math.sin(math.tau * t)
  obj = bpy.data.objects.new("Living stem", curve_data)
  bpy.context.collection.objects.link(obj)
  curve_data.materials.append(mat)


def profile_widths(shape: str) -> tuple[float, ...]:
  if shape == "pointed":
    return (0.018, 0.10, 0.20, 0.27, 0.17, 0.07, 0.012)
  if shape == "fern":
    return (0.018, 0.12, 0.23, 0.30, 0.24, 0.12, 0.018)
  return (0.018, 0.16, 0.30, 0.40, 0.36, 0.26, 0.08)


def shaped_mesh(
  name: str,
  mat: bpy.types.Material,
  *,
  shape: str,
  length: float,
  width: float,
  curl: float,
  ridge: float,
) -> bpy.types.Object:
  row_positions = (0.0, 0.12, 0.28, 0.46, 0.64, 0.82, 1.0)
  widths = profile_widths(shape)
  verts = []
  for t, row_width in zip(row_positions, widths):
    for across in (-1.0, 0.0, 1.0):
      edge = abs(across)
      x = across * row_width * width
      y = -ridge * math.sin(math.pi * t) * (1.0 - edge**1.7) + 0.012 * edge
      z = length * t - curl * t**2 + ridge * 0.45 * math.sin(math.pi * t) * (1.0 - edge)
      verts.append((x, y, z))
  faces = []
  for row in range(len(row_positions) - 1):
    start = row * 3
    next_start = (row + 1) * 3
    for column in range(2):
      faces.append(
        (
          start + column,
          start + column + 1,
          next_start + column + 1,
          next_start + column,
        )
      )
  mesh = bpy.data.meshes.new(name)
  mesh.from_pydata(verts, [], faces)
  mesh.update()
  obj = bpy.data.objects.new(name, mesh)
  bpy.context.collection.objects.link(obj)
  obj.data.materials.append(mat)
  solidify = obj.modifiers.new("Organic thickness", "SOLIDIFY")
  solidify.thickness = 0.026 if "Petal" in name else 0.018
  bevel = obj.modifiers.new("Soft edge", "BEVEL")
  bevel.width = 0.022 if "Petal" in name else 0.014
  bevel.segments = 2
  for polygon in mesh.polygons:
    polygon.use_smooth = True
  return obj


def add_bud(
  mat: bpy.types.Material,
  sepal_mat: bpy.types.Material,
  center: Vector,
  bud_size: float,
  petal_open: float,
  vitality: float,
) -> None:
  if petal_open < 0.55:
    bpy.ops.mesh.primitive_uv_sphere_add(
      segments=24,
      ring_count=16,
      radius=1.0,
      location=center + Vector((0.0, 0.0, 0.10 * bud_size)),
    )
    bud = bpy.context.object
    bud.name = "Bud_Core"
    enclosure = smoothstep((0.55 - petal_open) / 0.55) * vitality**2
    bud.scale = (0.12 * enclosure, 0.12 * enclosure, 0.18 * enclosure)
    bud.data.materials.append(mat)
    for polygon in bud.data.polygons:
      polygon.use_smooth = True

  sepal_amount = 1.0 - 0.55 * petal_open
  for index in range(3):
    angle = 2 * math.pi * index / 3 + 0.25
    sepal = shaped_mesh(
      f"Sepal_{index:02d}",
      sepal_mat,
      shape="pointed",
      length=0.32 * sepal_amount,
      width=0.18 * sepal_amount,
      curl=0.05,
      ridge=0.018,
    )
    sepal.location = center + Vector((0.0, 0.015, -0.07))
    sepal.rotation_euler[1] = angle
    sepal.rotation_euler[0] = -0.20 + 0.55 * petal_open


def add_petal_ring(
  petal_mats: tuple[bpy.types.Material, ...],
  bloom: float,
  droop: float,
  head_lean: float,
  flower_height: float,
  maturity: float,
  petal_curl: float,
  collapse: float,
) -> None:
  center = Vector((0.03 + head_lean, -0.045, flower_height))
  opening = clamp01(bloom)
  axis = Vector((0.20 + 0.5 * collapse, -0.83, 0.52 - droop * 1.4 + 0.86 * collapse)).normalized()
  horizontal = Vector((1.0, 0.0, 0.0))
  horizontal = (horizontal - axis * horizontal.dot(axis)).normalized()
  vertical = axis.cross(horizontal).normalized()
  for index in range(STYLE.petal_count):
    angle = 2 * math.pi * index / STYLE.petal_count + 0.42
    radial = horizontal * math.cos(angle) + vertical * math.sin(angle)
    tangent = axis.cross(radial)
    unfurl = smoothstep(clamp01(opening * 1.14 - 0.10 * (0.5 + 0.5 * math.sin(index * 2.3))))
    tissue = smoothstep(maturity / 0.08)
    length = STYLE.petal_length * (0.52 + 0.48 * maturity) * (0.65 + 0.35 * tissue)
    length *= 0.94 + 0.06 * math.sin(index * 1.9)
    curl = smoothstep(petal_curl * (0.94 + 0.06 * math.sin(index * 1.7)))
    tip_angle = curl * (2.8 + 0.4 * math.sin(index * 1.3))
    edge_angle = 1.8 * curl
    verts = []
    rows, columns = 25, 13
    for row in range(rows):
      t = row / (rows - 1)
      width = STYLE.petal_width * math.sin(math.pi * t) ** 0.65
      width *= (0.35 + 0.65 * maturity) * (0.72 + 0.28 * t)
      width *= (1.0 - 0.20 * droop * t) * (0.35 + 0.65 * unfurl)
      along = math.sin(tip_angle * t) / tip_angle if tip_angle > 1e-8 else t
      inward = (1.0 - math.cos(tip_angle * t)) / tip_angle if tip_angle > 1e-8 else 0.0
      distance = 0.045 + length * along * (0.12 + 0.88 * unfurl) - 0.22 * length * inward
      distance += 0.13 * tissue * (1.0 - unfurl) * math.sin(math.pi * t) * (1.0 - 0.6 * curl)
      cup = length * along * (0.92 - 0.76 * unfurl)
      cup += 0.20 * unfurl * t**3
      for column in range(columns):
        across = 2 * column / (columns - 1) - 1
        pleat = 0.012 * math.cos(across * math.pi * 4 + t * 1.8) * math.sin(math.pi * t)
        edge = (0.09 + 0.10 * (1.0 - unfurl)) * across**2 * math.sin(math.pi * t)
        ripple = 0.025 * math.sin(across * 8 + index * 1.7) * t**4
        folded_across = math.sin(across * edge_angle) / edge_angle if edge_angle > 1e-8 else across
        edge_roll = width * (1.0 - math.cos(across * edge_angle)) / edge_angle if edge_angle > 1e-8 else 0.0
        point = center + radial * distance + tangent * (folded_across * width)
        point += axis * (cup + edge + edge_roll + pleat * (1.0 + droop + 1.5 * curl) + ripple)
        point.z -= droop * (0.12 + 0.045 * math.sin(index * 2.1)) * t**2 * (1.0 - 0.7 * curl)
        verts.append(tuple(point))
    faces = []
    for row in range(rows - 1):
      for column in range(columns - 1):
        a = row * columns + column
        faces.append((a, a + 1, a + columns + 1, a + columns))
    mesh = bpy.data.meshes.new(f"Petal_{index:02d}")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.materials.append(petal_mats[index % len(petal_mats)])
    for polygon in mesh.polygons:
      polygon.use_smooth = True
    solidify = obj.modifiers.new("Thin petal tissue", "SOLIDIFY")
    solidify.thickness = 0.006


def add_leaf(
  mat: bpy.types.Material,
  side: float,
  growth: float,
  droop: float,
  head_lean: float,
  stem_length: float,
  index: int,
  stem_bend: float,
  collapse: float,
  petal_curl: float,
) -> None:
  growth = clamp01(growth)
  if growth <= 0.01:
    return
  curl = smoothstep((petal_curl - 0.25) / 0.75)
  leaf = shaped_mesh(
    f"Leaf_{'L' if side < 0 else 'R'}_{index}",
    mat,
    shape=STYLE.leaf_profile,
    length=STYLE.leaf_length,
    width=STYLE.leaf_width * (1.0 - 0.65 * curl),
    curl=0.12 + 0.12 * droop + 0.50 * curl,
    ridge=0.034 + 0.07 * curl,
  )
  t = 0.53 + 0.10 * index
  leaf.location = stem_point(t, stem_length, stem_bend, head_lean, collapse)
  angle = (68.0 - 22.0 * growth + 95.0 * droop) * (1.0 - collapse) + 35.0 * collapse
  leaf.rotation_euler[1] = side * math.radians(angle)
  leaf.rotation_euler[0] = math.radians(7.0) + droop * side * 0.65
  leaf.rotation_euler[2] = side * 0.08 * (1.0 - growth)
  leaf.scale = (growth, growth, growth)


def add_center(
  center_mat: bpy.types.Material,
  filament_mat: bpy.types.Material,
  head_lean: float,
  flower_height: float,
  droop: float,
  maturity: float,
  collapse: float,
) -> None:
  axis = Vector((0.20 + 0.5 * collapse, -0.83, 0.52 - droop * 1.4 + 0.86 * collapse)).normalized()
  horizontal = Vector((1.0, 0.0, 0.0))
  horizontal = (horizontal - axis * horizontal.dot(axis)).normalized()
  vertical = axis.cross(horizontal).normalized()
  spent = smoothstep((droop - 0.18) / 0.72)
  center = Vector((0.03 + head_lean, -0.045, flower_height)) + axis * (0.085 * (1.0 - 0.45 * spent))
  radius = 0.11 * (0.35 + 0.65 * maturity) * (1.0 - 0.30 * spent)
  bpy.ops.mesh.primitive_ico_sphere_add(
    subdivisions=3,
    radius=radius,
    location=center,
  )
  obj = bpy.context.object
  obj.name = "Flower_Center"
  obj.scale = (1.0, 1.0, 0.55 * (1.0 - 0.5 * spent))
  obj.rotation_mode = "QUATERNION"
  obj.rotation_quaternion = axis.to_track_quat("Z", "Y")
  obj.data.materials.append(center_mat)
  for polygon in obj.data.polygons:
    polygon.use_smooth = True

  if maturity < 0.001:
    return
  filament_amount = smoothstep(maturity)
  for index in range(24):
    angle = 2 * math.pi * index / 24 + 0.2
    direction = horizontal * math.cos(angle) + vertical * math.sin(angle)
    start = center + direction * radius * 0.7
    end = center + direction * radius * (1.15 + 0.45 * filament_amount) * (1.0 - 0.4 * spent)
    end += axis * (0.025 + 0.022 * math.sin(index * 2.4)) * (1.0 - 0.7 * spent)
    add_cylinder_between(
      f"Filament_{index:02d}",
      start,
      end,
      0.012 * (1.0 - 0.6 * spent),
      filament_mat,
      vertices=8,
    )
    bpy.ops.mesh.primitive_ico_sphere_add(
      subdivisions=2,
      radius=0.017 * (1.0 - 0.65 * spent),
      location=end,
    )
    pollen = bpy.context.object
    pollen.name = f"Pollen_{index:02d}"
    pollen.scale = (1.0, 0.75, 1.0)
    pollen.data.materials.append(filament_mat)


def add_camera() -> None:
  bpy.ops.object.camera_add(location=(4.30, -7.50, 1.45))
  camera = bpy.context.object
  camera.name = "Flower_Camera"
  camera.data.type = "ORTHO"
  camera.data.ortho_scale = 3.35
  direction = Vector((0.10, 0.0, 1.28)) - camera.location
  camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
  bpy.context.scene.camera = camera


def add_lights() -> None:
  for name, location, energy, size, color in (
    ("Key", (-3.5, -4.5, 6.0), 520.0, 4.0, (1.0, 0.72, 0.62)),
    ("Fill", (3.0, -2.0, 3.0), 300.0, 3.0, (0.52, 0.70, 1.0)),
    ("Rim", (0.0, 2.0, 4.5), 420.0, 2.5, (1.0, 0.42, 0.26)),
  ):
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.name = f"Light_{name}"
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
    light.data.color = color
    direction = Vector((0.0, 0.0, 1.35)) - light.location
    light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def configure_render(render_path: Path) -> bpy.types.Scene:
  scene = bpy.context.scene
  try:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
  except TypeError:
    scene.render.engine = "BLENDER_EEVEE"
  scene.render.resolution_x = 256
  scene.render.resolution_y = 256
  scene.render.resolution_percentage = 100
  scene.render.image_settings.file_format = "PNG"
  scene.render.image_settings.color_mode = "RGBA"
  scene.render.film_transparent = True
  scene.render.filepath = str(render_path)
  scene.render.use_file_extension = True
  scene.render.use_compositing = False
  scene.render.use_sequencer = False
  scene.render.image_settings.color_depth = "8"
  scene.render.fps = 60
  try:
    scene.view_settings.look = "AgX - Medium High Contrast"
  except TypeError:
    pass
  return scene


def save_scene(path: Path) -> None:
  """Save the scene alone, without the UI state or paths that would place it on this machine."""
  scene = bpy.context.scene
  render_path = scene.render.filepath
  # The path is saved as its whole fixed-size buffer, so fill it first to leave no old path behind the new one.
  scene.render.filepath = "/" * 1023
  scene.render.filepath = f"//{path.stem}"
  bpy.data.libraries.write(str(path), {scene}, compress=True)
  scene.render.filepath = render_path


def build_scene(pose_name: str) -> bpy.types.Scene:
  if pose_name not in POSES:
    raise ValueError(f"unknown pose: {pose_name}")
  pose = POSES[pose_name]
  render_path = RENDER_DIR / f"flower_{pose_name}.png"
  render_path.parent.mkdir(parents=True, exist_ok=True)
  clear_scene()

  stem_length = pose.get("stem_length", pose["stem"])
  petal_open = pose["petal_open"]
  vitality = pose["vitality"]
  droop = pose["droop"]
  head_lean = pose["head_lean"]
  collapse = pose["collapse"]
  petal_curl = pose["petal_curl"]
  tip = stem_point(1.0, stem_length, pose["stem_bend"], head_lean, collapse)
  flower_height = tip.z

  petal_mats = tuple(
    material(
      f"{STYLE.name} petal {index}",
      tuple(
        color[channel] * (0.4 + 0.6 * vitality) + (0.24, 0.10, 0.075)[channel] * 0.6 * (1.0 - vitality)
        for channel in range(3)
      )
      + (color[3],),
      0.40 + 0.16 * (1.0 - vitality),
      transmission=0.16,
      subsurface=0.075,
    )
    for index, color in enumerate(STYLE.petal_colors)
  )
  center_mat = material("Flower center", STYLE.center_color, 0.30 + 0.30 * droop, subsurface=0.04)
  filament_mat = material("Pollen gold", STYLE.filament_color, 0.24 + 0.36 * droop)
  stem_mat = material("Living stem", STYLE.stem_color, 0.40, subsurface=0.025)
  leaf_mat = material("Leaf surface", STYLE.leaf_color, 0.33, transmission=0.04, subsurface=0.05)
  sepal_mat = material("Sepal green", STYLE.leaf_color, 0.38, subsurface=0.04)

  add_stem(stem_mat, stem_length, pose["stem_bend"], head_lean, collapse)
  add_leaf(
    leaf_mat,
    -1.0,
    pose["leaf_unfurl"] * 1.05,
    droop,
    head_lean,
    stem_length,
    0,
    pose["stem_bend"],
    collapse,
    petal_curl,
  )
  add_leaf(
    leaf_mat,
    1.0,
    (pose["leaf_unfurl"] - 0.12) / 0.88,
    droop,
    head_lean,
    stem_length,
    1,
    pose["stem_bend"],
    collapse,
    petal_curl,
  )
  head_offset = tip.x - 0.03
  add_petal_ring(
    petal_mats,
    petal_open,
    droop,
    head_offset,
    flower_height,
    pose["maturity"],
    petal_curl,
    collapse,
  )
  add_bud(
    petal_mats[0],
    sepal_mat,
    Vector((0.03 + head_offset, -0.03, flower_height)),
    pose["bud_size"],
    petal_open,
    vitality,
  )
  add_center(
    center_mat,
    filament_mat,
    head_offset,
    flower_height,
    droop,
    pose["maturity"],
    collapse,
  )
  bpy.context.view_layer.update()
  head_origin = Vector((0.03 + head_offset, -0.03, flower_height))
  head_scale = 0.28 + 0.72 * smoothstep(stem_length)
  head_depth = Vector((0.0, tip.y - 0.025 * math.sin(math.pi * 1.4), 0.0))
  for obj in bpy.context.scene.objects:
    if obj.type == "MESH" and obj.name.startswith(
      ("Petal_", "Sepal_", "Bud_", "Flower_Center", "Filament_", "Pollen_")
    ):
      inverse = obj.matrix_world.inverted()
      for vertex in obj.data.vertices:
        world = obj.matrix_world @ vertex.co
        vertex.co = inverse @ (head_origin + (world - head_origin) * head_scale + head_depth)
  if pose["retraction"] > 0.0:
    remaining = 1.0 - pose["retraction"]
    for obj in bpy.context.scene.objects:
      if obj.type in {"MESH", "CURVE"}:
        obj.location *= remaining
        obj.scale *= remaining
  add_camera()
  add_lights()
  return configure_render(render_path)
