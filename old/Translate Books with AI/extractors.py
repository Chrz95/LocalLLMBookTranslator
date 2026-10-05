import os
import zipfile
import logging
import shutil
from pathlib import Path
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

import settings_manager


def extract_epub_cover(epub_path):
    """
    Extract cover image from EPUB if available.
    Returns path to extracted cover or None.
    """
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            candidates = [
                "cover.jpg", "cover.jpeg", "cover.png",
                "images/cover.jpg", "images/cover.jpeg", "images/cover.png"
            ]

            for name in z.namelist():
                lower = name.lower()
                if any(lower.endswith(c) for c in candidates):
                    out_path = "cover_extracted" + os.path.splitext(lower)[1]
                    with open(out_path, "wb") as f:
                        f.write(z.read(name))
                    return out_path

            # Fallback: search for any image named 'cover'
            for name in z.namelist():
                if "cover" in name.lower() and name.lower().endswith((".jpg", ".jpeg", ".png")):
                    out_path = "cover_extracted" + os.path.splitext(name)[1]
                    with open(out_path, "wb") as f:
                        f.write(z.read(name))
                    return out_path

    except Exception as e:
        logging.error(f"Failed to extract EPUB cover: {e}")

    return None


def extract_units(input_file, file_type):
    """
    Extract text units from EPUB/PDF/DOCX/TXT.
    Now also extracts cover for EPUB.
    """
    settings_manager.last_input_file = input_file

    if file_type == "epub":
        book = epub.read_epub(input_file)
        units = []

        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), "html.parser")
                text = soup.get_text(separator="\n")
                units.append(text)

        cover = extract_epub_cover(input_file)
        return units, cover

    # TXT fallback
    if file_type == "txt":
        with open(input_file, "r", encoding="utf-8") as f:
            return [f.read()], None

    # PDF/DOCX placeholder (your existing logic)
    with open(input_file, "r", encoding="utf-8") as f:
        return [f.read()], None

def write_output(units, output_path, file_type, output_lang, cover=None):
    """
    Write final translated output.
    Supports EPUB cover reinsertion.
    """
    if file_type == "epub":
        return write_epub_output(units, output_path, cover)

    # fallback for txt/pdf/docx
    with open(output_path, "w", encoding="utf-8") as f:
        for u in units:
            f.write(u + "\n\n")
    return output_path
    
def write_epub_output(units, output_path, cover):
    """
    Create EPUB with preserved cover.
    """
    import ebooklib
    from ebooklib import epub

    book = epub.EpubBook()

    # Metadata
    book.set_identifier("translated-book")
    book.set_title("Translated Book")
    book.set_language("en")

    # Add cover if available
    if cover and os.path.exists(cover):
        with open(cover, "rb") as f:
            book.set_cover(os.path.basename(cover), f.read())

    # Add chapters
    chapters = []
    for i, text in enumerate(units):
        c = epub.EpubHtml(title=f"Chapter {i+1}", file_name=f"chap_{i+1}.xhtml", lang="en")
        c.content = f"<html><body><p>{text.replace('\n', '<br/>')}</p></body></html>"
        book.add_item(c)
        chapters.append(c)

    # TOC
    book.toc = tuple(chapters)

    # Spine
    book.spine = ["nav"] + chapters

    # Required items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(output_path, book, {})
    return output_path
