"""Lossless EPUB markup patches and ordered extraction of other formats.

EPUB members are never parsed and reserialized for output. Only recorded text
spans change. Original encodings, tag spelling, namespace prefixes and attributes
are preserved. ZIP member order, compression, comments and metadata are retained.
"""
from __future__ import annotations

import codecs
import hashlib
import html
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from settings_manager import atomic_write


@dataclass(frozen=True)
class Unit:
    text: str
    label: str
    member: str = ""
    start: int = 0
    end: int = 0


@dataclass
class Book:
    path: Path
    kind: str
    digest: str
    units: list[Unit]
    metadata: dict[str, list[str]] = field(default_factory=dict)
    spine: list[str] = field(default_factory=list)
    toc: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    css: list[str] = field(default_factory=list)
    cover: list[str] = field(default_factory=list)
    documents: dict[str, tuple[str, str]] = field(default_factory=dict)
    original: bytes = b""

    def export(self, destination: Path, translated_units: dict[int, str]) -> None:
        if destination.resolve() == self.path.resolve():
            raise ValueError("Choose an output path different from the source.")
        if set(translated_units) != set(range(len(self.units))):
            raise ValueError("Every unit must be present before export.")
        if self.kind == "epub":
            self._export_epub(destination, translated_units)
        else:
            atomic_write(destination, "".join(translated_units[i]
                                             for i in range(len(self.units))))

    def _export_epub(self, destination: Path, translations: dict[int, str]) -> None:
        changes: dict[str, list[tuple[int, int, str]]] = {}
        for index, unit in enumerate(self.units):
            value = translations[index]
            if value != unit.text:
                # Reject XML control characters even when the model emits them.
                if any(not valid_xml_character(ord(c)) for c in value):
                    raise ValueError(f"Invalid XML character in unit {index}.")
                changes.setdefault(unit.member, []).append(
                    (unit.start, unit.end, html.escape(value, quote=False)))
        replacements = {}
        for name, patches in changes.items():
            text, encoding = self.documents[name]
            for start, end, value in sorted(patches, reverse=True):
                text = text[:start] + value + text[end:]
            replacements[name] = text.encode(encoding, errors="xmlcharrefreplace")
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.original)) as source:
            with zipfile.ZipFile(output, "w") as target:
                target.comment = source.comment
                for info in source.infolist():
                    target.writestr(info, replacements.get(info.filename,
                                                          source.read(info)))
        atomic_write(destination, output.getvalue())


def valid_xml_character(code: int) -> bool:
    return (code in (9, 10, 13) or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF)


def decode_markup(data: bytes) -> tuple[str, str]:
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith(codecs.BOM_UTF16_LE):
        encoding = "utf-16-le"
    elif data.startswith(codecs.BOM_UTF16_BE):
        encoding = "utf-16-be"
    else:
        match = re.search(br'<\?xml[^>]*encoding\s*=\s*[\'"]([^\'"]+)', data[:512], re.I)
        if not match:
            match = re.search(br'charset\s*=\s*[\'"]?([\w-]+)', data[:2048], re.I)
        encoding = match.group(1).decode("ascii") if match else "utf-8"
    return data.decode(encoding), encoding


class TextSpans(HTMLParser):
    """Use parser positions without letting a serializer touch the markup."""
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0]
        self.offsets.extend(match.end() for match in re.finditer("\n", source))
        self.stack: list[str] = []
        self.spans: list[tuple[int, int]] = []

    def position(self) -> int:
        row, column = self.getpos()
        return self.offsets[row - 1] + column

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        pass  # No stack change for XML empty elements.

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]

    def handle_data(self, data: str) -> None:
        if any(tag.split(":")[-1] in ("script", "style") for tag in self.stack):
            return
        start = self.position()
        if start == 0 and data.startswith("\ufeff"):
            # Retain the original UTF-16 byte order mark outside text patches.
            start += 1
            data = data[1:]
        if not data:
            return
        end = start + len(data)
        if self.spans and self.spans[-1][1] == start:
            self.spans[-1] = (self.spans[-1][0], end)
        else:
            self.spans.append((start, end))

    def handle_entityref(self, name: str) -> None:
        start = self.position()
        length = len(name) + 1
        if self.source[start + length:start + length + 1] == ";":
            length += 1
        self.handle_data(self.source[start:start + length])

    def handle_charref(self, name: str) -> None:
        start = self.position()
        length = len(name) + 2
        if self.source[start + length:start + length + 1] == ";":
            length += 1
        self.handle_data(self.source[start:start + length])

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA[") and data[6:].strip():
            raise ValueError("Visible CDATA text is unsupported; convert it to normal text first.")


def safe_xml(data: bytes) -> ET.Element:
    # Never resolve external entities or silently expand arbitrary internal DTDs.
    if re.search(br'<!ENTITY\s', data, re.I):
        raise ValueError("Custom XML entity declarations are unsupported.")
    return ET.fromstring(data)


def member_path(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise ValueError("Remote EPUB content is unsupported.")
    result = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parsed.path)))
    if result.startswith(("../", "/")) or result == "..":
        raise ValueError("Unsafe EPUB member path.")
    return result


def load_epub(path: Path, data: bytes) -> Book:
    book = Book(path, "epub", hashlib.sha256(data).hexdigest(), [], original=data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise ValueError("EPUB has duplicate ZIP members.")
        if sum(info.file_size for info in archive.infolist()) > 1024 ** 3:
            raise ValueError("EPUB expands beyond the 1 GiB safety limit.")
        if "META-INF/encryption.xml" in names:
            encryption = safe_xml(archive.read("META-INF/encryption.xml"))
            allowed = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")
            if any(e.get("Algorithm") not in allowed for e in encryption.iter()
                   if e.tag.endswith("EncryptionMethod")):
                raise ValueError("Encrypted/DRM content cannot be translated.")
        container = safe_xml(archive.read("META-INF/container.xml"))
        roots = container.findall(".//{*}rootfile")
        if len(roots) != 1:
            raise ValueError("EPUB must contain exactly one package/rootfile.")
        package_name = roots[0].attrib["full-path"]
        package = safe_xml(archive.read(package_name))
        metadata = package.find("{*}metadata")
        if metadata is not None:
            for entry in metadata:
                book.metadata.setdefault(entry.tag.split("}")[-1], []).append(
                    "".join(entry.itertext()))
        manifest = {}
        for item in package.findall("{*}manifest/{*}item"):
            name = member_path(package_name, item.attrib["href"])
            manifest[item.attrib["id"]] = (name, item.get("media-type", ""),
                                            item.get("properties", "").split())
        spine_element = package.find("{*}spine")
        if spine_element is None:
            raise ValueError("EPUB package has no spine.")
        book.spine = [manifest[item.attrib["idref"]][0] for item in spine_element]
        cover_id = next((e.get("content") for e in package.findall("{*}metadata/{*}meta")
                         if e.get("name") == "cover"), None)
        for identifier, (name, media, properties) in manifest.items():
            if media.startswith("image/"):
                book.images.append(name)
            if "font" in media or Path(name).suffix.lower() in (".otf", ".ttf", ".woff", ".woff2"):
                book.fonts.append(name)
            if media == "text/css":
                book.css.append(name)
            if "cover-image" in properties or identifier == cover_id:
                book.cover.append(name)
            if "nav" in properties or media == "application/x-dtbncx+xml":
                book.toc.append(name)
        html_members = [name for name, media, _ in manifest.values()
                        if media in ("application/xhtml+xml", "text/html")]
        html_members += [name for name in names
                         if Path(name).suffix.lower() in (".html", ".htm", ".xhtml")]
        ordered = list(dict.fromkeys([n for n in book.spine if n in html_members] + html_members))
        for name in ordered:
            text, encoding = decode_markup(archive.read(name))
            if re.search(r'<!ENTITY\s', text, re.I):
                raise ValueError("Custom HTML entities are unsupported.")
            parser = TextSpans(text)
            parser.feed(text)
            parser.close()
            book.documents[name] = (text, encoding)
            for start, end in parser.spans:
                value = html.unescape(text[start:end])
                if value.strip():
                    book.units.append(Unit(value, f"{name} · text {len(book.units)}", name, start, end))
    return book


def load_book(filename: str | Path) -> Book:
    path = Path(filename).expanduser().resolve()
    data = path.read_bytes()
    kind = path.suffix.lower().lstrip(".")
    if kind == "epub":
        book = load_epub(path, data)
    else:
        book = Book(path, kind, hashlib.sha256(data).hexdigest(), [])
        if kind == "txt":
            text = data.decode("utf-8-sig")
            book.units = [Unit(line, f"Line {i + 1}")
                          for i, line in enumerate(text.splitlines(keepends=True))]
        elif kind == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("Password-protected PDF: decrypt it before opening.")
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                book.units.append(Unit(text + "\n\f\n", f"Page {i + 1}"))
            book.metadata = {str(k): [str(v)] for k, v in (reader.metadata or {}).items()}
        elif kind == "docx":
            # XML paragraph order includes tables, nested tables and text boxes.
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                root = safe_xml(archive.read("word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            for p in root.iter(ns + "p"):
                pieces = []
                def visit(element: ET.Element) -> None:
                    for child in element:
                        if child.tag == ns + "p":
                            continue  # Nested text-box paragraph is visited separately.
                        if child.tag == ns + "t":
                            pieces.append(child.text or "")
                        elif child.tag in (ns + "br", ns + "cr"):
                            pieces.append("\n")
                        elif child.tag == ns + "tab":
                            pieces.append("\t")
                        else:
                            visit(child)
                visit(p)
                book.units.append(Unit("".join(pieces) + "\n", f"Paragraph {len(book.units) + 1}"))
        else:
            raise ValueError("Choose an EPUB, PDF, DOCX or UTF-8 TXT file.")
    if not any(unit.text.strip() for unit in book.units):
        raise ValueError("No extractable text. Scanned PDFs require OCR before import.")
    return book
