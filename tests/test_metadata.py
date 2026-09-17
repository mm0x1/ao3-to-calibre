"""Tests for AO3 page parsing and EPUB metadata."""

import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from ao3archiver.metadata import (
    _calibre_user_metadata,
    AO3Metadata,
    canonical_rating,
    enrich_epub,
    enrich_epub_portable,
    epub_rating_subjects,
    has_ao3_metadata,
    metadata_from_csv_row,
    opf_prefixed_element_names,
    parse_ao3_metadata,
    read_ao3_metadata,
    has_calibre_user_metadata,
    read_calibre_user_metadata,
    read_epub_package,
    read_front_page_rating,
    repair_epub,
    validate_epub_file,
)
from tests.support import create_epub as create_book, front_page


AO3_PAGE = """
<dl class="stats">
  <dt class="published">Published:</dt><dd class="published">2010-02-22</dd>
  <dt class="category">Categories:</dt><dd class="category tags"><a>F/F</a><a>M/M</a></dd>
  <dt class="words">Words:</dt><dd class="words">1,315</dd>
  <dt class="chapters">Chapters:</dt><dd class="chapters">1/1</dd>
  <dt class="kudos">Kudos:</dt><dd class="kudos">68</dd>
  <dt class="bookmarks">Bookmarks:</dt><dd class="bookmarks"><a>6</a></dd>
  <dt class="hits">Hits:</dt><dd class="hits">1,040</dd>
</dl>
"""


def create_epub(path):
    container = b'''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    package = b'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Example Work</dc:title>
    <dc:creator>Example Author</dc:creator>
    <dc:description>Summary.</dc:description>
  </metadata>
</package>'''

    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", container)
        epub.writestr("content.opf", package)


class AO3MetadataTest(unittest.TestCase):
    def test_parse_ao3_statistics(self):
        metadata = parse_ao3_metadata(AO3_PAGE, "https://archiveofourown.org/works/64805")

        self.assertEqual(metadata.work_id, "64805")
        self.assertEqual(metadata.kudos, 68)
        self.assertEqual(metadata.bookmarks, 6)
        self.assertEqual(metadata.hits, 1040)
        self.assertEqual(metadata.words, 1315)
        self.assertEqual(metadata.chapters, "1/1")
        self.assertEqual(metadata.category, "F/F, M/M")

    def test_parse_ao3_category_is_optional(self):
        metadata = parse_ao3_metadata(
            '<dl class="stats"><dd class="words">10</dd></dl>',
            "https://archiveofourown.org/works/64805",
        )

        self.assertIsNone(metadata.category)

    def test_metadata_from_ao3downloader_csv_row(self):
        metadata = metadata_from_csv_row(
            {
                "link": "https://archiveofourown.org/works/64805",
                "title": "Cursed",
                "author": "Medie",
                "words": "1,315",
                "chapters": "1/1",
                "comments": "0",
                "kudos": "68",
                "bookmarks": "6",
                "hits": "1,040",
                "complete": "True",
                "categories": "F/F, M/M",
            },
            "https://archiveofourown.org/works/64805",
        )

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.kudos, 68)
        self.assertEqual(metadata.hits, 1040)
        self.assertEqual(metadata.status, "Complete")
        self.assertEqual(metadata.category, "F/F, M/M")

    def test_enrich_and_read_epub(self):
        metadata = AO3Metadata(
            work_id="64805",
            work_url="https://archiveofourown.org/works/64805",
            published="2010-02-22",
            words=1315,
            chapters="1/1",
            kudos=68,
            bookmarks=6,
            hits=1040,
        )

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            enrich_epub(epub_path, metadata)

            self.assertTrue(has_ao3_metadata(epub_path))
            actual = read_ao3_metadata(epub_path)
            self.assertEqual(actual.work_url, metadata.work_url)
            self.assertEqual(actual.kudos, 68)
            self.assertEqual(actual.title, "Example Work")
            self.assertEqual(actual.authors, ("Example Author",))

            with zipfile.ZipFile(epub_path) as epub:
                package = epub.read("content.opf").decode("utf-8")
                self.assertIn("ao3:kudos", package)
                self.assertIn("AO3 statistics", package)
                self.assertEqual(epub.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)

    def test_portable_enrichment_preserves_standard_metadata(self):
        metadata = AO3Metadata(
            work_id="64805",
            work_url="https://archiveofourown.org/works/64805",
            category="F/F, M/M",
            kudos=68,
            local_words=1234,
            local_gfog=8.456,
        )

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            enrich_epub_portable(epub_path, metadata)

            actual = read_ao3_metadata(epub_path)
            with zipfile.ZipFile(epub_path) as epub:
                package = epub.read("content.opf").decode("utf-8")

        self.assertEqual(actual.category, "F/F, M/M")
        self.assertEqual(actual.local_words, 1234)
        self.assertEqual(actual.local_gfog, 8.46)
        self.assertEqual(actual.title, "Example Work")
        self.assertEqual(actual.authors, ("Example Author",))
        self.assertIn("Summary.", package)
        self.assertNotIn("AO3 statistics", package)
        self.assertNotIn("scheme=\"ao3\"", package)

    def test_enrichment_replaces_existing_statistics_block(self):
        original = AO3Metadata(
            work_id="64805",
            work_url="https://archiveofourown.org/works/64805",
            kudos=68,
        )
        refreshed = AO3Metadata(
            work_id="64805",
            work_url="https://archiveofourown.org/works/64805",
            kudos=70,
        )

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            enrich_epub(epub_path, original)
            enrich_epub(epub_path, refreshed)

            with zipfile.ZipFile(epub_path) as epub:
                package = epub.read("content.opf").decode("utf-8")
                self.assertEqual(package.count("AO3 statistics"), 1)
            self.assertEqual(read_ao3_metadata(epub_path).kudos, 70)

    def test_refresh_removes_missing_counters_and_preserves_permissions(self):
        complete = AO3Metadata(
            work_id="64805",
            work_url="https://archiveofourown.org/works/64805",
            kudos=68,
            hits=1040,
        )
        partial = AO3Metadata(
            work_id="64805",
            work_url="https://archiveofourown.org/works/64805",
            kudos=70,
        )

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            os.chmod(epub_path, 0o644)
            enrich_epub(epub_path, complete)
            enrich_epub(epub_path, partial)

            validate_epub_file(epub_path)
            actual = read_ao3_metadata(epub_path)
            self.assertEqual(actual.kudos, 70)
            self.assertIsNone(actual.hits)
            self.assertEqual(stat.S_IMODE(os.stat(epub_path).st_mode), 0o644)


class OmittedZeroCountsTest(unittest.TestCase):
    def test_counts_missing_from_a_stats_block_with_hits_are_zero(self):
        metadata = parse_ao3_metadata(
            '<dl class="stats"><dd class="words">10</dd><dd class="hits">5</dd></dl>',
            "https://archiveofourown.org/works/64805",
        )

        self.assertEqual((metadata.kudos, metadata.comments, metadata.bookmarks), (0, 0, 0))
        self.assertEqual(metadata.hits, 5)

    def test_present_counts_are_kept(self):
        metadata = parse_ao3_metadata(AO3_PAGE, "https://archiveofourown.org/works/64805")

        self.assertEqual((metadata.kudos, metadata.bookmarks), (68, 6))

    def test_nothing_is_defaulted_when_hits_is_absent(self):
        metadata = parse_ao3_metadata(
            '<dl class="stats"><dd class="words">10</dd></dl>',
            "https://archiveofourown.org/works/64805",
        )

        self.assertIsNone(metadata.kudos)
        self.assertIsNone(metadata.comments)


class StatisticsBlockTest(unittest.TestCase):
    def test_floats_are_rounded_for_reading_while_the_column_keeps_the_exact_value(self):
        exact = 12.317393179326084
        item = AO3Metadata("64805", "https://archiveofourown.org/works/64805", local_gfog=exact)

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            enrich_epub(epub_path, item, calibre_columns=(("gfog", "Gfog", "float", "local_gfog"),))
            with zipfile.ZipFile(epub_path) as archive:
                content = archive.read("content.opf").decode("utf-8")

            columns = read_calibre_user_metadata(epub_path)

        self.assertIn("Gunning Fog: 12.32&lt;", content)
        self.assertEqual(columns["gfog"], exact)

    def test_prefixed_column_entries_are_not_counted_as_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            with zipfile.ZipFile(epub_path) as archive:
                package = archive.read("content.opf").decode("utf-8")
            package = package.replace(
                "</metadata>",
                '<opf:meta xmlns:opf="http://www.idpf.org/2007/opf" name="calibre:user_metadata:#pages" '
                'content="{&quot;#value#&quot;: 5}"/></metadata>')
            rewritten = f"{directory}/rewritten.epub"
            with zipfile.ZipFile(epub_path) as source, zipfile.ZipFile(rewritten, "w") as target:
                for entry in source.infolist():
                    target.writestr(entry, package.encode("utf-8") if entry.filename == "content.opf" else source.read(entry))

            self.assertEqual(read_calibre_user_metadata(rewritten), {})

    def test_entries_calibre_writes_under_a_default_namespace_are_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            with zipfile.ZipFile(epub_path) as archive:
                package = archive.read("content.opf").decode("utf-8")
            package = package.replace(
                "</metadata>",
                '<meta name="calibre:user_metadata:#pages" content="{&quot;#value#&quot;: 5}"/></metadata>')
            rewritten = f"{directory}/rewritten.epub"
            with zipfile.ZipFile(epub_path) as source, zipfile.ZipFile(rewritten, "w") as target:
                for entry in source.infolist():
                    target.writestr(entry, package.encode("utf-8") if entry.filename == "content.opf" else source.read(entry))

            self.assertEqual(read_calibre_user_metadata(rewritten), {"pages": 5})
            self.assertTrue(has_calibre_user_metadata(rewritten))


class CalibreUserMetadataTest(unittest.TestCase):
    """Guard the exact contract Calibre's importer requires."""

    COLUMNS = (
        ("ao3_kudos", "AO3 Kudos", "int", "kudos"),
        ("ao3_category", "AO3 Category", "text", "category"),
        ("gfog", "Gfog", "float", "local_gfog"),
        ("ao3_status", "AO3 Status", "text", "status"),
    )

    def metadata(self):
        return AO3Metadata(
            work_id="64805",
            work_url="https://archiveofourown.org/works/64805",
            kudos=68,
            category="F/F, M/M",
            local_gfog=8.45,
        )

    def test_only_populated_columns_are_written(self):
        values = _calibre_user_metadata(self.metadata(), self.COLUMNS)

        self.assertEqual(
            sorted(values),
            [
                "calibre:user_metadata:#ao3_category",
                "calibre:user_metadata:#ao3_kudos",
                "calibre:user_metadata:#gfog",
            ],
        )

    def test_each_payload_carries_the_label_datatype_and_typed_value(self):
        values = _calibre_user_metadata(self.metadata(), self.COLUMNS)
        payload = json.loads(values["calibre:user_metadata:#ao3_kudos"])

        self.assertEqual(payload["label"], "ao3_kudos")
        self.assertEqual(payload["datatype"], "int")
        self.assertEqual(payload["name"], "AO3 Kudos")
        self.assertEqual(payload["search_terms"], ["#ao3_kudos"])
        self.assertTrue(payload["is_custom"])
        self.assertEqual(payload["#value#"], 68)

    def test_float_and_text_values_keep_their_json_types(self):
        values = _calibre_user_metadata(self.metadata(), self.COLUMNS)

        self.assertEqual(json.loads(values["calibre:user_metadata:#gfog"])["#value#"], 8.45)
        self.assertEqual(
            json.loads(values["calibre:user_metadata:#ao3_category"])["#value#"], "F/F, M/M"
        )

    def test_calibre_metas_are_written_without_a_namespace_prefix(self):
        """Calibre matches ``name() = "meta"``, so ``<opf:meta>`` is invisible to it."""

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            enrich_epub(epub_path, self.metadata(), calibre_columns=self.COLUMNS)

            with zipfile.ZipFile(epub_path) as archive:
                content = archive.read("content.opf")
            columns = read_calibre_user_metadata(epub_path)

        written = re.findall(r"<([^\s/>]+)[^>]*name=\"calibre:user_metadata:", content.decode("utf-8"))
        self.assertEqual(written, ["meta"] * 3, "Calibre ignores a namespaced meta element")
        self.assertEqual(sorted(columns), ["ao3_category", "ao3_kudos", "gfog"])

    def test_enrichment_without_calibre_columns_writes_none_of_them(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            enrich_epub(epub_path, self.metadata())

            with zipfile.ZipFile(epub_path) as archive:
                content = archive.read("content.opf").decode("utf-8")

        self.assertNotIn("calibre:user_metadata", content)
        self.assertIn("ao3:kudos", content)

    def test_a_column_calibre_already_embedded_is_rewritten_unprefixed(self):
        """calibredb embed_metadata writes these in the OPF namespace; reusing them made <opf:meta>."""

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            with zipfile.ZipFile(epub_path) as archive:
                package = archive.read("content.opf").decode("utf-8")
            calibre_written = (
                '<meta name="calibre:user_metadata:#ao3_kudos" '
                'content="{&quot;#value#&quot;: 1, &quot;datatype&quot;: &quot;int&quot;}"/>'
            )
            package = package.replace("</metadata>", calibre_written + "</metadata>")
            rewritten = f"{directory}/rewritten.epub"
            with zipfile.ZipFile(epub_path) as source, zipfile.ZipFile(rewritten, "w") as target:
                for item in source.infolist():
                    data = package.encode("utf-8") if item.filename == "content.opf" else source.read(item)
                    target.writestr(item, data)

            enrich_epub(rewritten, self.metadata(), calibre_columns=self.COLUMNS)

            with zipfile.ZipFile(rewritten) as archive:
                content = archive.read("content.opf").decode("utf-8")
            columns = read_calibre_user_metadata(rewritten)

        written = re.findall(r"<([^\s/>]+)[^>]*name=\"calibre:user_metadata:#ao3_kudos\"", content)
        self.assertEqual(written, ["meta"], "Calibre ignores <opf:meta>")
        self.assertEqual(columns["ao3_kudos"], 68)

    def test_columns_this_project_does_not_set_also_stay_readable_by_calibre(self):
        """#pages embedded by Calibre used to come out as <opf:meta> and vanish on import."""

        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            with zipfile.ZipFile(epub_path) as archive:
                package = archive.read("content.opf").decode("utf-8")
            package = package.replace(
                "</metadata>",
                '<meta name="calibre:user_metadata:#pages" '
                'content="{&quot;#value#&quot;: 105, &quot;datatype&quot;: &quot;int&quot;}"/></metadata>',
            )
            rewritten = f"{directory}/rewritten.epub"
            with zipfile.ZipFile(epub_path) as source, zipfile.ZipFile(rewritten, "w") as target:
                for item in source.infolist():
                    data = package.encode("utf-8") if item.filename == "content.opf" else source.read(item)
                    target.writestr(item, data)

            for enrich in (
                lambda path: enrich_epub(path, self.metadata(), calibre_columns=self.COLUMNS),
                lambda path: enrich_epub_portable(path, self.metadata()),
            ):
                enrich(rewritten)
                with zipfile.ZipFile(rewritten) as archive:
                    content = archive.read("content.opf").decode("utf-8")
                written = re.findall(r"<([^\s/>]+)[^>]*name=\"calibre:user_metadata:#pages\"", content)
                self.assertEqual(written, ["meta"])
                self.assertEqual(read_calibre_user_metadata(rewritten)["pages"], 105)

    def test_re_enrichment_replaces_rather_than_duplicates_a_column(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = f"{directory}/work.epub"
            create_epub(epub_path)
            enrich_epub(epub_path, self.metadata(), calibre_columns=self.COLUMNS)
            updated = AO3Metadata(**{**self.metadata().__dict__, "kudos": 99})
            enrich_epub(epub_path, updated, calibre_columns=self.COLUMNS)

            with zipfile.ZipFile(epub_path) as archive:
                content = archive.read("content.opf").decode("utf-8")
                package = ET.fromstring(archive.read("content.opf"))

        self.assertEqual(content.count('name="calibre:user_metadata:#ao3_kudos"'), 1)
        metadata_element = next(
            child for child in package if child.tag.rsplit("}", 1)[-1] == "metadata"
        )
        kudos = next(
            child
            for child in metadata_element
            if child.attrib.get("name") == "calibre:user_metadata:#ao3_kudos"
        )
        self.assertEqual(json.loads(kudos.attrib["content"])["#value#"], 99)


class OpfNamespaceTest(unittest.TestCase):
    """Element names must come out unprefixed under a default namespace.

    ``<opf:package>`` is valid, and readers that look elements up by literal
    name miss it: BookOrbit's reader shows an empty book, and Calibre's
    importer skips a prefixed custom-column meta.
    """

    def metadata(self):
        return AO3Metadata("64805", "https://archiveofourown.org/works/64805", kudos=68)

    def test_enrichment_writes_no_prefixed_element_names(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, front_page("Mature"))
            enrich_epub(epub_path, self.metadata(), calibre_columns=(("ao3_kudos", "AO3 Kudos", "int", "kudos"),))

            opf_path, package = read_epub_package(epub_path)

        self.assertEqual(opf_prefixed_element_names(package), ())
        self.assertIn(b'xmlns="http://www.idpf.org/2007/opf"', package)
        self.assertIn(b"<metadata", package)
        self.assertNotIn(b"<opf:", package)

    def test_attributes_keep_their_prefix_because_a_default_namespace_never_applies_to_them(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, front_page("Mature"))
            enrich_epub(epub_path, self.metadata())

            package = read_epub_package(epub_path)[1].decode("utf-8")

        self.assertIn('opf:role="aut"', package)
        self.assertIn('opf:file-as="Author"', package)
        self.assertIn('opf:scheme="ao3"', package)

    def test_a_prefixed_opf_is_repaired_and_keeps_everything_else(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, front_page("Explicit"), subjects=["Fluff"], prefixed=True)
            before = read_epub_package(epub_path)[1]
            self.assertTrue(opf_prefixed_element_names(before), "the fixture must start prefixed")

            repair_epub(epub_path)

            package = read_epub_package(epub_path)[1]
            validate_epub_file(epub_path)

        self.assertEqual(opf_prefixed_element_names(package), ())
        parsed = ET.fromstring(package)
        self.assertEqual(parsed.tag, "{http://www.idpf.org/2007/opf}package")
        self.assertEqual(
            [item.attrib["href"] for item in parsed.iter("{http://www.idpf.org/2007/opf}item")],
            ["cover.xhtml", "preface.xhtml", "chapter.xhtml"],
        )
        self.assertEqual(
            [ref.attrib["idref"] for ref in parsed.iter("{http://www.idpf.org/2007/opf}itemref")],
            ["cover", "preface", "chapter"],
        )
        self.assertIn(b"<dc:subject>Fluff</dc:subject>", package)

    def test_an_opf_that_was_already_plain_stays_plain(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, front_page("Mature"))
            before = read_epub_package(epub_path)[1]

            repair_epub(epub_path)

            after = read_epub_package(epub_path)[1]

        self.assertEqual(opf_prefixed_element_names(before), ())
        self.assertEqual(opf_prefixed_element_names(after), ())
        self.assertEqual(ET.fromstring(before).tag, ET.fromstring(after).tag)

    def test_only_opf_namespace_prefixes_are_reported_not_dublin_core_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, front_page("Mature"), subjects=["Fluff"], prefixed=True)
            package = read_epub_package(epub_path)[1]

        self.assertIn(b"<dc:title>", package, "dc: elements are prefixed in every EPUB")
        self.assertEqual(
            sorted(set(opf_prefixed_element_names(package))),
            ["opf:item", "opf:itemref", "opf:manifest", "opf:metadata", "opf:package", "opf:spine"],
        )


class RatingTest(unittest.TestCase):
    def test_the_rating_is_read_from_the_work_page(self):
        page = AO3_PAGE + '<dd class="rating tags"><ul><li><a>Teen And Up Audiences</a></li></ul></dd>'

        metadata = parse_ao3_metadata(page, "https://archiveofourown.org/works/64805")

        self.assertEqual(metadata.rating, "Teen And Up Audiences")
        self.assertEqual(metadata.category, "F/F, M/M", "the category must still parse beside it")

    def test_a_page_without_a_rating_reports_none(self):
        metadata = parse_ao3_metadata(AO3_PAGE, "https://archiveofourown.org/works/64805")

        self.assertIsNone(metadata.rating)

    def test_ao3s_own_spelling_wins_over_the_spelling_in_the_file(self):
        self.assertEqual(canonical_rating("general audiences"), "General Audiences")
        self.assertEqual(canonical_rating(" Not Rated "), "Not Rated")
        self.assertIsNone(canonical_rating("Explicit Consent"), "a work tag is not a rating")
        self.assertIsNone(canonical_rating("Rating: Mature"), "only the bare value is a rating")
        self.assertIsNone(canonical_rating(None))

    def test_the_rating_is_read_from_an_ao3_export_front_page(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, "<html/>", rating_page=front_page("Explicit"))

            self.assertEqual(read_front_page_rating(epub_path), "Explicit")

    def test_a_file_with_no_rating_anywhere_reads_as_none(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, "<html/>", rating_page=front_page(None))

            self.assertIsNone(read_front_page_rating(epub_path))

    def test_a_rating_named_in_a_chapter_is_never_taken_for_the_works_rating(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(
                epub_path,
                "<html/>",
                chapter="<html><body><p>Rating: Explicit</p></body></html>",
                rating_page=front_page(None),
            )

            self.assertIsNone(read_front_page_rating(epub_path))

    def test_enrichment_adds_the_rating_beside_the_other_tags(self):
        metadata = AO3Metadata(
            "64805", "https://archiveofourown.org/works/64805", rating="Mature", kudos=68
        )

        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, "<html/>", subjects=["Fluff", "Angst"])
            enrich_epub(epub_path, metadata)

            package = read_epub_package(epub_path)[1].decode("utf-8")
            self.assertEqual(epub_rating_subjects(epub_path), ("Mature",))

        self.assertLess(
            package.index("<dc:subject>Mature</dc:subject>"),
            package.index("</metadata>"),
            "the rating is a tag like any other",
        )
        self.assertIn("<dc:subject>Fluff</dc:subject>", package)

    def test_re_enrichment_never_adds_a_second_rating(self):
        metadata = AO3Metadata("64805", "https://archiveofourown.org/works/64805", rating="Mature")

        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, "<html/>")
            enrich_epub(epub_path, metadata)
            enrich_epub(epub_path, metadata)

            self.assertEqual(epub_rating_subjects(epub_path), ("Mature",))

    def test_a_rating_already_in_the_tag_list_is_left_exactly_as_it_is(self):
        metadata = AO3Metadata("64805", "https://archiveofourown.org/works/64805", rating="Mature")

        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, "<html/>", subjects=["Explicit"])
            enrich_epub(epub_path, metadata)

            self.assertEqual(epub_rating_subjects(epub_path), ("Explicit",))

    def test_repair_adds_the_rating_and_the_namespace_fix_together(self):
        with tempfile.TemporaryDirectory() as directory:
            epub_path = Path(directory) / "work.epub"
            create_book(epub_path, "<html/>", subjects=["Fluff"], prefixed=True)

            repair_epub(epub_path, rating="Not Rated")

            self.assertEqual(epub_rating_subjects(epub_path), ("Not Rated",))
            self.assertEqual(opf_prefixed_element_names(read_epub_package(epub_path)[1]), ())


if __name__ == "__main__":
    unittest.main()
