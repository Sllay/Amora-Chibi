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
    if _REFERENCE.search(text):
        return "latest"
    return None
