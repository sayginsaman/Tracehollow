"""Synthetic PDF documents written byte by byte for document-processing tests.

No real documents are used. Text pages use the standard Helvetica font (ASCII text); image pages
embed an uncompressed greyscale raster; hostile pages carry JavaScript, launch and URI actions
that must never be executed or followed.
"""

from __future__ import annotations

import io
import zlib


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _assemble(objects: list[bytes]) -> bytes:
    """Serialise numbered objects (1..n, object 1 is the catalog) with a valid xref table."""
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return out.getvalue()


def _stream(data: bytes, extra: str = "", compress: bool = False) -> bytes:
    if compress:
        data = zlib.compress(data)
        extra += " /Filter /FlateDecode"
    return f"<< /Length {len(data)}{extra} >>\nstream\n".encode("ascii") + data + b"\nendstream"


def build_pdf(pages: list[dict[str, object]], *, catalog_extra: str = "") -> bytes:
    """Pages are dicts with optional ``text`` (list of lines), ``image`` ((w, h, grey bytes))
    and ``annotations`` (raw annotation dictionaries)."""
    # Object layout: 1 catalog, 2 page tree, 3 font, then per page: page, content, [image].
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for page in pages:
        page_number = len(objects) + 1
        content_number = page_number + 1
        kids.append(f"{page_number} 0 R")
        commands = []
        lines = page.get("text") or []
        assert isinstance(lines, list)
        y = 720
        for line in lines:
            commands.append(f"BT /F1 12 Tf 72 {y} Td ({_escape(str(line))}) Tj ET")
            y -= 18
        resources = "/Font << /F1 3 0 R >>"
        image = page.get("image")
        image_number = None
        if image is not None:
            assert isinstance(image, tuple)
            width, height, _pixels = image
            image_number = content_number + 1
            resources += f" /XObject << /Im1 {image_number} 0 R >>"
            box = page.get("image_box") or (36, 36, width, height)
            assert isinstance(box, tuple)
            commands.append(f"q {box[2]} 0 0 {box[3]} {box[0]} {box[1]} cm /Im1 Do Q")
        annots = page.get("annotations") or []
        assert isinstance(annots, list)
        annot_text = f" /Annots [{' '.join(str(a) for a in annots)}]" if annots else ""
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << {resources} >> /Contents {content_number} 0 R{annot_text} >>"
            ).encode("ascii")
        )
        objects.append(_stream("\n".join(commands).encode("latin-1")))
        if image is not None:
            width, height, pixels = image
            assert isinstance(pixels, bytes)
            objects.append(
                _stream(
                    pixels,
                    f" /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                    "/ColorSpace /DeviceGray /BitsPerComponent 8",
                    compress=True,
                )
            )
    objects[0] = f"<< /Type /Catalog /Pages 2 0 R{catalog_extra} >>".encode("ascii")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode("ascii")
    return _assemble(objects)


def text_pdf(*pages: list[str]) -> bytes:
    return build_pdf([{"text": lines} for lines in pages])


def noise_image(width: int = 64, height: int = 64) -> tuple[int, int, bytes]:
    return width, height, bytes((x * 7 + y * 13) % 256 for y in range(height) for x in range(width))


def image_only_pdf(pages: int = 1) -> bytes:
    return build_pdf([{"image": noise_image()} for _ in range(pages)])


def hostile_pdf() -> bytes:
    """Active content that a renderer must not execute and a parser must not follow."""
    annotation = (
        "<< /Type /Annot /Subtype /Link /Rect [0 0 100 100] "
        "/A << /S /URI /URI (http://198.51.100.7/beacon) >> >>"
    )
    return build_pdf(
        [
            {
                "text": [
                    "Quarterly note <script>alert(1)</script>",
                    "Contact desk at example.org",
                ],
                "annotations": [annotation],
            }
        ],
        catalog_extra=(
            " /OpenAction << /S /JavaScript /JS (app.launchURL\\('http://198.51.100.7/x'\\);) >>"
            " /AA << /WC << /S /Launch /F (calc.exe) >> >>"
        ),
    )


def encrypted_pdf(
    user_password: str = "synthetic-fixture",  # noqa: S107 - synthetic test document
    owner_password: str | None = None,
) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(text_pdf(["Protected synthetic page"]))))
    writer.encrypt(user_password, owner_password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def malformed_pdf() -> bytes:
    return b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >>\nthis is not a PDF body\n%%EOF"


def scanned_text_pdf(lines: list[str]) -> bytes:
    """A page whose only content is an image of rendered text (like a scan), for real OCR."""
    import pypdfium2 as pdfium

    source = pdfium.PdfDocument(build_pdf([{"text": lines}]))
    try:
        bitmap = source[0].render(scale=200 / 72, grayscale=True)
        width, height, stride = bitmap.width, bitmap.height, bitmap.stride
        buffer = bytes(bitmap.buffer)
        pixels = b"".join(buffer[y * stride : y * stride + width] for y in range(height))
    finally:
        source.close()
    # The image covers the whole page, as a scanner would produce.
    return build_pdf([{"image": (width, height, pixels), "image_box": (0, 0, 612, 792)}])
