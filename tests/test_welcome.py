"""Tests for the front door: the setup check, the menu, and the commands it prints."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ao3archiver import welcome


def marks(checks):
    return {check.label: check.ok for check in checks}


def details(checks):
    return {check.label: check.detail for check in checks}


class LibraryPathTest(unittest.TestCase):
    def test_the_environment_wins_over_the_dotenv_file(self):
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text("CALIBRE_LIBRARY=/from/the/file\n", encoding="utf-8")

            found = welcome.library_path({"CALIBRE_LIBRARY": "/from/the/environment"}, dotenv_path=dotenv)

        self.assertEqual(found, Path("/from/the/environment"))

    def test_an_unset_library_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(welcome.library_path({}, dotenv_path=Path(directory) / ".env"))

    def test_a_home_relative_path_is_expanded(self):
        with tempfile.TemporaryDirectory() as directory:
            found = welcome.library_path({"CALIBRE_LIBRARY": "~/Calibre Library"},
                                         dotenv_path=Path(directory) / ".env")

        self.assertEqual(found, Path.home() / "Calibre Library")


class SetupCheckTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def checks(self, **options):
        defaults = {
            "dotenv_path": self.root / ".env",
            "ini_path": self.root / "personal.ini",
            "environ": {},
            "links_dir": self.root / "links",
            "output_dir": self.root / "downloaded",
        }
        return welcome.setup_checks(**{**defaults, "library": None, **options})

    def test_a_bare_clone_says_what_is_missing_without_failing(self):
        checks = self.checks()

        self.assertEqual(marks(checks)[".env"], False)
        self.assertEqual(marks(checks)["AO3 credentials"], False)
        self.assertEqual(marks(checks)["links/"], False)
        self.assertIn(".env.example", details(checks)[".env"])

    def test_credentials_are_reported_by_source_and_never_by_value(self):
        (self.root / ".env").write_text("AO3_USERNAME=reader\nAO3_PASSWORD=hunter2\n", encoding="utf-8")

        checks = self.checks()

        self.assertEqual(marks(checks)["AO3 credentials"], True)
        self.assertNotIn("hunter2", "".join(details(checks).values()))

    def test_an_unused_library_and_bookorbit_are_neither_right_nor_wrong(self):
        checks = self.checks()

        self.assertIsNone(marks(checks)["Calibre library"], "only the backfill needs one")
        self.assertIsNone(marks(checks)["BookOrbit"], "most libraries never use it")

    def test_a_library_is_only_a_library_with_a_metadata_db(self):
        library = self.root / "Calibre Library"
        library.mkdir()

        self.assertEqual(marks(self.checks(library=library))["Calibre library"], False)

        (library / "metadata.db").write_bytes(b"")

        self.assertEqual(marks(self.checks(library=library))["Calibre library"], True)

    def test_links_and_downloads_are_counted(self):
        links = self.root / "links"
        links.mkdir()
        (links / "a.txt").write_text(
            "https://archiveofourown.org/works/111\nhttps://archiveofourown.org/works/222\n",
            encoding="utf-8",
        )
        downloaded = self.root / "downloaded"
        downloaded.mkdir()
        (downloaded / "111.epub").write_bytes(b"")

        checks = self.checks()

        self.assertEqual(marks(checks)["links/"], True)
        self.assertIn("2 works", details(checks)["links/"])
        self.assertIn("1 EPUB", details(checks)["downloaded/"])


class FlowTest(unittest.TestCase):
    def test_a_known_library_lands_in_the_printed_commands(self):
        commands = "\n".join(
            command for flow in welcome.flows(Path("/srv/Calibre Library")) for command in flow.commands
        )

        self.assertIn('"/srv/Calibre Library"', commands)
        self.assertNotIn(welcome.LIBRARY_PLACEHOLDER, commands)

    def test_without_one_the_command_still_shows_its_shape(self):
        commands = "\n".join(command for flow in welcome.flows() for command in flow.commands)

        self.assertIn(welcome.LIBRARY_PLACEHOLDER, commands)

    def test_only_read_only_steps_are_offered_to_run(self):
        """Anything that writes is printed for the person to run themselves."""

        for flow in welcome.flows():
            if flow.preview is not None:
                self.assertIn("--dry-run", flow.preview, flow.title)


class MenuTest(unittest.TestCase):
    """The menu is tested on its own; the machine's real .env never takes part."""

    CHECKS = (welcome.Check(".env", True, "/somewhere/.env"),)

    def setUp(self):
        self.lines: list[str] = []
        self.ran: list[list[str]] = []
        patcher = patch.object(welcome, "setup_checks", lambda **options: self.CHECKS)
        patcher.start()
        self.addCleanup(patcher.stop)
        library = patch.object(welcome, "library_path", lambda *args, **options: None)
        library.start()
        self.addCleanup(library.stop)

    def emit(self, line):
        self.lines.append(line)

    def runner(self, command):
        self.ran.append(list(command))
        return 0

    def main(self, *, choices=(), confirmations=()):
        chosen = iter(choices)
        answers = iter(confirmations)
        with patch.object(welcome.prompts, "choose", lambda *args, **options: next(chosen, None)), patch.object(
            welcome.prompts, "confirm", lambda *args, **options: next(answers, False)
        ), patch.object(welcome.prompts, "at_a_terminal", lambda: True):
            return welcome.main(emit=self.emit, runner=self.runner)

    def text(self):
        return "\n".join(self.lines)

    def test_quitting_straight_away_runs_nothing(self):
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.ran, [])
        self.assertIn("Setup", self.text())

    def test_a_chosen_flow_prints_the_exact_command_before_offering_to_run_it(self):
        self.main(choices=(1,), confirmations=(False,))

        self.assertIn("$ python3 download.py --dry-run", self.text())
        self.assertIn("$ python3 download.py", self.text())
        self.assertEqual(self.ran, [], "a declined offer runs nothing")

    def test_accepting_runs_exactly_the_command_that_was_printed(self):
        self.main(choices=(1,), confirmations=(True,))

        self.assertEqual(self.ran, [["python3", "download.py", "--dry-run"]])

    def test_a_flow_with_nothing_to_run_just_explains_itself(self):
        self.main(choices=(0,))

        self.assertIn("ao3downloader", self.text())
        self.assertEqual(self.ran, [])

    def test_off_a_terminal_it_prints_what_it_can_and_stops(self):
        with patch.object(welcome.prompts, "at_a_terminal", lambda: False):
            exit_code = welcome.main(emit=self.emit, runner=self.runner)

        self.assertEqual(exit_code, 0)
        self.assertIn("Download the works", self.text())
        self.assertEqual(self.ran, [])
