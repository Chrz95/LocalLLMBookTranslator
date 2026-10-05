"""Offline integration tests: real ZIPs, parsers, files, threads and HTTP."""
from __future__ import annotations

import io
import codecs
import json
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

from extractors import load_book
from lmstudio import APIError, LMStudioClient, TRANSLATION_PROMPT
from settings_manager import Settings
from translator import Job, MAX_CHARS, TranslationEngine, split_unit


class FakeClient:
    def __init__(self):
        self.calls = []
        self.checks = 0
        self.failures = 0
        self.lock = threading.Lock()

    def translate(self, text):
        with self.lock:
            self.calls.append(text)
            if self.failures:
                self.failures -= 1
                raise APIError("temporary failure")
        return text.upper()

    def check(self, source, translation):
        self.checks += 1
        return {"issues": []}


def make_epub(path: Path):
    chapter = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Kapitel</title>
<link href="style.css" rel="stylesheet"/><style>.x:after {content: "keep";}</style></head>
<body data-x="a > b"><h1>Ein Titel</h1><p>  Hallo &amp; Welt <span class='x'>lieber</span> Freund!<br/>Ende.</p>
<!-- untouched --><img src="cover.png" alt="Unchanged"/><svg xmlns="http://www.w3.org/2000/svg"><text>Grafik</text><path d="M0 0"/></svg>
<script>const x = "unchanged";</script></body></html>'''
    container = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>'
    package = '''<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Book</dc:title><dc:creator>Author</dc:creator><dc:publisher>Publisher</dc:publisher><dc:identifier id="id">urn:isbn:123</dc:identifier><dc:rights>All rights</dc:rights><dc:language>de</dc:language></metadata>
<manifest><item id="c2" href="two.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="one.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/><item id="font" href="font.otf" media-type="font/otf"/><item id="css" href="style.css" media-type="text/css"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>
<spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine></package>'''
    members = {"mimetype": b"application/epub+zip", "META-INF/container.xml": container.encode(),
               "OPS/book.opf": package.encode(), "OPS/one.xhtml": chapter.encode(),
               "OPS/two.xhtml": b'<html><body><p>Zweites Kapitel.</p></body></html>',
               "OPS/nav.xhtml": b'<html><body><nav><a href="one.xhtml">Kapitel eins</a></nav></body></html>',
               "OPS/cover.png": b"\x89PNG fake binary bytes", "OPS/font.otf": b"font binary data",
               "OPS/style.css": b"p { font-family: custom; }", "OPS/toc.ncx": b'<ncx><text>Original TOC</text></ncx>'}
    with zipfile.ZipFile(path, "w") as archive:
        archive.comment = b"original archive comment"
        for index, (name, data) in enumerate(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 2, 3, 4, 5, 6))
            info.compress_type = zipfile.ZIP_STORED if index == 0 else zipfile.ZIP_DEFLATED
            info.comment = b"member comment"
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return members


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(model="offline-test", retries=1, timeout=2)

    def text_job(self, text="eins\nzwei\ndrei\n", settings=None):
        path = self.root / "book.txt"
        path.write_text(text, encoding="utf-8")
        return Job(load_book(path), settings or self.settings, self.root / "jobs")

    def test_split_is_lossless_and_bounded(self):
        for text in ("  Hallo. Welt!\n" * 1000, "word" * 3000, "\t \n" * 3000,
                     "a" * 3999 + "  Next paragraph.\n", "😀" * 4100):
            parts = list(split_unit(text))
            self.assertEqual(text, "".join(left + core + right for core, left, right in parts))
            self.assertTrue(all(len(core) <= MAX_CHARS for core, _, _ in parts))

    def test_resume_retranslate_checks_and_job_isolation(self):
        job = self.text_job(settings=replace(self.settings, proofread=True))
        client = FakeClient()
        events = []
        engine = TranslationEngine(job, events.append, client)
        engine.run()
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.checks, 3)
        engine.run()
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.checks, 3)
        self.assertEqual(sum(e.get("status") == "Resumed" for e in events), 3)
        job.chunk_path(1).write_text("wrong", encoding="utf-8")
        engine.retranslate_single_chunk(1)
        self.assertEqual(job.read_translation(1), "ZWEI")
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(client.checks, 4)
        self.assertTrue(any(e.get("status") == "Retranslated" for e in events))
        self.assertEqual(job.index_map, {0: 0, 1: 1, 2: 2})
        other = Job(job.book, replace(self.settings, output_lang="Greek"), self.root / "jobs")
        self.assertNotEqual(job.directory, other.directory)
        out = self.root / "out.txt"
        job.export(out)
        self.assertEqual(out.read_text(), "EINS\nZWEI\nDREI\n")
        self.assertFalse(any(p.name == "resume_progress.json" for p in self.root.rglob("*")))

    def test_epub_assets_order_and_markup_are_preserved(self):
        path = self.root / "book.epub"
        original = make_epub(path)
        book = load_book(path)
        self.assertEqual(book.spine, ["OPS/one.xhtml", "OPS/two.xhtml"])
        self.assertEqual(book.metadata["creator"], ["Author"])
        self.assertEqual(book.cover, ["OPS/cover.png"])
        self.assertEqual(book.fonts, ["OPS/font.otf"])
        self.assertFalse(any("const x" in u.text or "font-family" in u.text for u in book.units))
        job = Job(book, self.settings, self.root / "jobs")
        TranslationEngine(job, client=FakeClient()).run()
        output = self.root / "translated.epub"
        job.export(output)
        with zipfile.ZipFile(path) as before, zipfile.ZipFile(output) as after:
            self.assertEqual(before.namelist(), after.namelist())
            self.assertEqual(before.comment, after.comment)
            for old, new in zip(before.infolist(), after.infolist()):
                for attribute in ("compress_type", "date_time", "external_attr", "comment", "extra"):
                    self.assertEqual(getattr(old, attribute), getattr(new, attribute))
            for name, value in original.items():
                if name not in book.documents:
                    self.assertEqual(value, after.read(name))
                else:
                    # Delete just the original/replaced node spans: the remainder
                    # must be exactly the same, including quote style and comments.
                    source = value.decode()
                    actual = after.read(name).decode()
                    expected = source
                    import html
                    replacements = [(u.start, u.end, html.escape(u.text.upper(), quote=False))
                                    for u in book.units if u.member == name]
                    for start, end, replacement in sorted(replacements, reverse=True):
                        expected = expected[:start] + replacement + expected[end:]
                    self.assertEqual(expected, actual)
            ET.fromstring(after.read("OPS/one.xhtml"))

    def test_plaintext_is_escaped_in_epub(self):
        path = self.root / "book.epub"
        make_epub(path)
        book = load_book(path)
        translated = {i: 'a < b & "quoted"' for i in range(len(book.units))}
        output = self.root / "out.epub"
        book.export(output, translated)
        with zipfile.ZipFile(output) as archive:
            root = ET.fromstring(archive.read("OPS/one.xhtml"))
            self.assertIn('a < b & "quoted"', "".join(root.itertext()))

    def test_utf16_and_legacy_markup_encoding(self):
        for encoding, bom, declaration in (("utf-16-be", codecs.BOM_UTF16_BE, "UTF-16"),
                                            ("utf-16-le", codecs.BOM_UTF16_LE, "UTF-16"),
                                            ("iso-8859-1", b"", "ISO-8859-1")):
            with self.subTest(encoding=encoding):
                path = self.root / "encoded.epub"
                members = make_epub(path)
                markup = (f'<?xml version="1.0" encoding="{declaration}"?>'
                          '<html><body><p class="same">Hallo</p></body></html>')
                members["OPS/one.xhtml"] = bom + markup.encode(encoding)
                with zipfile.ZipFile(path, "w") as archive:
                    for name, value in members.items():
                        archive.writestr(name, value)
                book = load_book(path)
                self.assertFalse(any("\ufeff" in unit.text for unit in book.units))
                values = {i: ("Γειά" if u.member == "OPS/one.xhtml" else u.text)
                          for i, u in enumerate(book.units)}
                out = self.root / "encoded_out.epub"
                book.export(out, values)
                with zipfile.ZipFile(out) as archive:
                    result = archive.read("OPS/one.xhtml")
                expected = bom + markup.replace("Hallo", "Γειά").encode(
                    encoding, errors="xmlcharrefreplace")
                self.assertEqual(result, expected)
                self.assertEqual(ET.fromstring(result).find("body/p").text, "Γειά")

    def test_missing_or_empty_chunk_never_exports(self):
        job = self.text_job()
        with self.assertRaises(ValueError):
            job.export(self.root / "missing.txt")
        job.chunk_path(0).write_text("", encoding="utf-8")
        client = FakeClient()
        events = []
        TranslationEngine(job, events.append, client).run()
        self.assertNotIn("eins", client.calls)
        self.assertTrue(any(e.get("index") == 0 and e.get("status") == "Error" for e in events))
        with self.assertRaises(ValueError):
            job.export(self.root / "empty.txt")

    def test_retry_and_skipped_status(self):
        job = self.text_job("eins\n\n")
        client = FakeClient()
        client.failures = 1
        events = []
        TranslationEngine(job, events.append, client).run()
        self.assertTrue(any(e.get("status") == "Retry" for e in events))
        self.assertTrue(any(e.get("status") == "Skipped" for e in events))
        self.assertEqual(job.read_translation(0), "EINS")

    def test_pause_cancel_and_resume_retains_only_committed_files(self):
        job = self.text_job(settings=replace(self.settings, max_workers=1))
        entered, release = threading.Event(), threading.Event()
        class BlockingClient(FakeClient):
            def translate(self, text):
                entered.set()
                release.wait(3)
                return super().translate(text)
        client = BlockingClient()
        engine = TranslationEngine(job, client=client)
        worker = threading.Thread(target=engine.run)
        worker.start()
        self.assertTrue(entered.wait(2))
        engine.pause()
        release.set()
        time.sleep(0.15)
        self.assertFalse(job.chunk_path(0).exists())
        engine.cancel()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(job.chunk_path(0).exists())
        engine.run()
        self.assertTrue(all(job.chunk_path(i).exists() for i in range(3)))

    def test_failed_retranslation_keeps_previous_file(self):
        job = self.text_job("eins\n", replace(self.settings, retries=0))
        client = FakeClient()
        engine = TranslationEngine(job, client=client)
        engine.run()
        client.failures = 1
        engine.retranslate_single_chunk(0)
        self.assertEqual(job.read_translation(0), "EINS")

    def test_docx_table_and_paragraph_order(self):
        path = self.root / "book.docx"
        xml = b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>First</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t><w:tab/><w:t>text</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>Last</w:t></w:r></w:p></w:body></w:document>'''
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", xml)
        book = load_book(path)
        self.assertEqual([u.text for u in book.units], ["First\n", "Cell\ttext\n", "Last\n"])

    def test_pdf_extracts_in_page_order(self):
        # Generate a small valid PDF using only the standard library.
        stream = b"BT /F1 12 Tf 72 720 Td (First page) Tj ET"
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
                   b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                   b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
                   b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                   b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
        data = io.BytesIO()
        data.write(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(data.tell())
            data.write(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = data.tell()
        data.write(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]:
            data.write(f"{offset:010d} 00000 n \n".encode())
        data.write(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
        path = self.root / "book.pdf"
        path.write_bytes(data.getvalue())
        self.assertIn("First page", load_book(path).units[0].text)

    def test_http_payload_truncation_and_check_validation(self):
        requests = []
        replies = [("stop", "Translated"), ("length", "Partial"),
                   ("stop", '{"issues": []}'), ("stop", '{"issues":"bad"}')]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                reason, content = replies.pop(0)
                body = json.dumps({"choices": [{"finish_reason": reason, "message": {"content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, format, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = LMStudioClient(self.settings, f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
            self.assertEqual(client.translate("Hallo"), "Translated")
            self.assertEqual(requests[0][0], "/v1/chat/completions")
            payload = requests[0][1]
            self.assertEqual(set(payload), {"model", "messages"})
            self.assertEqual(payload["messages"][1], {"role": "user", "content": "Hallo"})
            self.assertEqual(payload["messages"][0]["content"], TRANSLATION_PROMPT.replace(
                "<input_lang>", "German").replace("<output_lang>", "English"))
            with self.assertRaises(APIError):
                client.translate("Hallo")
            self.assertEqual(client.check("Hallo", "Hello"), {"issues": []})
            with self.assertRaises(APIError):
                client.check("Hallo", "Hello")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
