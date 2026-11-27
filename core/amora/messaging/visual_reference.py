"""Explicit, conservative references to previously registered visual evidence."""
import re
import unicodedata

_IMAGE = r"(?:imagem|imagens|foto|fotos|print|prints|captura|capturas|screenshot|screenshots|fotografia|fotografias)"
_PREVIOUS = r"(?:anterior(?:es)?|passad[ao]s?|ultim[ao]s?|de antes|que (?:eu )?(?:mandei|enviei)(?: antes)?)"
_REFERENCE = re.compile(
    r"\b(?:" + _IMAGE + r"\s+" + _PREVIOUS + r"|" + _PREVIOUS + r"\s+" + _IMAGE + r")\b"
)
_IMAGE_WORD = re.compile(r"\b" + _IMAGE + r"\b")
# Do not resolve requests for an older ordinal to the latest registered image.
_OLDER = re.compile(r"\b(?:penultima|antepenultima|duas anteriores|primeira de ontem|de ontem|semana passada)\b")


_FIRST = re.compile(r"\b(?:primeir[ao]\s+" + _IMAGE + r"|" + _IMAGE + r"\s+primeir[ao])\b")
_LAST = re.compile(r"\b(?:ultim[ao]\s+" + _IMAGE + r"|" + _IMAGE + r"\s+ultim[ao])\b")


def visual_reference_intent(message):
    """A local routing hint, not permission to disclose or infer image content."""
    text = "".join(c for c in unicodedata.normalize("NFKD", message.text.casefold())
                   if not unicodedata.combining(c))
    if not _IMAGE_WORD.search(text):
        return None
    if message.reply_to_message_id and not message.reply_to_amora:
        return "reply"
    if _OLDER.search(text):
        return "unsupported"
    if _FIRST.search(text):
        return "first_latest" if _LAST.search(text) else "first"
    if _REFERENCE.search(text):
        return "latest"
    return None


_REVIEW = re.compile(r"\b(?:reanalise|reavali[ae]|revise|olh[ae] (?:de novo|novamente)|analise (?:de novo|novamente)|verifi[qc]ue novamente)\b")
_DISPUTE = re.compile(r"\b(?:voce (?:errou|se enganou|descreveu errado)|a (?:descricao|analise) (?:esta|ta|ficou) (?:errada|incorreta)|corrija (?:a|sua) (?:descricao|analise))\b")

def visual_review_intent(message):
    """Explicit review/dispute of a registered image, not an implicit guess."""
    if visual_reference_intent(message) not in {"latest","reply","first"}:
        return None
    text = "".join(c for c in unicodedata.normalize("NFKD",message.text.casefold())
                   if not unicodedata.combining(c))
    if _DISPUTE.search(text):
        return "dispute"
    if _REVIEW.search(text):
        return "review"
    return None
