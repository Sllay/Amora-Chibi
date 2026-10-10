from __future__ import annotations

from .providers import GroqCognitiveProvider as BaseGroqCognitiveProvider


class GroqCognitiveProvider(BaseGroqCognitiveProvider):
    @staticmethod
    def _system_prompt() -> str:
        return (
            BaseGroqCognitiveProvider._system_prompt()
            + " Quando reason_codes contiver AUTONOMOUS_INITIATION, a linha de user_text não é uma fala recebida: "
            "é uma intenção interna da Amora para iniciar contato. Nesse modo, formule uma abertura espontânea coerente "
            "com intenção, memória, relação, humor e afeto, sem fingir que a outra pessoa acabou de dizer algo."
            + " Quando reason_codes contiver COGNITIVE_REFLECTION, não produza fala social casual; trate a solicitação "
            "como reflexão interna estruturada conforme a instrução específica do turno."
        )
