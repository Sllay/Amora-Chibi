from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Vec2(BaseModel):
    x: float = 0.0
    y: float = 0.0


class Transform2D(BaseModel):
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    pivot_x: float = 0.5
    pivot_y: float = 0.5
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)


class CanvasConfig(BaseModel):
    width: int = Field(default=720, ge=64, le=4096)
    height: int = Field(default=1280, ge=64, le=4096)
    background: str = "#171228"


class ColorSlot(BaseModel):
    id: str
    name: str
    value: str = "#ffffff"


class AvatarAssetRef(BaseModel):
    id: str
    name: str
    content_type: str = "image/png"


class AnchorPoint(BaseModel):
    """Normalized visual socket stored on one sprite."""

    id: str
    name: str
    x: float = Field(default=0.5, ge=0.0, le=1.0)
    y: float = Field(default=0.5, ge=0.0, le=1.0)


class AvatarPart(BaseModel):
    id: str
    name: str
    asset_id: str
    layer: int = 0
    bone_id: str | None = None
    tint_slot: str | None = None
    visible: bool = True
    locked: bool = False
    pickable: bool = True
    flip_x: bool = False
    flip_y: bool = False
    anchors: list[AnchorPoint] = Field(default_factory=list)
    transform: Transform2D = Field(default_factory=Transform2D)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_anchor_ids(self) -> "AvatarPart":
        ids = [item.id for item in self.anchors]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate anchor id on part {self.id}")
        return self


class PartAttachment(BaseModel):
    """Rigid socket-to-socket connection between two sprite parts."""

    id: str
    name: str
    parent_part_id: str
    parent_anchor_id: str
    child_part_id: str
    child_anchor_id: str
    inherit_rotation: bool = True
    enabled: bool = True


class BoneDefinition(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    length: float = Field(default=60.0, ge=0.0, le=4096.0)
    min_rotation: float = -3.141592653589793
    max_rotation: float = 3.141592653589793


class IKChain(BaseModel):
    id: str
    name: str
    root_bone_id: str
    joint_bone_id: str
    end_bone_id: str
    target: Vec2 = Field(default_factory=Vec2)
    bend: Literal["positive", "negative"] = "positive"
    enabled: bool = True


class PhysicsSlot(BaseModel):
    id: str
    target_part_id: str
    enabled: bool = True
    stiffness: float = Field(default=0.18, ge=0.0, le=1.0)
    damping: float = Field(default=0.78, ge=0.0, le=1.0)
    gravity: float = Field(default=0.0, ge=-4.0, le=4.0)
    max_angle: float = Field(default=0.22, ge=0.0, le=3.141592653589793)


class ExpressionPreset(BaseModel):
    id: str
    name: str
    visibility: dict[str, bool] = Field(default_factory=dict)
    part_opacity: dict[str, float] = Field(default_factory=dict)
    mouth_part_id: str | None = None
    brow_part_id: str | None = None
    blush_opacity: float | None = Field(default=None, ge=0.0, le=1.0)


class AnimationKeyframe(BaseModel):
    time: float = Field(ge=0.0)
    value: float | str | bool | list[float]
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out", "spring"] = "ease_in_out"


class AnimationTrack(BaseModel):
    target_id: str
    property: str
    keyframes: list[AnimationKeyframe] = Field(default_factory=list)


class AnimationClip(BaseModel):
    id: str
    name: str
    duration: float = Field(default=1.0, gt=0.0, le=120.0)
    loop: bool = True
    tracks: list[AnimationTrack] = Field(default_factory=list)


class AvatarProject(BaseModel):
    id: str = "default"
    # V2 is additive: V1 projects validate unchanged, gaining only defaults for
    # new fields when they are next saved by the Studio.
    schema_version: int = 2
    name: str = "Amora"
    canvas: CanvasConfig = Field(default_factory=CanvasConfig)
    color_slots: list[ColorSlot] = Field(default_factory=list)
    assets: list[AvatarAssetRef] = Field(default_factory=list)
    parts: list[AvatarPart] = Field(default_factory=list)
    attachments: list[PartAttachment] = Field(default_factory=list)
    bones: list[BoneDefinition] = Field(default_factory=list)
    ik_chains: list[IKChain] = Field(default_factory=list)
    physics: list[PhysicsSlot] = Field(default_factory=list)
    expressions: list[ExpressionPreset] = Field(default_factory=list)
    animations: list[AnimationClip] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_project(self) -> "AvatarProject":
        groups = {
            "color slot": [item.id for item in self.color_slots],
            "asset": [item.id for item in self.assets],
            "part": [item.id for item in self.parts],
            "attachment": [item.id for item in self.attachments],
            "bone": [item.id for item in self.bones],
            "IK chain": [item.id for item in self.ik_chains],
            "physics slot": [item.id for item in self.physics],
            "expression": [item.id for item in self.expressions],
            "animation": [item.id for item in self.animations],
        }
        for label, values in groups.items():
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} id")

        parts = {item.id: item for item in self.parts}
        parent_of: dict[str, str] = {}
        for attachment in self.attachments:
            if attachment.parent_part_id == attachment.child_part_id:
                raise ValueError("attachment cannot connect a part to itself")
            parent = parts.get(attachment.parent_part_id)
            child = parts.get(attachment.child_part_id)
            if parent is None or child is None:
                raise ValueError(f"attachment {attachment.id} references missing part")
            if not any(item.id == attachment.parent_anchor_id for item in parent.anchors):
                raise ValueError(f"attachment {attachment.id} references missing parent anchor")
            if not any(item.id == attachment.child_anchor_id for item in child.anchors):
                raise ValueError(f"attachment {attachment.id} references missing child anchor")
            if attachment.enabled:
                if attachment.child_part_id in parent_of:
                    raise ValueError(f"part {attachment.child_part_id} has more than one active attachment")
                parent_of[attachment.child_part_id] = attachment.parent_part_id

        for child in parent_of:
            seen: set[str] = set()
            current = child
            while current in parent_of:
                if current in seen:
                    raise ValueError("attachment cycle detected")
                seen.add(current)
                current = parent_of[current]
        return self


class AvatarAssetInfo(BaseModel):
    id: str
    content_type: str
    file_name: str
    size_bytes: int
    sha256: str
    created_at: datetime
    updated_at: datetime


class AvatarSemanticState(BaseModel):
    timestamp: datetime
    expression: str = "neutral"
    emotion: str = "neutral"
    intensity: float = Field(default=0.0, ge=0.0, le=1.0)
    gesture: str | None = None
    attention_target: str | None = None
    speaking: bool = False
    mouth: str = "neutral"
    blink_allowed: bool = True
    blush: float = Field(default=0.0, ge=0.0, le=1.0)
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0)
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


def default_project() -> AvatarProject:
    return AvatarProject(
        color_slots=[
            ColorSlot(id="skin", name="Pele", value="#f4cfd0"),
            ColorSlot(id="hair_primary", name="Cabelo principal", value="#342064"),
            ColorSlot(id="hair_secondary", name="Cabelo secundário", value="#d79aaf"),
            ColorSlot(id="eyes", name="Olhos", value="#7656ff"),
            ColorSlot(id="dress", name="Vestido", value="#4b247f"),
            ColorSlot(id="dress_straps", name="Alças", value="#6c3ca0"),
            ColorSlot(id="shoes", name="Sapatos", value="#2b1748"),
            ColorSlot(id="accessory", name="Acessórios", value="#593087"),
            ColorSlot(id="blush", name="Corado", value="#f99bad"),
        ],
        expressions=[
            ExpressionPreset(id="neutral", name="Neutra"),
            ExpressionPreset(id="happy", name="Feliz"),
            ExpressionPreset(id="sad", name="Triste"),
            ExpressionPreset(id="angry", name="Raiva"),
            ExpressionPreset(id="playful", name="Brincalhona"),
        ],
        animations=[
            AnimationClip(id="idle", name="Idle", duration=2.4, loop=True),
            AnimationClip(id="talk", name="Falando", duration=0.42, loop=True),
        ],
        metadata={"editor": "amora-avatar-studio", "mobile_first": True},
    )
