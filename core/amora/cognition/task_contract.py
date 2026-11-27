"""Task-scoped evidence and lightweight quality diagnostics.

Only literal authorized context, no model-owned authority. Lexical coverage
is a diagnostic, NOT semantic proof. Never turn a user's task into Core policy.
"""
from __future__ import annotations
import re
import unicodedata

from .causal_conversation import literal_negative_constraints
from .surface_performance import detailed_output_requested
from amora.memory.context_selection import subject_terms

_CARRY = re.compile(
    r"\b(?:mantendo|mantenha|respeitando|respeite|preserve|preservando|sem mudar|"
    r"sem alterar|sem quebrar|com as mesmas)\b.{0,100}\b"
    r"(?:regras|restricoes|condicoes|limitacoes)\b|\b(?:regras|restricoes|condicoes)"
    r"\s+(?:anteriores|ja estabelecidas)\b")
_ECON = re.compile(r"\b(?:economia|economico|aldeias?|estoque|estoques|producao|comercio)\b")
_SHOCK = re.compile(r"\b(?:crise|crises|sobreviv\w*|impossibilitad\w*|interromp\w*|par[ae]r|"
                    r"paralis\w*|dias|contingencia|escassez)\b")
_LIST = re.compile(r"\b(?:inclua|incluindo|aborde|cubra|detalhe)\s+([^.!?;\n]{12,640})",re.I)


def fold(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD",str(value).casefold())
                    if not unicodedata.combining(c)).split())


def carried_constraints(context):
    if not (context.scene_visibility=="private" and context.remote_processing_allowed
            and context.privacy_scope.value=="PERSONAL" and context.speaker_id=="PERSON_BK"
            and context.target_id=="ENTITY_AMORA" and _CARRY.search(fold(context.user_text))):
        return []
    terms=subject_terms(context.user_text)
    # Only the most recent distinct user task, never sweep unrelated old
    # messages or accept assistant-generated restrictions as user authority.
    for row in reversed(context.recent_messages[-16:]):
        if row.get("speaker_id")!=context.speaker_id:
            continue
        if (row.get("addressee_id")!=context.target_id or row.get("status")!="received"
                or str(row.get("turn_id"))==str(context.turn_id)):
            continue
        text=row.get("text") or ""
        if not (terms & subject_terms(text)):
            return []
        return [{"source_turn":str(row.get("turn_id") or ""), "literal":entry["literal"]}
                for entry in literal_negative_constraints(text)][:6]
    return []


def economic_task(context):
    current=fold(context.user_text)
    return bool(context.scene_visibility=="private" and _ECON.search(current)
                and _SHOCK.search(current) and (literal_negative_constraints(context.user_text)
                                                or carried_constraints(context)))


def requested_topics(text):
    """Explicit comma-separated coverage items. No inferred requirements."""
    match=_LIST.search(text)
    if not match:
        return ()
    raw=re.split(r",\s*|\s+e\s+",match.group(1).strip())
    items=tuple(p.strip() for p in raw if 3<=len(p.strip())<=90)
    return items if 4<=len(items)<=14 and len(items)==len(raw) else ()


def task_guidance(context):
    notes=[]
    if carried_constraints(context):
        notes.append("O usuário pediu explicitamente manter as restrições anteriores. Use os trechos literais "
                     "marcados com o turno de origem; não trate como regra permanente ou altere outras condições.")
    if economic_task(context):
        notes.append("Para contingência econômica, mantenha a especialização exclusiva mesmo na crise. "
                     "Modele por recurso: estoque final = estoque inicial + produção + entradas - consumo - saídas - perdas. "
                     "Para uma interrupção de D dias, compare estoque utilizável e entradas externas realmente permitidas "
                     "com demanda acumulada essencial e perdas; transferências internas redistribuem, não criam recurso. "
                     "Sem valores, dê fórmulas, variáveis e cenários condicionais; não invente percentuais suficientes "
                     "nem prometa sobrevivência se a desigualdade de viabilidade não for satisfeita. "
                     "Explique estoques, racionamento, prioridades, comércio e retomada sem liberar produção proibida.")
    if detailed_output_requested(context):
        notes.append("Cada seção deve desenvolver mecanismos concretos e relações entre os componentes, "
                     "com critérios de validação observáveis. Evite reintroduzir o assunto a cada seção; "
                     "responda ao pedido completo antes de qualquer comentário de personalidade.")
    return " ".join(notes)


def removable_history_turn(context):
    # Only optional old exchanges with no lexical subject overlap. Preserve
    # latest two complete logical turns, every correction/restriction, and user-origin history
    # on explicit carryover tasks (even if some constraints weren't parsed).
    if detailed_output_requested(context) or _CARRY.search(fold(context.user_text)):
        return None
    rows=context.recent_messages
    # A logical turn can contain one input and several response segments.
    # Protect all rows of the two newest distinct turns, not a fixed count of
    # rows that may cut through an exchange. Missing IDs are never removed.
    if len(rows)<=2:
        return None
    protected=set()
    for row in reversed(rows):
        turn=str(row.get("turn_id") or "")
        if turn:
            protected.add(turn)
        if len(protected)==2:
            break
    if len(protected)<2:
        return None
    terms=subject_terms(context.user_text)
    if not terms:
        return None
    for row in rows:
        turn=str(row.get("turn_id") or "")
        if not turn or turn in protected:
            continue
        group=[r for r in rows if str(r.get("turn_id") or "")==turn]
        if not any(r.get("speaker_id")==context.speaker_id for r in group):
            continue
        if any(r.get("speaker_id") not in {context.speaker_id,context.target_id}
               or r.get("source")=="historical_owner_report" or
               terms & subject_terms(r.get("text") or "") or
               literal_negative_constraints(r.get("text") or "") for r in group):
            continue
        # A later correction can depend on an old named source: when causal
        # followups exist, prefer a compatible provider over removing history.
        if context.causal_followups:
            return None
        return turn
    return None


def repeated_passage(text,previous):
    def passages(value):
        chunks=re.split(r"\n+|(?<=[.!?])\s+",fold(value.replace("\n","\n")))
        # Long exact sentences are high-signal duplication, unlike repeated
        # technical nouns or short headings.
        return {part.strip() for part in chunks if len(part.strip())>=100}
    current=passages(text)
    return bool(current and any(current & passages(old) for old in previous))


def quality_reasons(context,text):
    if not (detailed_output_requested(context) or economic_task(context)):
        return ()
    notes=[]
    topics=requested_topics(context.user_text)
    if topics:
        normalized=fold(text)
        missing=sum(1 for topic in topics if not subject_terms(topic)
                    or not any(t in normalized for t in subject_terms(topic)))
        notes.append("TASK_COVERAGE_LEXICAL_GAPS" if missing else "TASK_COVERAGE_TERMS_PRESENT")
    if economic_task(context):
        normalized=fold(text)
        stock=bool(re.search(r"\b(?:estoqu\w*|reserv\w*)\b",normalized))
        demand=bool(re.search(r"\b(?:demanda|consumo|necessidade\w*)\b",normalized))
        time=bool(re.search(r"\b(?:dia\w*|periodo|duracao|tempo)\b",normalized))
        notes.append("TASK_ECONOMIC_VIABILITY_TERMS_PRESENT" if stock and demand and time
                     else "TASK_ECONOMIC_VIABILITY_UNVERIFIED")
    # Explicitly never claim semantic validation from these heuristics.
    return tuple([*notes,"TASK_SEMANTIC_COVERAGE_NOT_PROVEN"])
