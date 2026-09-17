"""Tests for confirmations that must never block an unattended run."""

from contextlib import redirect_stdout
import io
import logging
import unittest

from ao3archiver import prompts


class Answers:
    """Stands in for a person at a terminal, one typed line at a time."""

    def __init__(self, *lines: str):
        self.lines = list(lines)
        self.questions: list[str] = []

    def __call__(self, question: str) -> str:
        self.questions.append(question)
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


def refuse(question: str) -> str:
    raise AssertionError(f"nobody should have been asked: {question}")


class ConfirmTest(unittest.TestCase):
    def confirm(self, *lines, terminal=True, **options):
        answers = Answers(*lines)
        with redirect_stdout(io.StringIO()) as output:
            result = prompts.confirm(
                "Start?",
                input_fn=answers,
                terminal_fn=lambda: terminal,
                **options,
            )
        return result, answers, output.getvalue()

    def test_nothing_is_asked_off_a_terminal(self):
        """An unattended run must never stop at a question nobody can see."""

        result, answers, _ = self.confirm(terminal=False, default=True)

        self.assertTrue(result)
        self.assertEqual(answers.questions, [])

    def test_off_a_terminal_the_answer_is_the_documented_default(self):
        result, _, _ = self.confirm(terminal=False, default=False)

        self.assertFalse(result)

    def test_yes_skips_the_question_everywhere(self):
        self.assertTrue(
            prompts.confirm("Start?", default=False, assume_yes=True, input_fn=refuse, terminal_fn=lambda: True)
        )
        self.assertTrue(
            prompts.confirm("Start?", default=False, assume_yes=True, input_fn=refuse, terminal_fn=lambda: False)
        )

    def test_an_empty_answer_takes_the_default(self):
        self.assertTrue(self.confirm("", default=True)[0])
        self.assertFalse(self.confirm("", default=False)[0])

    def test_yes_and_no_are_read_in_any_form(self):
        for answer in ("y", "Y", "yes", " YES "):
            self.assertTrue(self.confirm(answer, default=False)[0], answer)
        for answer in ("n", "No", " no "):
            self.assertFalse(self.confirm(answer, default=True)[0], answer)

    def test_an_unreadable_answer_is_asked_again(self):
        result, answers, output = self.confirm("maybe", "y", default=False)

        self.assertTrue(result)
        self.assertEqual(len(answers.questions), 2)
        self.assertIn("yes or no", output)

    def test_the_prompt_shows_which_answer_the_default_is(self):
        self.assertIn("[Y/n]", self.confirm("y", default=True)[1].questions[0])
        self.assertIn("[y/N]", self.confirm("y", default=False)[1].questions[0])

    def test_end_of_input_takes_the_default_rather_than_failing(self):
        result, _, _ = self.confirm(default=True)

        self.assertTrue(result)

    def test_the_decision_is_logged_so_a_run_records_it(self):
        logger = logging.getLogger("ao3.prompts.test")
        with self.assertLogs(logger, level="INFO") as logs:
            prompts.confirm("Start?", default=False, logger=logger, terminal_fn=lambda: False)

        self.assertIn("no", logs.output[0])
        self.assertIn("not a terminal", logs.output[0])


class ChooseTest(unittest.TestCase):
    OPTIONS = ("Download", "Repair", "Publish")

    def choose(self, *lines, terminal=True, **options):
        answers = Answers(*lines)
        with redirect_stdout(io.StringIO()) as output:
            result = prompts.choose(
                "Which?",
                self.OPTIONS,
                input_fn=answers,
                terminal_fn=lambda: terminal,
                **options,
            )
        return result, output.getvalue()

    def test_a_number_picks_that_option(self):
        self.assertEqual(self.choose("2")[0], 1)

    def test_every_option_is_shown_numbered(self):
        _, output = self.choose("1")

        for index, option in enumerate(self.OPTIONS, start=1):
            self.assertIn(f"{index}. {option}", output)

    def test_quitting_chooses_nothing(self):
        self.assertIsNone(self.choose("q")[0])

    def test_a_number_outside_the_list_is_asked_again(self):
        result, output = self.choose("9", "0", "3")

        self.assertEqual(result, 2)
        self.assertIn("between 1 and 3", output)

    def test_off_a_terminal_the_default_stands_unasked(self):
        self.assertEqual(self.choose(terminal=False, default=1)[0], 1)
        self.assertIsNone(self.choose(terminal=False)[0])

    def test_an_empty_list_offers_nothing(self):
        self.assertIsNone(prompts.choose("Which?", (), terminal_fn=lambda: True, input_fn=refuse))
