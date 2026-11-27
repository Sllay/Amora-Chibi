"""Transport preview identity, not canonical visual memory."""
import hashlib
import logging
from .incoming_vision import valid_image_bytes

_LOG=logging.getLogger("amora.incoming_vision")

def short_hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:20]

def manifest_for(message):
    supplied={item.attachment_id:item.image for item in message.incoming_images}
    if message.incoming_image is not None:
        supplied[message.incoming_image.attachment_id]=message.incoming_image
    rows=[]
    for ref in message.attachments:
        if ref.attachment_id not in supplied and not (ref.media_type or "").lower().startswith("image/"):
            continue
        image=supplied.get(ref.attachment_id)
        entry={"number":len(rows)+1,"attachment_hash":short_hash(ref.attachment_id)}
        if image is not None:
            try:
                data=valid_image_bytes(image)
                entry.update(preview_hash=hashlib.sha256(data).hexdigest(),
                    mime=image.content_mime,byte_count=len(data))
            except Exception:
                pass
        rows.append(entry)
    return {"message_hash":short_hash(message.native_source_message_id),
            "revision":message.transport_revision,"sent_at":message.timestamp.isoformat(),
            "images":rows}

def validated_manifest(value):
    from amora.cognition.visual_context import VisualManifest
    try:
        return VisualManifest.model_validate(value).model_dump(mode="json",exclude_none=True)
    except (TypeError,ValueError):
        return None

def cache_key(session,manifest,number):
    if manifest is not None:
        for item in manifest["images"]:
            if item["number"]==number and item.get("preview_hash"):
                return (session,manifest["message_hash"],manifest["revision"],
                        item["attachment_hash"],item["preview_hash"])
    return None

def trace(stage,manifest,number,status):
    row=next((r for r in manifest["images"] if r["number"]==number),{})
    _LOG.info("visual_trace stage=%s msg_hash=%s revision=%d number=%d attachment_hash=%s preview_hash=%s bytes=%d mime=%s status=%s",
              stage,manifest["message_hash"],manifest["revision"],number,
              row.get("attachment_hash","-"),row.get("preview_hash","-")[:20],
              row.get("byte_count",0),row.get("mime","-"),status)

def reference_trace(stage,session,source,revision,status):
    _LOG.info("visual_reference_trace stage=%s session_hash=%s msg_hash=%s revision=%d status=%s",
              stage,short_hash(session),source,revision,status)
