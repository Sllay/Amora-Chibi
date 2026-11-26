import base64
from io import BytesIO
from datetime import timedelta

from PIL import Image
from amora.messaging.models import InboundImageContent
from amora.messaging.visual_pixels import compare_pixels, VisualPreviewCache
from amora.cognition.visual_context import PixelComparison
from amora.time import SimulatedClock
from pydantic import ValidationError
import pytest


def photo(color, size=(8,8)):
    out=BytesIO()
    Image.new("RGBA",size,color).save(out,format="PNG")
    return InboundImageContent(attachment_id="123",content_mime="image/png",
                               content_base64=base64.b64encode(out.getvalue()).decode())


def test_pixels_exact_and_dimensions():
    a,b=photo((0,0,0,255)),photo((255,0,0,255))
    result=compare_pixels(a,b)
    assert result["changed_pixels"]==64
    assert result["changed_fraction"]==1
    assert result["mean_channel_delta"]==.25
    assert result["sampled"] is False
    PixelComparison.model_validate(result)
    assert compare_pixels(a,a)["changed_pixels"]==0
    mismatch=compare_pixels(a,photo((0,0,0,255),(4,8)))
    assert mismatch["status"]=="different_dimensions"
    assert "changed_fraction" not in mismatch
    PixelComparison.model_validate(mismatch)
    with pytest.raises(ValidationError):
        PixelComparison.model_validate({**result,"changed_pixels":0})


def test_sample_and_invalid_decode():
    a=photo((0,0,0,255),(600,600))
    result=compare_pixels(a,a)
    assert result["sample_size"]==[512,512] and result["sampled"]
    bad=a.model_copy(update={"content_base64":base64.b64encode(b"not a raster").decode()})
    assert compare_pixels(a,bad) is None


def test_preview_cache_scope_expiration_and_no_url():
    clock=SimulatedClock()
    cache=VisualPreviewCache(clock)
    image=photo((255,0,0,255)).model_copy(update={"source_url":"https://example.invalid/private"})
    key=("session","native",1)
    assert cache.put(key,image)
    assert cache.get(key).source_url is None
    assert cache.get(("other","native",1)) is None
    assert cache.get(("session","native",2)) is None
    clock.advance(timedelta(minutes=16))
    assert cache.get(key) is None
    for i in range(4):
        cache.put(("session",str(i),1),image)
    assert cache.get(("session","0",1)) is None
    assert cache.get(("session","3",1)) is not None
