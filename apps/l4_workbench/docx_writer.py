"""Minimal editable .docx writer using only the standard library.

Supports headings, paragraphs, bullet-like lines, simple tables and inline
images at their native aspect ratio. It exists so teachers can download a
working copy with every original figure; the workbench JSON remains the
structured source of truth.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


EMU_PER_INCH = 914400
PAGE_CONTENT_WIDTH_EMU = int(6.3 * EMU_PER_INCH)  # A4 with ~1 inch margins
MAX_IMAGE_HEIGHT_EMU = int(7.5 * EMU_PER_INCH)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Default Extension="jpeg" ContentType="image/jpeg"/>
<Default Extension="jpg" ContentType="image/jpeg"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="PingFang SC"/><w:sz w:val="21"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="160"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:rPr><w:i/><w:color w:val="667085"/><w:sz w:val="17"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:color="BFBFBF"/><w:left w:val="single" w:sz="4" w:color="BFBFBF"/><w:bottom w:val="single" w:sz="4" w:color="BFBFBF"/><w:right w:val="single" w:sz="4" w:color="BFBFBF"/><w:insideH w:val="single" w:sz="4" w:color="BFBFBF"/><w:insideV w:val="single" w:sz="4" w:color="BFBFBF"/>
</w:tblBorders></w:tblPr></w:style>
</w:styles>"""

DOCUMENT_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)


class DocxBuilder:
    def __init__(self) -> None:
        self._body: list[str] = []
        self._media: list[tuple[str, bytes]] = []
        self._rels: list[str] = []
        self.missing_images: list[str] = []

    # -- block-level content -------------------------------------------------
    def heading(self, text: str, level: int = 1) -> None:
        self._body.append(f'<w:p><w:pPr><w:pStyle w:val="Heading{min(max(level, 1), 3)}"/></w:pPr>{_run(text)}</w:p>')

    def paragraph(self, text: str, *, bold: bool = False, style: str | None = None, color: str | None = None) -> None:
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        self._body.append(f"<w:p>{style_xml}{_run(text, bold=bold, color=color)}</w:p>")

    def caption(self, text: str) -> None:
        self.paragraph(text, style="Caption")

    def bullets(self, items: list[str]) -> None:
        for item in items:
            self._body.append(f'<w:p><w:pPr><w:ind w:left="360" w:hanging="220"/></w:pPr>{_run("• " + item)}</w:p>')

    def table(self, rows: list[list[str]], *, header: bool = True) -> None:
        if not rows:
            return
        cells_xml = []
        for index, row in enumerate(rows):
            cells = "".join(
                f"<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr><w:p>{_run(str(cell), bold=header and index == 0)}</w:p></w:tc>"
                for cell in row
            )
            cells_xml.append(f"<w:tr>{cells}</w:tr>")
        self._body.append(
            '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/></w:tblPr>'
            + "".join(cells_xml) + "</w:tbl><w:p/>"
        )

    def image(self, path: Path, caption: str | None = None) -> bool:
        """Embed a PNG/JPEG at native aspect ratio; report (not fake) missing files."""
        try:
            data = Path(path).read_bytes()
            width_px, height_px, extension = _image_info(data)
        except (OSError, ValueError):
            self.missing_images.append(str(path))
            self.paragraph(f"【原题图缺失：{path}】", color="C73E43")
            return False
        index = len(self._media) + 1
        name = f"image{index}.{extension}"
        rel_id = f"rIdImg{index}"
        self._media.append((name, data))
        self._rels.append(
            f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{name}"/>'
        )
        width_emu = min(PAGE_CONTENT_WIDTH_EMU, int(width_px * EMU_PER_INCH / 144))
        height_emu = int(width_emu * height_px / max(width_px, 1))
        if height_emu > MAX_IMAGE_HEIGHT_EMU:
            height_emu = MAX_IMAGE_HEIGHT_EMU
            width_emu = int(height_emu * width_px / max(height_px, 1))
        self._body.append(
            '<w:p><w:pPr><w:keepNext/></w:pPr><w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{width_emu}" cy="{height_emu}"/>'
            f'<wp:docPr id="{index}" name="{escape(name)}"/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{index}" name="{escape(name)}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
        )
        if caption:
            self.caption(caption)
        return True

    def page_break(self) -> None:
        self._body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    # -- packaging -------------------------------------------------------------
    def to_bytes(self) -> bytes:
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {DOCUMENT_NS}><w:body>{''.join(self._body)}"
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1300" w:right="1300" w:bottom="1300" w:left="1300" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
            "</w:body></w:document>"
        )
        document_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            + "".join(self._rels) + "</Relationships>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES)
            archive.writestr("_rels/.rels", ROOT_RELS)
            archive.writestr("word/document.xml", document)
            archive.writestr("word/styles.xml", STYLES)
            archive.writestr("word/_rels/document.xml.rels", document_rels)
            for name, data in self._media:
                archive.writestr(f"word/media/{name}", data)
        return buffer.getvalue()


def _run(text: str, *, bold: bool = False, color: str | None = None) -> str:
    props = ""
    if bold or color:
        props = "<w:rPr>" + ("<w:b/>" if bold else "") + (f'<w:color w:val="{color}"/>' if color else "") + "</w:rPr>"
    parts = str(text).split("\n")
    runs = []
    for index, part in enumerate(parts):
        if index:
            runs.append(f"<w:r>{props}<w:br/></w:r>")
        runs.append(f'<w:r>{props}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(runs)


def _image_info(data: bytes) -> tuple[int, int, str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return width, height, "png"
    if data[:2] == b"\xff\xd8":
        index = 2
        while index < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in {0xC0, 0xC1, 0xC2}:
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return width, height, "jpeg"
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            index += 2 + length
    raise ValueError("unsupported image format")


def caption_for(block: dict[str, Any]) -> str:
    return str(block.get("caption") or block.get("source_locator") or "")
