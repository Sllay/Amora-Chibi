"""Bounded, private preview cache and deterministic raster comparison.

No disk, event ledger, URL retention or provider projection of raw pixels.
Metrics compare decoded transport previews, not necessarily CDN originals.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta
from io import BytesIO

from PIL import Image, ImageChops, ImageOps
from .incoming_vision import valid_image_bytes

MAX_PIXELS = 4_194_304
MAX_SIDE = 512
CACHE_BYTES = 5_000_000
CACHE_RECORDS = 3
CACHE_TTL = timedelta(minutes=15)


def raster(image):
    """Decode a single bounded frame; reject malformed/animated/oversized input."""
    try:
        with Image.open(BytesIO(valid_image_bytes(image))) as source:
            if (source.width * source.height > MAX_PIXELS
                    or getattr(source, "n_frames", 1) != 1
                    or source.format != {"image/png":"PNG","image/jpeg":"JPEG",
                                         "image/webp":"WEBP"}[image.content_mime]):
                return None
            oriented = ImageOps.exif_transpose(source)
            size = oriented.size
            # Fixed nearest-neighbor grid preserves actual sampled pixel values.
            oriented.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.NEAREST)
            return size, oriented.convert("RGBA")
    except Exception:
        return None


def compare_pixels(first, second):
    a, b = raster(first), raster(second)
    if a is None or b is None:
        return None
    size_a, pixels_a = a
    size_b, pixels_b = b
    result = {"source":"decoded_preview_pixels", "first_size":list(size_a),
              "second_size":list(size_b)}
    if size_a != size_b:
        return {**result, "status":"different_dimensions"}
    delta = ImageChops.difference(pixels_a, pixels_b)
    channels = delta.split()
    maximum = channels[0]
    for channel in channels[1:]:
        maximum = ImageChops.lighter(maximum, channel)
    total = pixels_a.width * pixels_a.height
    histogram = maximum.histogram()
    changed = total - histogram[0]
    energy = sum(i*n for channel in channels for i,n in enumerate(channel.histogram()))
    return {**result, "status":"compared", "sample_size":list(pixels_a.size),
            "sampled":pixels_a.size != size_a, "changed_pixels":changed,
            "changed_fraction":changed/total, "mean_channel_delta":energy/(total*4*255)}


class VisualPreviewCache:
    """Process-local FIFO/LRU cache. Lazy expiry; no promise of secure RAM erasure."""
    def __init__(self, clock):
        self.clock = clock
        self._entries = OrderedDict()

    def _purge(self):
        now = self.clock.now()
        for key, (when, _) in list(self._entries.items()):
            if not timedelta(0) <= now-when <= CACHE_TTL:
                self._entries.pop(key, None)

    def put(self, key, image):
        self._purge()
        if raster(image) is None:
            return False
        # Never keep signed CDN URLs beyond the original transport turn.
        preview = image.model_copy(update={"source_url":None})
        self._entries.pop(key, None)
        self._entries[key] = (self.clock.now(), preview)
        while (len(self._entries)>CACHE_RECORDS or
               sum(len(v.content_base64) for _,v in self._entries.values())>CACHE_BYTES):
            self._entries.popitem(last=False)
        return key in self._entries

    def get(self, key):
        self._purge()
        row = self._entries.get(key)
        if row is None:
            return None
        self._entries.move_to_end(key)
        return row[1]

    def discard(self, key):
        self._entries.pop(key, None)
