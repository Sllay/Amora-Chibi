"""Narrow directed comparison: assemble attributed sensor reports, not new facts."""
import re
import unicodedata

def directed_comparison(context):
    obs=context.visual_observation
    if not (context.scene_visibility=="private" and context.remote_processing_allowed
            and context.privacy_scope.value=="PERSONAL" and context.speaker_id=="PERSON_BK"
            and isinstance(obs,dict) and obs.get("observation_version")==4):
        return None
    text="".join(c for c in unicodedata.normalize("NFKD",context.user_text.casefold())
                 if not unicodedata.combining(c)).strip()
    # Do not replace multi-task or detailed questions with a generic report.
    if not re.fullmatch(r"(?:amora[, ]+)?(?:compare|compara)\s+(?:(?:esta|essa|a atual)(?: imagem)?\s+)?"
            r"(?:(?:com|a)\s+)?(?:a\s+)?(?:imagem|foto)\s+(?:anterior|primeira|passada)"
            r"[.!]?|(?:amora[, ]+)?(?:compare|compara)\s+a\s+primeira\s+(?:imagem|foto)\s+com\s+a\s+ultima(?:\s+(?:imagem|foto))?[.!]?",text):
        return None
    old=obs.get("historical")
    other=obs.get("current")
    second_label="Imagem atual"
    second_manifest=obs.get("current_manifest")
    if obs.get("reference_kind")=="first_latest" and other is None:
        other=obs.get("comparison_latest")
        second_label="Última imagem registrada"
        second_manifest=obs.get("latest_manifest")
    if not (old and other and obs.get("historical_manifest") and second_manifest):
        return None
    from .visual_context import visual_context_for_request
    if visual_context_for_request(obs,remote=True,private=True,mode="reply") is None:
        return None
    if old.get("observation_version")==3 or other.get("observation_version")==3:
        return None
    first_label="Primeira imagem registrada" if obs.get("reference_kind") in {"first","first_latest"} else "Imagem anterior"
    def report(label,value):
        if value.get("status")!="analyzed":
            return label+": não há descrição visual válida."
        desc=" ".join(value.get("description","").split())
        return label+" (relato do sensor): "+desc[:300]+("…" if len(desc)>300 else "")
    result=report(first_label,old)+" "+report(second_label,other)
    if old.get("status")==other.get("status")=="analyzed":
        result+=(" As descrições coincidem; isso não comprova imagens iguais." if old.get("description")==other.get("description")
                 else " Essas são as diferenças relatadas, não uma confirmação independente.")
    else:
        result+=" Não consigo confirmar a diferença sem uma observação válida das duas."
    return result
