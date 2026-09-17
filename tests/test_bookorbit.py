"""Tests for publishing AO3 numbers to BookOrbit, including not using BookOrbit at all."""

import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ao3archiver import backfill, bookorbit
from ao3archiver.metadata import AO3Metadata, enrich_epub
from ao3archiver.run_log import register_secret
from tests.support import create_epub, front_page


FIELDS = [
    {"id": 3, "label": "AO3 Kudos", "type": "number", "archivedAt": None},
    {"id": 4, "label": "AO3 Hits", "type": "number", "archivedAt": None},
    {"id": 5, "label": "Local Words", "type": "number", "archivedAt": None},
    {"id": 6, "label": "AO3 Rating", "type": "text", "archivedAt": None},
    {"id": 7, "label": "AO3 Link", "type": "url", "archivedAt": None},
    {"id": 8, "label": "Retired", "type": "text", "archivedAt": "2026-01-01T00:00:00.000Z"},
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"{}" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Stands in for BookOrbit, recording every call and answering by route."""

    def __init__(self, *, books=None, fields=None, allow_privileged_fields=False):
        self.books = books if books is not None else []
        self.fields = fields if fields is not None else FIELDS
        self.allow_privileged_fields = allow_privileged_fields
        self.requests: list[tuple[str, str, object]] = []
        self.sign_ins = 0
        self.patches: dict[int, list[dict]] = {}

    def post(self, url, json=None, timeout=None, headers=None):
        self.requests.append(("POST", url, json))
        if url.endswith("/auth/login"):
            self.sign_ins += 1
            return FakeResponse(payload={"accessToken": f"token-{self.sign_ins}", "user": {"id": 1}})
        if url.endswith("/books/metadata-export/download"):
            return FakeResponse(payload={"schemaVersion": 1, "items": self.books})
        raise AssertionError(f"unexpected POST {url}")

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.requests.append((method, url, json))
        if url.endswith("/custom-metadata/fields"):
            if not self.allow_privileged_fields:
                return FakeResponse(status_code=403, text="Forbidden")
            return FakeResponse(payload=[{**field, "key": bookorbit.slugify_label(field["label"])}
                                         for field in self.fields])
        if url.endswith("/custom-metadata/fields/active"):
            return FakeResponse(payload=self.fields)
        if url.endswith("/books/metadata-export/download"):
            return FakeResponse(payload={"schemaVersion": 1, "items": self.books})
        if "/books/" in url and url.endswith("/metadata"):
            book_id = int(url.rsplit("/books/", 1)[1].split("/")[0])
            self.patches.setdefault(book_id, []).extend(json["customMetadata"])
            return FakeResponse(payload={"id": book_id})
        raise AssertionError(f"unexpected {method} {url}")


def config(**overrides):
    values = {
        "base_url": "http://books.example/api/v1",
        "token": None,
        "username": "archiver",
        "password": "secret",
        "library_id": None,
        "source": "test",
    }
    values.update(overrides)
    return bookorbit.BookOrbitConfig(**values)


def client_for(session, **overrides):
    return bookorbit.BookOrbitClient(config(**overrides), session=session)


def write_epub(path: Path, *, kudos=68, hits=1040, rating="Mature"):
    create_epub(path, "<html/>", rating_page=front_page(rating))
    enrich_epub(
        path,
        AO3Metadata(
            work_id="111",
            work_url="https://archiveofourown.org/works/111",
            kudos=kudos,
            hits=hits,
            rating=rating,
            local_words=4399,
            published="2018-06-01",
            status="Completed: 12 Mar 2019",
        ),
    )
    return path


class NotConfiguredTest(unittest.TestCase):
    """Most people using this project do not run BookOrbit."""

    def test_no_settings_at_all_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / ".env"

            self.assertIsNone(bookorbit.load_bookorbit_config({}, dotenv_path=missing))

    def test_a_sync_without_bookorbit_does_nothing_and_reports_it(self):
        # Never read the developer's own .env: the answer must not depend on
        # whether the machine running the tests happens to use BookOrbit.
        original = bookorbit.load_bookorbit_config
        bookorbit.load_bookorbit_config = lambda *args, **kwargs: None
        try:
            with tempfile.TemporaryDirectory() as directory:
                epub = write_epub(Path(directory) / "work.epub")

                summary = bookorbit.sync_epubs([epub], config=None, client=None)
        finally:
            bookorbit.load_bookorbit_config = original

        self.assertFalse(summary.configured)
        self.assertEqual(summary.updated, ())

    def test_the_command_exits_cleanly_when_bookorbit_is_not_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "library"
            library.mkdir()
            write_epub(library / "work.epub")
            original = bookorbit.load_bookorbit_config
            bookorbit.load_bookorbit_config = lambda *args, **kwargs: None
            try:
                exit_code = backfill.main(["bookorbit-sync", "--library", str(library), "--yes"])
            finally:
                bookorbit.load_bookorbit_config = original

        self.assertEqual(exit_code, 0)

    def test_the_setup_steps_are_offered_instead_of_a_bare_refusal(self):
        original = bookorbit.load_bookorbit_config
        bookorbit.load_bookorbit_config = lambda *args, **kwargs: None
        logger = logging.getLogger("ao3.bookorbit")
        try:
            with tempfile.TemporaryDirectory() as directory:
                epub = write_epub(Path(directory) / "work.epub")
                with self.assertLogs(logger, level="INFO") as logs:
                    bookorbit.sync_epubs([epub], config=None, client=None)
        finally:
            bookorbit.load_bookorbit_config = original

        self.assertIn("BOOKORBIT_URL", "\n".join(logs.output))
        self.assertIn("Custom Metadata", "\n".join(logs.output))

    def test_writing_off_a_terminal_needs_yes(self):
        """Every other writing command needs an explicit flag; so does this one."""

        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "library"
            library.mkdir()
            write_epub(library / "work.epub")

            with self.assertRaises(backfill.BackfillError):
                backfill.main(["bookorbit-sync", "--library", str(library)])

    def test_a_dry_run_needs_no_approval_at_all(self):
        original = bookorbit.load_bookorbit_config
        bookorbit.load_bookorbit_config = lambda *args, **kwargs: None
        try:
            with tempfile.TemporaryDirectory() as directory:
                library = Path(directory) / "library"
                library.mkdir()
                write_epub(library / "work.epub")

                exit_code = backfill.main(["bookorbit-sync", "--library", str(library), "--dry-run"])
        finally:
            bookorbit.load_bookorbit_config = original

        self.assertEqual(exit_code, 0)

    def test_a_url_without_credentials_is_reported_rather_than_guessed(self):
        with self.assertRaises(bookorbit.BookOrbitError):
            bookorbit.load_bookorbit_config({"BOOKORBIT_URL": "http://books.example"}, dotenv_path=None)


class SecrecyTest(unittest.TestCase):
    """A credential must never reach a log line, an error, or a URL."""

    def test_no_error_path_repeats_the_password_or_token(self):
        secret = "hunter22-bookorbit"

        class Failing(FakeSession):
            def post(self, url, json=None, timeout=None, headers=None):
                if url.endswith("/auth/login"):
                    return FakeResponse(status_code=401, text=f"bad password {secret}")
                return super().post(url, json=json, timeout=timeout, headers=headers)

            def request(self, method, url, json=None, headers=None, timeout=None):
                return FakeResponse(status_code=500, text=f"boom {secret}")

        session = Failing()
        client = client_for(session, password=secret)
        with self.assertRaises(bookorbit.BookOrbitError) as caught:
            client.custom_fields()
        self.assertNotIn(secret, str(caught.exception))

        register_secret(secret)
        token_client = client_for(Failing(), token=secret, username=None, password=None)
        with self.assertRaises(bookorbit.BookOrbitError) as caught:
            token_client.custom_fields()
        self.assertNotIn(secret, str(caught.exception))

    def test_a_malformed_field_row_is_skipped_rather_than_crashing(self):
        """A server response this client cannot use must not end the run with a traceback."""

        session = FakeSession(
            books=[],
            fields=[
                {"label": "AO3 Kudos", "type": "number", "archivedAt": None},  # no id
                {"id": "not-a-number", "label": "AO3 Hits", "type": "number", "archivedAt": None},
                {"id": 9, "label": None, "type": None, "archivedAt": None},
                {"id": 3, "label": "AO3 Words", "type": "number", "archivedAt": None},
            ],
        )

        fields = client_for(session).custom_fields()

        self.assertEqual([(field.field_id, field.label) for field in fields], [(3, "AO3 Words")])

    def test_the_credential_never_travels_in_the_url(self):
        session = FakeSession(books=[])
        client = client_for(session, password="hunter22")
        client.custom_fields()

        for _, url, _ in session.requests:
            self.assertNotIn("hunter22", url)
            self.assertNotIn("token", url)


class ConfigTest(unittest.TestCase):
    def test_a_bare_host_becomes_an_api_root(self):
        self.assertEqual(bookorbit.normalize_base_url("192.0.2.10:8080"), "http://192.0.2.10:8080/api/v1")
        self.assertEqual(bookorbit.normalize_base_url("https://books.example/"), "https://books.example/api/v1")
        self.assertEqual(
            bookorbit.normalize_base_url("https://books.example/api/v1"), "https://books.example/api/v1"
        )

    def test_a_token_alone_is_enough(self):
        loaded = bookorbit.load_bookorbit_config(
            {"BOOKORBIT_URL": "http://books.example", "BOOKORBIT_TOKEN": "abc"}, dotenv_path=None
        )

        self.assertEqual(loaded.token, "abc")
        self.assertIsNone(loaded.username)

    def test_the_field_key_matches_the_one_bookorbit_derives(self):
        self.assertEqual(bookorbit.slugify_label("AO3 Kudos"), "ao3_kudos")
        self.assertEqual(bookorbit.slugify_label("Gunning Fog"), "gunning_fog")


class ValuesTest(unittest.TestCase):
    def test_values_come_from_the_file_including_the_rating_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            epub = write_epub(Path(directory) / "work.epub")

            values = bookorbit.epub_field_values(epub)

        self.assertEqual(values["AO3 Kudos"], 68)
        self.assertEqual(values["AO3 Hits"], 1040)
        self.assertEqual(values["AO3 Rating"], "Mature")
        self.assertEqual(values["Local Words"], 4399)
        self.assertEqual(values["AO3 Link"], "https://archiveofourown.org/works/111")

    def test_a_date_bookorbit_would_reject_is_left_out(self):
        with tempfile.TemporaryDirectory() as directory:
            epub = write_epub(Path(directory) / "work.epub")

            values = bookorbit.epub_field_values(epub)

        self.assertEqual(values["AO3 Published"], "2018-06-01")
        self.assertNotIn("AO3 Updated", values, "AO3 words some of these as prose")


class MatchingTest(unittest.TestCase):
    def test_a_book_is_found_by_its_folder_and_filename(self):
        index = bookorbit.index_by_path([
            {"bookId": 1, "filePaths": ["/srv/books/Author/Title (7)/Title - Author.epub"]},
        ])

        found = bookorbit.find_book(index, Path("/home/me/Calibre Library/Author/Title (7)/Title - Author.epub"))

        self.assertEqual(found["bookId"], 1)

    def test_a_flat_download_folder_matches_on_the_filename(self):
        index = bookorbit.index_by_path([{"bookId": 2, "filePaths": ["/srv/fanfiction/111.epub"]}])

        self.assertEqual(bookorbit.find_book(index, Path("downloaded/111.epub"))["bookId"], 2)

    def test_an_ambiguous_filename_is_never_guessed(self):
        index = bookorbit.index_by_path([
            {"bookId": 1, "filePaths": ["/srv/a/Chapter One.epub"]},
            {"bookId": 2, "filePaths": ["/srv/b/Chapter One.epub"]},
        ])

        self.assertIsNone(bookorbit.find_book(index, Path("/elsewhere/Chapter One.epub")))
        self.assertEqual(bookorbit.find_book(index, Path("/elsewhere/a/Chapter One.epub"))["bookId"], 1)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        folder = self.root / "Author" / "Title (7)"
        folder.mkdir(parents=True)
        self.epub = write_epub(folder / "Title - Author.epub")

    def tearDown(self):
        self._directory.cleanup()

    def book(self, **values):
        return {
            "bookId": 42,
            "filePaths": [f"/srv/fanfiction/Title (7)/{self.epub.name}"],
            **values,
        }

    def test_every_known_field_is_written_once(self):
        session = FakeSession(books=[self.book()])

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertEqual(len(summary.updated), 1)
        written = {entry["fieldId"]: entry["value"] for entry in session.patches[42]}
        self.assertEqual(written[3], 68, "AO3 Kudos")
        self.assertEqual(written[4], 1040, "AO3 Hits")
        self.assertEqual(written[6], "Mature", "AO3 Rating")
        self.assertNotIn(8, written, "an archived field is never written")

    def test_a_second_run_writes_nothing(self):
        session = FakeSession(books=[self.book(**{
            "custom.ao3_kudos": 68,
            "custom.ao3_hits": 1040,
            "custom.local_words": 4399,
            "custom.ao3_rating": "Mature",
            "custom.ao3_link": "https://archiveofourown.org/works/111",
        })])

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertEqual(summary.updated, ())
        self.assertEqual(len(summary.unchanged), 1)
        self.assertEqual(session.patches, {})

    def test_only_the_fields_that_differ_are_sent(self):
        session = FakeSession(books=[self.book(**{"custom.ao3_kudos": 68, "custom.ao3_hits": 1})])

        bookorbit.sync_epubs([self.epub], client=client_for(session))

        sent = {entry["fieldId"] for entry in session.patches[42]}
        self.assertIn(4, sent, "the stale hit count")
        self.assertNotIn(3, sent, "kudos already match")

    def test_a_dry_run_sends_no_write(self):
        session = FakeSession(books=[self.book()])

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session), dry_run=True)

        self.assertEqual(len(summary.updated), 1)
        self.assertEqual(session.patches, {})

    def test_a_book_bookorbit_does_not_have_is_reported_not_failed(self):
        session = FakeSession(books=[{"bookId": 9, "filePaths": ["/srv/other/Something else.epub"]}])

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertEqual(summary.unmatched, (str(self.epub),))
        self.assertEqual(summary.updated, ())

    def test_a_field_named_after_the_calibre_column_is_filled_too(self):
        """`Words` and `Gfog` are at least as natural as the README's own labels."""

        session = FakeSession(books=[self.book()], fields=[
            {"id": 9, "label": "Words", "type": "number", "archivedAt": None},
            {"id": 10, "label": "Gfog", "type": "number", "archivedAt": None},
        ])

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        written = {entry["fieldId"]: entry["value"] for entry in session.patches[42]}
        self.assertEqual(written[9], 4399, "the local word count")
        self.assertEqual(len(summary.updated), 1)

    def test_both_a_canonical_field_and_its_alias_are_filled(self):
        session = FakeSession(books=[self.book()], fields=[
            {"id": 5, "label": "Local Words", "type": "number", "archivedAt": None},
            {"id": 9, "label": "Words", "type": "number", "archivedAt": None},
        ])

        bookorbit.sync_epubs([self.epub], client=client_for(session))

        written = {entry["fieldId"]: entry["value"] for entry in session.patches[42]}
        self.assertEqual(written, {5: 4399, 9: 4399})

    def test_a_field_this_project_knows_nothing_about_is_left_alone(self):
        session = FakeSession(books=[self.book()], fields=[
            {"id": 1, "label": "AO3 Kudos", "type": "number", "archivedAt": None},
            {"id": 99, "label": "My Own Notes", "type": "text", "archivedAt": None},
        ])

        bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertEqual([entry["fieldId"] for entry in session.patches[42]], [1])

    def test_the_ao3_status_field_is_published(self):
        session = FakeSession(books=[self.book()], fields=[
            {"id": 7, "label": "AO3 Status", "type": "text", "archivedAt": None},
        ])

        bookorbit.sync_epubs([self.epub], client=client_for(session))

        written = {entry["fieldId"]: entry["value"] for entry in session.patches[42]}
        self.assertEqual(written[7], "Completed: 12 Mar 2019")

    def test_stopping_part_way_keeps_the_summary_and_what_was_written(self):
        """Ctrl-C between books must report, not traceback: each book is one request."""

        session = FakeSession(books=[self.book()])

        class Stopping(bookorbit.BookOrbitClient):
            def set_custom_values(self, book_id, values):
                super().set_custom_values(book_id, values)
                raise KeyboardInterrupt

        client = Stopping(config(), session=session)
        summary = bookorbit.sync_epubs([self.epub, self.epub], client=client)

        self.assertTrue(summary.interrupted)
        self.assertEqual(len(summary.updated), 0, "the book it was writing is not counted as done")
        self.assertEqual(
            len(session.patches[42]), 5, "but what it did send was every field it had, in one request"
        )

    def test_fields_that_do_not_exist_yet_are_reported(self):
        session = FakeSession(books=[self.book()], fields=[FIELDS[0]])

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertIn("AO3 Hits", summary.missing_fields)
        self.assertEqual(len(summary.updated), 1, "the fields that do exist are still written")

    def test_declining_the_missing_fields_question_publishes_nothing(self):
        session = FakeSession(books=[self.book()], fields=[FIELDS[0]])

        with patch.object(bookorbit.prompts, "confirm", lambda *args, **options: False):
            summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertIn("AO3 Hits", summary.missing_fields)
        self.assertEqual(summary.updated, ())
        self.assertEqual(session.patches, {})

    def test_a_field_of_the_wrong_type_is_written_unless_someone_says_otherwise(self):
        text_kudos = [{"id": 3, "label": "AO3 Kudos", "type": "text", "archivedAt": None}]
        session = FakeSession(books=[self.book()], fields=text_kudos)

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertEqual(len(summary.updated), 1, "off a terminal the sync behaves as it always did")

    def test_declining_a_wrongly_typed_field_leaves_it_alone(self):
        fields = [
            {"id": 3, "label": "AO3 Kudos", "type": "text", "archivedAt": None},
            {"id": 4, "label": "AO3 Hits", "type": "number", "archivedAt": None},
        ]
        session = FakeSession(books=[self.book()], fields=fields)
        answers = iter([True, False])

        def confirm(question, **options):
            # The first question is about the fields that do not exist at all.
            return next(answers)

        with patch.object(bookorbit.prompts, "confirm", confirm):
            summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertEqual(len(summary.updated), 1)
        written = {entry["fieldId"] for entry in session.patches[42]}
        self.assertEqual(written, {4}, "the text field that cannot sort is skipped")

    def test_no_fields_at_all_stops_before_exporting_anything(self):
        session = FakeSession(books=[self.book()], fields=[])

        summary = bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertTrue(summary.configured)
        self.assertEqual(summary.updated, ())
        self.assertFalse(any("metadata-export" in url for _, url, _ in session.requests))

    def test_a_privileged_account_uses_bookorbits_own_field_keys(self):
        session = FakeSession(books=[self.book(**{"custom.ao3_kudos": 68})], allow_privileged_fields=True)

        bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertNotIn(3, {entry["fieldId"] for entry in session.patches[42]})

    def test_the_run_signs_in_once_and_sends_the_token(self):
        session = FakeSession(books=[self.book()])

        bookorbit.sync_epubs([self.epub], client=client_for(session))

        self.assertEqual(session.sign_ins, 1)

    def test_a_pasted_token_is_used_without_signing_in(self):
        session = FakeSession(books=[self.book()])

        bookorbit.sync_epubs([self.epub], client=client_for(session, token="pasted", username=None, password=None))

        self.assertEqual(session.sign_ins, 0)

    def test_an_expired_token_is_replaced_mid_run(self):
        session = FakeSession(books=[self.book()])
        clock = [0.0]
        client = bookorbit.BookOrbitClient(config(), session=session, monotonic_fn=lambda: clock[0])
        client._authorization()
        clock[0] = bookorbit.TOKEN_LIFETIME_SECONDS + 1

        header = client._authorization()

        self.assertEqual(session.sign_ins, 2)
        self.assertEqual(header, "Bearer token-2")


if __name__ == "__main__":
    unittest.main()
