"""pdf_support: inspect / extract / rasterize / adapt_content + the capability flag."""

from __future__ import annotations

import base64
import io
import struct
import zlib

import pytest

from coworker import pdf_support
from coworker.providers.base import ModelCapabilities
from coworker.providers.capabilities import capabilities_for


def _blank_pdf_url(pages: int = 3) -> str:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return "data:application/pdf;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(autouse=True)
def _reset_mode():
    pdf_support.set_fallback_mode("text")
    yield
    pdf_support.set_fallback_mode("text")


# -- capability flag ------------------------------------------------------------


def test_native_three_have_pdf_capability():
    assert capabilities_for("gpt-5.6-sol").pdf
    assert capabilities_for("anthropic:claude-fable-5").pdf
    assert capabilities_for("gemini:gemini-2.5-pro").pdf


def test_compat_vendors_lack_pdf_capability():
    for model in (
        "zai:glm-5.2",
        "kimi:kimi-k2.6",
        "together:zai-org/GLM-5.2",
        "ollama:qwen3",
    ):
        assert not capabilities_for(model).pdf, model


# -- inspect --------------------------------------------------------------------


def test_inspect_counts_pages_and_bytes():
    result = pdf_support.inspect(_blank_pdf_url(pages=4))
    assert result["ok"] and result["pages"] == 4 and result["bytes"] > 0


def test_inspect_rejects_non_pdf():
    assert not pdf_support.inspect("data:image/png;base64,zz")["ok"]
    assert not pdf_support.inspect("plain text")["ok"]


# -- rasterize ------------------------------------------------------------------


def test_rasterize_produces_valid_pngs():
    urls = pdf_support.rasterize(_blank_pdf_url(pages=3), max_pages=2)
    assert urls is not None and len(urls) == 2
    png = base64.b64decode(urls[0].split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (400, 600)  # 200x300 page @ RASTER_SCALE=2
    channels = 4 if png[25] == 6 else 3
    idat = png.find(b"IDAT")
    (length,) = struct.unpack(">I", png[idat - 4 : idat])
    scanlines = zlib.decompress(png[idat + 4 : idat + 4 + length])
    assert len(scanlines) == height * (1 + width * channels)


# -- adapt_content --------------------------------------------------------------


def _file_part(url: str) -> dict:
    return {"type": "file", "file": {"filename": "doc.pdf", "file_data": url}}


def test_adapt_scanned_pdf_yields_visible_note():
    caps = ModelCapabilities(vision=False, pdf=False)
    out = pdf_support.adapt_content([_file_part(_blank_pdf_url())], caps)
    assert len(out) == 1 and out[0]["type"] == "text"
    assert "no extractable text" in out[0]["text"]


def test_adapt_images_mode_needs_vision():
    url = _blank_pdf_url(pages=2)
    pdf_support.set_fallback_mode("images")
    with_vision = pdf_support.adapt_content(
        [_file_part(url)], ModelCapabilities(vision=True, pdf=False)
    )
    assert [p["type"] for p in with_vision] == ["text", "image_url", "image_url"]
    without_vision = pdf_support.adapt_content(
        [_file_part(url)], ModelCapabilities(vision=False, pdf=False)
    )
    assert all(p["type"] == "text" for p in without_vision)  # degrades to text


def test_adapt_leaves_other_parts_alone():
    caps = ModelCapabilities(vision=False, pdf=False)
    parts = [{"type": "text", "text": "hi"}, _file_part(_blank_pdf_url())]
    out = pdf_support.adapt_content(parts, caps)
    assert out[0] == {"type": "text", "text": "hi"} and len(out) == 2


def test_set_fallback_mode_rejects_junk():
    assert pdf_support.set_fallback_mode("images") == "images"
    assert pdf_support.set_fallback_mode("bogus") == "text"


# -- the decode point is the bound --------------------------------------------


def test_pdf_bytes_refuses_oversized_before_decoding(monkeypatch):
    """The size cap must sit at the decode, not only at today's callers.

    `b64decode` expands 4 input characters into 3 bytes, so decoding first and
    validating after pays the allocation the cap exists to prevent. Assert the
    decode is *not reached*, which is the property a `len()` check in a caller
    cannot provide: a fourth caller would simply not have it.

    The cap is lowered here so the test stays cheap; the real one is 16M chars.
    """
    fits = _blank_pdf_url()
    monkeypatch.setattr(pdf_support, "_MAX_PDF_DATA_URL", len(fits) + 10)
    decoded: list[int] = []
    real = pdf_support.base64.b64decode

    def spy(data, *args, **kwargs):
        decoded.append(len(data))
        return real(data, *args, **kwargs)

    monkeypatch.setattr(pdf_support.base64, "b64decode", spy)

    oversized = "data:application/pdf;base64," + "A" * (len(fits) + 11)
    assert pdf_support._pdf_bytes(oversized) is None
    assert decoded == [], "oversized body reached b64decode"

    # ...and the cap must not reject bodies that fit.
    assert pdf_support._pdf_bytes(fits) is not None
    assert len(decoded) == 1


def test_pdf_bytes_cap_does_not_change_what_callers_already_reject():
    """The floor sits above the attach cap, so no input that works today changes.

    `attachments.MAX_PDF_CHARS` is what the message path enforces; the decode floor
    is a backstop for callers that have no cap, so it must be the looser of the two
    -- otherwise it would silently start rejecting PDFs that used to attach.
    """
    from coworker import attachments

    assert attachments.MAX_PDF_CHARS <= pdf_support._MAX_PDF_DATA_URL
