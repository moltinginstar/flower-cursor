"""Still studies of candidate species, at the runtime camera and caret scale."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bake_idle_bee as bee_authoring
import create_cornflower_scene as cornflower
import create_flower_scene as base
import study_shedding as study

from flower_cursor.lifecycle import CursorCell, FlowerInstance

NORMAL = cornflower.NORMAL
RIGHT = cornflower.RIGHT
ACROSS = cornflower.ACROSS
OPTIONS = ("daisy", "crocus", "poppy", "cosmos", "dahlia", "orchid")


def pigment(name, color, roughness=0.43, transmission=0.16):
    return base.material(name, (*color, 1), roughness, transmission=transmission, subsurface=0.075)


def sphere(name, center, radius, mat, *, axis=NORMAL, flatten=1.0):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = axis.to_track_quat("Z", "Y")
    obj.scale = (1, 1, flatten)
    obj.data.materials.append(mat)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    return obj


def disc(origin, radius, depth, mats, count=91):
    sphere("Disc cushion", origin, radius, mats[0], flatten=depth / radius)
    for index in range(count):
        angle = index * 2.3999632297
        r = radius * math.sqrt((index + 0.5) / count)
        radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
        z = depth * math.sqrt(max(0, 1 - (r / radius) ** 2))
        center = origin + radial * r + NORMAL * z
        sphere(f"Disc floret {index}", center, radius * 0.085, mats[index % len(mats)], flatten=1.3)


def petal(name, origin, angle, length, width, cup, mats, *, form="round", ripple=0.008):
    radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
    tangent = -RIGHT * math.sin(angle) + ACROSS * math.cos(angle)
    vertices, faces, shades = [], [], []
    rows, columns = 29, 17
    for row in range(rows):
        t = row / (rows - 1)
        if form == "fan":
            profile = math.sin(math.pi * 0.5 * t) ** 0.68 * (1 - 0.12 * t)
        else:
            profile = math.sin(math.pi * (0.01 + 0.985 * t)) ** 0.55
        for column in range(columns):
            u = column * 2 / (columns - 1) - 1
            notch = t**6 * (0.065 * u * u + 0.10 * (1 - u * u) ** 8) if form == "fan" else 0
            spread = length * t - notch
            elevation = cup * t**1.7 + 0.026 * u * u * math.sin(math.pi * t)
            crease = ripple * math.sin(t * 17 + u * 13 + angle) * t * t
            crease += ripple * 0.5 * math.cos(u * 19) * math.sin(math.pi * t)
            vertices.append(
                origin
                + radial * spread
                + tangent * (width * profile * u)
                + NORMAL * (elevation + crease)
            )
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            faces.append((a, a + 1, a + columns + 1, a + columns))
            shades.append(1 if column in (3, 7, 8, 12) else 0)
    obj = cornflower.mesh_object(name, vertices, faces, mats, "head", shades)
    obj.modifiers.new("Thin translucent petal", "SOLIDIFY").thickness = 0.004
    return obj


def lamina(name, start, direction, length, width, mats, form, *, normal=NORMAL):
    side = normal.cross(direction).normalized()
    facing = direction.cross(side).normalized()
    vertices, faces, shades = [], [], []
    columns = (-1.0, -0.5, -0.16, 0.0, 0.16, 0.5, 1.0)
    rows = 65
    for row in range(rows):
        t = row / (rows - 1)
        if form == "striped":
            profile = (0.75 + 0.25 * math.sin(math.pi * t)) * (1 - t**8)
        elif form == "strap":
            profile = (0.20 + 0.80 * math.sin(math.pi * t) ** 0.6) * (1 - t**12)
        else:
            blade = max(0, (t - 0.16) / 0.84)
            profile = 0.025 + math.sin(math.pi * blade) ** 0.7
        for across in columns:
            edge = 1.0
            if form == "toothed":
                edge = 0.78 + 0.22 * (0.5 + 0.5 * math.cos(t * math.pi * 18))
            elif form == "lobed":
                edge = 0.33 + 0.67 * (0.5 + 0.5 * math.cos(t * math.pi * 10 + across * 0.25)) ** 0.7
            if t < 0.16 and form != "striped":
                edge = 1.0
            ridge = 0.014 * math.sin(math.pi * t) * (1 - abs(across))
            arch = 0.055 * math.sin(math.pi * t) - 0.09 * t * t
            vertices.append(
                start
                + direction * (length * t)
                + side * (across * width * profile * edge)
                + facing * (arch + ridge)
            )
    for row in range(rows - 1):
        for column in range(len(columns) - 1):
            a = row * len(columns) + column
            faces.append((a, a + len(columns), a + 1 + len(columns), a + 1))
            shades.append(1 if column in (2, 3) else 0)
    obj = cornflower.mesh_object(name, vertices, faces, mats, "body", shades)
    obj.modifiers.new("Leaf tissue", "SOLIDIFY").thickness = 0.003
    obj["leaf_anchor"] = list(start)
    return obj


def divided_leaf(name, start, direction, length, mats):
    side = NORMAL.cross(direction).normalized()
    end = start + direction * length
    base.add_cylinder_between(name + " rachis", start, end, 0.005, mats[0], vertices=6)
    for index in range(5):
        t = 0.20 + index * 0.145
        attachment = start + direction * (length * t)
        for sign in (-1, 1):
            pinna = (direction * 0.46 + side * sign * 0.88).normalized()
            pinna_length = length * (0.45 - 0.23 * t)
            pinna_end = attachment + pinna * pinna_length
            label = f"{name} pinna {index} {sign}"
            base.add_cylinder_between(label, attachment, pinna_end, 0.0035, mats[0], vertices=6)
            for segment in range(3):
                sub_start = attachment + pinna * (pinna_length * (0.28 + segment * 0.23))
                for branch in (-1, 1):
                    sub_direction = (pinna * 0.62 + direction * branch * 0.57).normalized()
                    cornflower.ribbon(
                        f"{label} division {segment} {branch}",
                        sub_start,
                        sub_direction,
                        side,
                        NORMAL,
                        pinna_length * (0.46 - segment * 0.07),
                        0.009,
                        -0.008,
                        mats,
                        "body",
                    )


def poppy_leaf(name, start, direction, petiole_length, length, width, mats, phase):
    side = NORMAL.cross(direction).normalized()
    facing = direction.cross(side).normalized()
    base_point = start + direction * petiole_length
    petiole = base.add_cylinder_between(
        name + " petiole", start, base_point, 0.009, mats[0], vertices=10
    )
    petiole["leaf_anchor"] = list(start)
    vertices, faces, shades = [], [], []
    rows, columns = 49, 9
    for row in range(rows):
        t = row / (rows - 1)
        envelope = base.smoothstep(t / 0.12) * math.sqrt(max(0, 1 - t**6))
        center = base_point + direction * (length * t)
        center += side * (0.022 * math.sin(math.pi * t + phase) * t)
        for column in range(columns):
            across = 2 * column / (columns - 1) - 1
            shift = 0.025 * math.sin(phase + (1 if across > 0 else -1))
            lobes = sum(
                amplitude * math.exp(-(((t - position - shift) / spread) ** 2))
                for position, spread, amplitude in (
                    (0.24, 0.11, 0.33),
                    (0.47, 0.135, 0.45),
                    (0.74, 0.19, 0.61),
                )
            )
            profile = (0.29 + lobes) * envelope
            curvature = 0.025 * math.sin(math.pi * t) * (1 - across * across) - 0.025 * t * t
            vertices.append(center + side * (across * width * profile) + facing * curvature)
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            faces.append((a, a + columns, a + columns + 1, a + 1))
            shades.append(1 if column in (3, 4) else 0)
    obj = cornflower.mesh_object(name, vertices, faces, mats, "body", shades)
    obj.modifiers.new("Soft poppy leaf tissue", "SOLIDIFY").thickness = 0.003
    obj["leaf_anchor"] = list(start)


def leaves(kind, pose):
    green_color = (0.11, 0.235, 0.16) if kind == "poppy" else (0.055, 0.21, 0.075)
    greens = (
        pigment("Leaf green", green_color, roughness=0.48, transmission=0.045),
        pigment("Leaf midrib", (0.14, 0.31, 0.16), roughness=0.48, transmission=0.045),
    )
    up = Vector((0, 0, 1))
    if kind == "orchid":
        mottled = (
            pigment("Orchid dark leaf", (0.025, 0.11, 0.045), transmission=0.025),
            pigment("Orchid leaf mottling", (0.16, 0.32, 0.11), transmission=0.025),
        )
        bee_authoring.organic_surface(
            mottled[0], (0.022, 0.085, 0.03), (0.19, 0.34, 0.105), scale=7, relief=0.001
        )
        for index, (sign, length, rise) in enumerate(
            ((-1, 0.95, 0.32), (1, 0.82, 0.42), (-1, 0.74, 0.67), (1, 1.02, 0.23))
        ):
            start = study.stem_point(
                0.012 + index * 0.009, pose.stem, pose.stem_bend, pose.head_lean
            )
            direction = (RIGHT * sign + up * rise).normalized()
            obj = lamina(
                f"Mottled orchid blade {index}", start, direction, length, 0.145, mottled, "strap"
            )
            for polygon in obj.data.polygons:
                polygon.material_index = 0
    elif kind == "dahlia":
        for node, height in enumerate((0.32, 0.57)):
            start = study.stem_point(height, pose.stem, pose.stem_bend, pose.head_lean)
            for sign in (-1, 1):
                direction = (RIGHT * sign * 0.80 + up * 0.60).normalized()
                junction = start + direction * 0.24
                base.add_cylinder_between(
                    f"Dahlia leaf rachis {node} {sign}",
                    start,
                    junction,
                    0.008,
                    greens[0],
                    vertices=8,
                )
                lamina(
                    f"Dahlia terminal leaflet {node} {sign}",
                    junction,
                    direction,
                    0.44 - node * 0.06,
                    0.135,
                    greens,
                    "toothed",
                )
                side = NORMAL.cross(direction).normalized()
                for branch in (-1, 1):
                    lateral = (direction * 0.42 + side * branch * 0.80).normalized()
                    lamina(
                        f"Dahlia lateral leaflet {node} {sign} {branch}",
                        start + direction * 0.18,
                        lateral,
                        0.32 - node * 0.04,
                        0.105,
                        greens,
                        "toothed",
                    )
    elif kind == "crocus":
        stripe = pigment("Crocus silver-white midstripe", (0.60, 0.66, 0.55), transmission=0.02)
        for index, (sign, spread, length) in enumerate(
            ((-1, 0.32, 1.48), (1, 0.25, 1.20), (-1, 0.12, 1.70), (1, 0.41, 1.36))
        ):
            start = study.stem_point(
                0.015 + index * 0.004, pose.stem, pose.stem_bend, pose.head_lean
            )
            direction = (RIGHT * sign * spread + up).normalized()
            lamina(
                f"Basal crocus blade {index}",
                start,
                direction,
                length,
                0.036,
                (greens[0], stripe),
                "striped",
            )
    elif kind == "poppy":
        forward = Vector((0.5, -0.866, 0))
        for index, (horizontal, depth, rise, stalk, length, width) in enumerate(
            (
                (-0.91, 0.08, 0.43, 0.25, 0.57, 0.13),
                (0.83, -0.12, 0.61, 0.23, 0.51, 0.12),
                (-0.28, -0.20, 0.94, 0.31, 0.45, 0.105),
                (0.57, 0.58, 0.38, 0.29, 0.39, 0.10),
            )
        ):
            start = study.stem_point(
                0.008 + index * 0.006, pose.stem, pose.stem_bend, pose.head_lean
            )
            direction = (RIGHT * horizontal + forward * depth + up * rise).normalized()
            poppy_leaf(
                f"Basal poppy leaf {index}",
                start,
                direction,
                stalk,
                length,
                width,
                greens,
                index * 1.7,
            )
    elif kind == "cosmos":
        for node, height in enumerate((0.34, 0.60)):
            start = study.stem_point(height, pose.stem, pose.stem_bend, pose.head_lean)
            for sign in (-1, 1):
                direction = (RIGHT * sign * 0.84 + up * 0.54).normalized()
                divided_leaf(
                    f"Opposite cosmos leaf {node} {sign}",
                    start,
                    direction,
                    0.62 - node * 0.13,
                    greens,
                )
    else:
        for index, (height, sign, length, width, form) in enumerate(
            (
                (0.035, -1, 0.72, 0.15, "toothed"),
                (0.055, 1, 0.61, 0.13, "toothed"),
                (0.34, -1, 0.57, 0.11, "lobed"),
                (0.60, 1, 0.45, 0.08, "lobed"),
            )
        ):
            start = study.stem_point(height, pose.stem, pose.stem_bend, pose.head_lean)
            direction = (RIGHT * sign * 0.87 + up * 0.49).normalized()
            lamina(f"Daisy {form} leaf {index}", start, direction, length, width, greens, form)
    return greens[0]


def daisy(origin):
    whites = (pigment("Ivory ray", (0.87, 0.86, 0.74)), pigment("Ivory vein", (0.97, 0.94, 0.84)))
    for i in range(17):
        angle = math.tau * i / 17 + 0.04 * math.sin(i * 4)
        petal(
            f"Daisy ray {i}",
            origin,
            angle,
            0.71 + 0.035 * math.sin(i * 3),
            0.095,
            -0.025 + 0.055 * math.sin(i * 1.3),
            whites,
        )
    gold = (
        pigment("Ochre florets", (0.82, 0.37, 0.015)),
        pigment("Golden pollen", (1.0, 0.68, 0.045)),
    )
    disc(origin + NORMAL * 0.05, 0.205, 0.085, gold)


def crocus(origin):
    purple = (
        pigment("Purple silk", (0.24, 0.07, 0.46)),
        pigment("Lavender veins", (0.42, 0.21, 0.67)),
    )
    for i in range(6):
        angle = math.tau * i / 6
        petal(
            f"Crocus tepal {i}",
            origin - NORMAL * 0.12,
            angle,
            0.40 if i % 2 else 0.46,
            0.22,
            0.73 + 0.055 * (i % 2),
            purple,
            ripple=0.004,
        )
    saffron = pigment("Saffron stigma", (0.95, 0.30, 0.012))
    yellow = pigment("Yellow anthers", (1.0, 0.66, 0.035))
    for i in range(3):
        angle = math.tau * i / 3
        radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
        end = origin + NORMAL * 0.59 + radial * 0.075
        base.add_cylinder_between(f"Crocus stamen {i}", origin, end, 0.011, yellow)
        sphere(f"Crocus anther {i}", end, 0.035, yellow, flatten=2.2)
        stigma = origin + NORMAL * 0.65 + radial * 0.06
        base.add_cylinder_between(
            f"Crocus stigma {i}", origin + NORMAL * 0.28, stigma, 0.012, saffron
        )


def poppy(origin):
    orange = (
        pigment("Apricot tissue", (0.95, 0.22, 0.045)),
        pigment("Apricot crinkles", (1.0, 0.36, 0.075)),
    )
    for i in range(4):
        petal(
            f"Poppy petal {i}",
            origin,
            math.tau * i / 4 + 0.32,
            0.78,
            0.48,
            0.20 + 0.045 * math.sin(i * 3),
            orange,
            ripple=0.032,
        )
    pollen = pigment("Poppy pollen", (0.92, 0.52, 0.025))
    green = pigment("Poppy capsule", (0.33, 0.38, 0.09))
    for i in range(45):
        angle = i * 2.3999632297
        radius = 0.12 + 0.075 * math.sin(i * 2.3) ** 2
        radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
        end = origin + radial * radius + NORMAL * (0.14 + 0.045 * math.sin(i * 1.9))
        base.add_cylinder_between(
            f"Poppy filament {i}", origin + radial * 0.06, end, 0.007, pollen, vertices=6
        )
        sphere(f"Poppy anther {i}", end, 0.022, pollen)
    sphere("Poppy ovary", origin + NORMAL * 0.105, 0.075, green, flatten=1.2)


def cosmos(origin):
    pink = (
        pigment("Magenta satin", (0.65, 0.025, 0.18)),
        pigment("Pink veins", (0.86, 0.075, 0.27)),
    )
    for i in range(8):
        petal(
            f"Cosmos ray {i}",
            origin,
            math.tau * i / 8 + 0.10,
            0.79,
            0.27,
            0.09 + 0.03 * math.sin(i * 2),
            pink,
            form="fan",
            ripple=0.013,
        )
    gold = (
        pigment("Cosmos ochre", (0.75, 0.30, 0.015)),
        pigment("Cosmos gold", (0.98, 0.61, 0.035)),
    )
    disc(origin + NORMAL * 0.045, 0.15, 0.07, gold, count=61)


def dahlia(origin):
    reds = (
        pigment("Ruby red petals", (0.14, 0.003, 0.007), roughness=0.57, transmission=0.03),
        pigment("Deep red petal veins", (0.11, 0.002, 0.005), roughness=0.57, transmission=0.03),
    )
    for mat in reds:
        bee_authoring.organic_surface(
            mat, (0.07, 0.002, 0.004), (0.23, 0.009, 0.011), scale=5, relief=0.0015
        )
        mat.node_tree.nodes["Principled BSDF"].inputs["Coat Weight"].default_value = 0
    for ring, (count, length, width, height, cup) in enumerate(
        (
            (13, 0.70, 0.16, 0.0, 0.12),
            (11, 0.56, 0.16, 0.075, 0.17),
            (9, 0.42, 0.135, 0.16, 0.17),
            (7, 0.29, 0.11, 0.25, 0.13),
            (5, 0.16, 0.06, 0.33, 0.085),
        )
    ):
        for index in range(count):
            angle = math.tau * index / count + ring * 0.39
            petal(
                f"Dahlia ring {ring} petal {index}",
                origin + NORMAL * height,
                angle,
                length * (1 + 0.035 * math.sin(index * 2.7 + ring)),
                width,
                cup,
                reds,
                ripple=0.006,
            )


def orchid_blade(name, origin, direction, side, front, length, width, mats, *, hood=False):
    vertices, faces, shades = [], [], []
    rows, columns = 41, 97
    for row in range(rows):
        t = row / (rows - 1)
        profile = math.sin(math.pi * t) ** (0.72 if hood else 0.53)
        center = origin + direction * (length * t)
        for column in range(columns):
            u = 2 * column / (columns - 1) - 1
            curve = (
                (0.065 * u * u - 0.04) * math.sin(math.pi * t)
                if hood
                else 0.055 * math.sin(math.pi * t)
            )
            vertices.append(center + side * (width * profile * u) + front * curve)
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            faces.append((a, a + columns, a + columns + 1, a + 1))
            shades.append(1 if column % (8 if hood else 12) < 2 else 0)
    obj = cornflower.mesh_object(name, vertices, faces, mats, "head", shades)
    obj.modifiers.new("Orchid tissue", "SOLIDIFY").thickness = 0.006


def orchid(origin):
    front = Vector((0.48, -0.84, 0.23)).normalized()
    upright = front.cross(RIGHT).normalized()
    center = origin - upright * 0.12 + front * 0.045
    ivory = pigment("Orchid ivory dorsal sepal", (0.78, 0.88, 0.66), transmission=0.08)
    green = pigment("Orchid green veins", (0.12, 0.35, 0.027), transmission=0.08)
    pale_green = pigment("Orchid lateral petals", (0.15, 0.37, 0.033), transmission=0.08)
    pale_vein = pigment("Orchid pale lateral veins", (0.27, 0.49, 0.085), transmission=0.08)
    bee_authoring.organic_surface(
        pale_green, (0.09, 0.27, 0.017), (0.25, 0.47, 0.07), scale=9, relief=0.001
    )
    bee_authoring.organic_surface(
        ivory, (0.64, 0.77, 0.50), (0.85, 0.91, 0.73), scale=12, relief=0.0008
    )
    orchid_blade(
        "Striped dorsal sepal",
        center,
        upright,
        -RIGHT,
        front,
        0.60,
        0.37,
        (ivory, green),
        hood=True,
    )
    for sign in (-1, 1):
        direction = (RIGHT * sign - upright * 0.36).normalized()
        side = front.cross(direction).normalized()
        orchid_blade(
            f"Orchid lateral petal {sign}",
            center + front * 0.025,
            direction,
            side,
            front,
            0.72,
            0.14,
            (pale_green, pale_vein),
        )
    pouch = pigment("Orchid green slipper", (0.29, 0.51, 0.075), roughness=0.36, transmission=0.06)
    vertices, faces = [], []
    rows, columns = 33, 64
    for row in range(rows):
        t = row / (rows - 1)
        radius = 0.003 + 0.185 * math.sin(math.pi * t / 2) ** 0.55 * (
            0.76 + 0.24 * math.sin(math.pi * t)
        )
        for column in range(columns):
            angle = math.tau * column / columns
            height = -0.42 + 0.46 * t - 0.14 * math.sin(angle) * t**8
            vertices.append(
                center
                + upright * height
                + RIGHT * (radius * math.cos(angle))
                + front * (0.17 + 0.85 * radius * math.sin(angle))
            )
    for row in range(rows - 1):
        for column in range(columns):
            a, b = row * columns + column, row * columns + (column + 1) % columns
            faces.append((a, a + columns, b + columns, b))
    obj = cornflower.mesh_object("Hollow slipper pouch", vertices, faces, (pouch,), "head")
    obj.modifiers.new("Slipper wall", "SOLIDIFY").thickness = 0.012
    bee_authoring.strand(
        "Rolled slipper rim", vertices[-columns:] + [vertices[-columns]], 0.009, pale_green
    )
    sphere(
        "Orchid staminode",
        center + front * 0.13 + upright * 0.08,
        0.055,
        green,
        axis=front,
        flatten=0.5,
    )


def bellflower(origin):
    blue = (pigment("Violet bell", (0.16, 0.12, 0.57)), pigment("Bell ridges", (0.29, 0.24, 0.77)))
    vertices, faces, shades = [], [], []
    rows, columns = 25, 100
    for row in range(rows):
        t = row / (rows - 1)
        for column in range(columns):
            angle = math.tau * column / columns
            lobe = 0.5 + 0.5 * math.cos(5 * angle)
            radius = 0.065 + 0.35 * t**1.65 + 0.14 * lobe * t**7
            depth = 0.47 * t - 0.13 * (1 - lobe) * t**8
            radial = RIGHT * math.cos(angle) + ACROSS * math.sin(angle)
            radius *= 1 + 0.018 * math.sin(t * 9 + angle * 3)
            vertices.append(origin + NORMAL * depth + radial * radius)
    for row in range(rows - 1):
        for column in range(columns):
            a, b = row * columns + column, row * columns + (column + 1) % columns
            faces.append((a, b, b + columns, a + columns))
            shades.append(1 if column % 20 < 3 else 0)
    obj = cornflower.mesh_object("Five-lobed fused bell", vertices, faces, blue, "head", shades)
    obj.modifiers.new("Bell tissue", "SOLIDIFY").thickness = 0.006
    ivory = pigment("Bellflower pistil", (0.68, 0.72, 0.58))
    end = origin + NORMAL * 0.39
    base.add_cylinder_between("Bellflower style", origin, end, 0.014, ivory)
    for i in range(3):
        radial = RIGHT * math.cos(math.tau * i / 3) + ACROSS * math.sin(math.tau * i / 3)
        base.add_cylinder_between(
            f"Bellflower stigma {i}", end, end + radial * 0.045 + NORMAL * 0.025, 0.014, ivory
        )


def main():
    output = Path(os.environ.get("FLOWER_OPTIONS_OUTPUT", "/tmp/flower-static-options"))
    output.mkdir(parents=True, exist_ok=True)
    flower = FlowerInstance.bud(1, CursorCell(0, 0))
    flower.update(8)
    pose = flower.pose
    selected = os.environ.get("FLOWER_OPTIONS", ",".join(OPTIONS)).split(",")
    for kind in selected:
        if kind not in OPTIONS:
            raise ValueError(f"unknown static design: {kind}")
        base.clear_scene()
        stem_mat = leaves(kind, pose)
        base.stem_point = study.stem_point
        base.add_stem(stem_mat, pose.stem, pose.stem_bend, pose.head_lean, 0)
        bpy.data.objects["Living stem"].data.bevel_depth = 0.038
        origin = study.head_origin(pose)
        globals()[kind](origin)
        base.add_camera()
        base.add_lights()
        scene = base.configure_render(output / f"{kind}.png")
        study.expand_canvas(scene)
        scene.render.resolution_x = scene.render.resolution_y = 1024
        scene.render.threads_mode = "FIXED"
        scene.render.threads = 4
        bpy.ops.wm.save_as_mainfile(filepath=str(output / f"{kind}.blend"))
        bpy.ops.render.render(write_still=True)
        print(f"STATIC DESIGN {kind}", flush=True)


if __name__ == "__main__":
    main()
