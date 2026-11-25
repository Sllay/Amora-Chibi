"""Bounded sensor evidence; region estimates are not verified geometry."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class VisualRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: str = Field(min_length=1,max_length=80)
    position: Literal["top_left","top","top_right","left","center","right","bottom_left","bottom","bottom_right","whole","uncertain"]
    visible_state: str = Field(min_length=1,max_length=160)
    color: str = Field(max_length=80)
    shape: str = Field(max_length=100)
    confidence: float = Field(ge=0,le=1)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.component.strip() or not self.visible_state.strip():
            raise ValueError("empty spatial region")
        return self

class SpatialObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout: Literal["single_scene","multi_panel","uncertain"]
    panel_count: int = Field(ge=0,le=16)
    regions: list[VisualRegion] = Field(max_length=12)

    @model_validator(mode="after")
    def validate_layout(self):
        if ((self.layout=="single_scene" and self.panel_count!=1)
                or (self.layout=="multi_panel" and self.panel_count<2)
                or (self.layout=="uncertain" and self.panel_count!=0)):
            raise ValueError("inconsistent visual layout")
        if len({(r.component.casefold().strip(),r.position) for r in self.regions})!=len(self.regions):
            raise ValueError("duplicate spatial region")
        return self

# Provider schema deliberately uses supported JSON types, local model owns bounds.
SPATIAL_SCHEMA = {
    "type":"object","additionalProperties":False,
    "required":["layout","panel_count","regions"],
    "properties":{
        "layout":{"type":"string","enum":["single_scene","multi_panel","uncertain"]},
        "panel_count":{"type":"integer"},
        "regions":{"type":"array","items":{
            "type":"object","additionalProperties":False,
            "required":["component","position","visible_state","color","shape","confidence"],
            "properties":{
                "component":{"type":"string"},
                "position":{"type":"string","enum":["top_left","top","top_right","left","center","right","bottom_left","bottom","bottom_right","whole","uncertain"]},
                "visible_state":{"type":"string"},"color":{"type":"string"},
                "shape":{"type":"string"},"confidence":{"type":"number"},
            }}}
    }
}
