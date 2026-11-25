"""Bounded visual focus; user text is evidence, never sensor authority."""
from dataclasses import dataclass
import json
import re

MAX_FOCUS_CHARS = 12000
_DETAIL = re.compile(r"\b(?:ocr|transcrev\w*|transcri\w*|leg[ií]vel|ileg[ií]vel|screenshot|print|documento|c[oó]digo|interface|erro|regi[aã]o|detalh\w*|compare|diferen[çc]\w*)\b", re.I)


@dataclass(frozen=True, slots=True)
class VisualFocus:
    mode: str
    output_tokens: int
    literal: str

    def wire(self) -> str:
        # The full accepted caption is supplied, including trailing questions.
        return json.dumps({"mode": self.mode, "literal_data": self.literal},
                          ensure_ascii=False, separators=(",", ":"))


def visual_focus(text: str) -> VisualFocus:
    literal = text or ""
    if len(literal) > MAX_FOCUS_CHARS:
        raise ValueError("INCOMING_VISION_FOCUS_LIMIT")
    # Local classification controls only the observation budget, not meaning,
    # consent, capabilities or provider routing. No extra LLM call.
    if _DETAIL.search(literal):
        return VisualFocus("detailed", 2400, literal)
    if literal.strip():
        return VisualFocus("directed", 1400, literal)
    return VisualFocus("quick", 1400, literal)


def focus_instruction(focus: VisualFocus) -> str:
    if focus.mode == "detailed":
        return ("Priorize regiões e detalhes técnicos relevantes ao pedido, "
                "transcreva texto legível na ordem espacial, indique áreas ilegíveis "
                "e não complete caracteres por inferência. Descrição até 1200 caracteres; "
                "até 16 trechos legíveis e 8 incertezas, cada item até 240 caracteres. "
                "No campo spatial, indique layout single_scene, multi_panel ou uncertain. "
                "Só conte painéis se divisórias entre cenas realmente forem visíveis; uma interface, "
                "duas partes do rosto ou duas janelas de cor não comprovam uma montagem. "
                "panel_count=1 para cena única, >=2 para montagem observada, 0 se incerto. "
                "Liste até 12 regiões observáveis: component, position (top_left/top/top_right/left/center/right/"
                "bottom_left/bottom/bottom_right/whole/uncertain), visible_state, color, shape e confidence (0 a 1). "
                "Use coordenadas relativas à imagem, não ao corpo do personagem; "
                "quando solicitado compare abertura dos olhos, pálpebras, íris visível, "
                "inclinação e contornos. Não invente estruturas invisíveis. "
                "Se não distinguir uma região, declare incerteza em vez de adivinhar; "
                "cor e forma podem ficar vazias se indeterminadas. ")
    if focus.mode == "directed":
        return ("Leia a legenda inteira, inclusive perguntas e correções no final; "
                "priorize evidência visual relevante a elas. Descrição até 650 caracteres; "
                "até 8 trechos e 6 incertezas, cada item até 120 caracteres. ")
    return "Descreva os elementos visuais principais sem inventar detalhes. "
