from __future__ import annotations

import pytest
from pydantic import ValidationError

from amora.avatar import AvatarProject


def _mapped_v1_payload() -> dict[str, object]:
    return {
        "id": "default",
        "schema_version": 1,
        "name": "Amora",
        "canvas": {"width": 720, "height": 1280, "background": "#000000"},
        "color_slots": [
            {"id": "skin", "name": "Pele", "value": "#ffbdd0"},
            {"id": "hair_primary", "name": "Cabelo principal", "value": "#7c00c4"},
        ],
        "assets": [
            {"id": "torso_asset", "name": "13 Sem Titulo torso.png", "content_type": "image/png"},
            {"id": "arm_asset", "name": "13 Sem Titulo arm.png", "content_type": "image/png"},
            {"id": "hand_asset", "name": "13 Sem Titulo hand.png", "content_type": "image/png"},
        ],
        "parts": [
            {
                "id": "torso_part",
                "name": "Torso",
                "asset_id": "torso_asset",
                "layer": 13,
                "bone_id": None,
                "tint_slot": "skin",
                "visible": True,
                "transform": {
                    "x": 360.0,
                    "y": 640.0,
                    "rotation": 0.0,
                    "scale_x": 1.0,
                    "scale_y": 1.0,
                    "pivot_x": 0.37,
                    "pivot_y": 0.42,
                    "opacity": 1.0,
                },
                "tags": ["Torso"],
            },
            {
                "id": "arm_part",
                "name": "Right/Arm",
                "asset_id": "arm_asset",
                "layer": 10,
                "bone_id": None,
                "tint_slot": "skin",
                "visible": True,
                "transform": {
                    "x": 412.5,
                    "y": 655.25,
                    "rotation": 0.17,
                    "scale_x": 1.0,
                    "scale_y": 1.0,
                    "pivot_x": 0.11,
                    "pivot_y": 0.18,
                    "opacity": 0.96,
                },
                "tags": ["Right/Arm"],
            },
            {
                "id": "hand_part",
                "name": "Right/Hand",
                "asset_id": "hand_asset",
                "layer": 12,
                "bone_id": None,
                "tint_slot": "skin",
                "visible": True,
                "transform": {
                    "x": 465.75,
                    "y": 710.5,
                    "rotation": -0.08,
                    "scale_x": 1.0,
                    "scale_y": 1.0,
                    "pivot_x": 0.5,
                    "pivot_y": 0.5,
                    "opacity": 1.0,
                },
                "tags": ["Right/Hand"],
            },
        ],
        "bones": [],
        "ik_chains": [],
        "physics": [],
        "expressions": [
            {
                "id": "neutral",
                "name": "Neutra",
                "visibility": {"hand_part": True},
                "part_opacity": {},
                "mouth_part_id": None,
                "brow_part_id": None,
                "blush_opacity": None,
            }
        ],
        "animations": [],
        "metadata": {"editor": "amora-avatar-studio", "mobile_first": True},
    }


def test_v1_mapping_loads_without_losing_user_work() -> None:
    raw = _mapped_v1_payload()
    project = AvatarProject.model_validate(raw)
    dumped = project.model_dump(mode="json")

    assert project.schema_version == 1
    assert [item.name for item in project.parts] == ["Torso", "Right/Arm", "Right/Hand"]
    assert [item.layer for item in project.parts] == [13, 10, 12]
    assert project.parts[1].transform.model_dump(mode="json") == raw["parts"][1]["transform"]
    assert project.parts[1].tags == ["Right/Arm"]
    assert project.color_slots[1].value == "#7c00c4"
    assert project.expressions[0].visibility == {"hand_part": True}

    # V2 fields are additive defaults only. Existing names/transforms/layers are
    # untouched, so a carefully mapped V1 project can be edited further safely.
    assert dumped["parts"][1]["anchors"] == []
    assert dumped["parts"][1]["flip_x"] is False
    assert dumped["parts"][1]["flip_y"] is False
    assert dumped["parts"][1]["locked"] is False
    assert dumped["parts"][1]["pickable"] is True
    assert dumped["attachments"] == []


def test_socket_attachment_and_flip_are_persisted() -> None:
    project = AvatarProject.model_validate(_mapped_v1_payload())
    project.schema_version = 2
    torso = project.parts[0]
    arm = project.parts[1]
    torso.anchors = [{"id": "shoulder_r", "name": "Ombro D", "x": 0.9, "y": 0.24}]
    arm.anchors = [{"id": "root", "name": "Raiz", "x": 0.08, "y": 0.12}]
    arm.flip_x = True
    project.attachments = [
        {
            "id": "torso_to_arm_r",
            "name": "Torso -> Right Arm",
            "parent_part_id": torso.id,
            "parent_anchor_id": "shoulder_r",
            "child_part_id": arm.id,
            "child_anchor_id": "root",
            "inherit_rotation": True,
            "enabled": True,
        }
    ]

    round_trip = AvatarProject.model_validate(project.model_dump(mode="json"))
    assert round_trip.parts[1].name == "Right/Arm"
    assert round_trip.parts[1].layer == 10
    assert round_trip.parts[1].flip_x is True
    assert round_trip.parts[1].anchors[0].id == "root"
    assert round_trip.attachments[0].parent_anchor_id == "shoulder_r"


def test_socket_cycles_are_rejected() -> None:
    payload = _mapped_v1_payload()
    for part in payload["parts"][:2]:
        part["anchors"] = [{"id": "root", "name": "Root", "x": 0.5, "y": 0.5}]
    payload["attachments"] = [
        {
            "id": "a",
            "name": "A",
            "parent_part_id": "torso_part",
            "parent_anchor_id": "root",
            "child_part_id": "arm_part",
            "child_anchor_id": "root",
        },
        {
            "id": "b",
            "name": "B",
            "parent_part_id": "arm_part",
            "parent_anchor_id": "root",
            "child_part_id": "torso_part",
            "child_anchor_id": "root",
        },
    ]
    with pytest.raises(ValidationError, match="attachment cycle detected"):
        AvatarProject.model_validate(payload)
