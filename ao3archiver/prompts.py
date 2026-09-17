"""Confirmations that never stand in the way of an unattended run.

The long jobs here are started with ``nohup`` and expected to survive for days,
so a question that blocks would hang exactly the run it is meant to guard.
Every question asked through this module therefore:

1. **is asked only at a terminal.** Off one - under ``nohup``, cron, a pipe, or
   CI - the documented default is taken and the decision is logged instead.
2. **is asked before work starts**, never inside a loop over books or works.
3. **is answered in advance by** ``--yes``.

The ``--approve-*`` flags stay authoritative. A prompt is an alternative to a
flag for someone sitting at a terminal, never a new gate a script must satisfy.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
import sys

LOGGER = logging.getLogger("ao3.prompts")

AFFIRMATIVE = frozenset({"y", "yes"})
NEGATIVE = frozenset({"n", "no"})
QUIT = frozenset({"q", "quit", "exit"})


def at_a_terminal() -> bool:
    """True only when there is a person to answer: both ends are a terminal."""

    streams = (sys.stdin, sys.stdout)
    return all(stream is not None and stream.isatty() for stream in streams)


def _say(message: str) -> None:
    """Talk to the person at the terminal; the log keeps only the decision."""

    print(message)


def _word(answer: bool) -> str:
    return "yes" if answer else "no"


def confirm(
    question: str,
    *,
    default: bool,
    assume_yes: bool = False,
    logger: logging.Logger = LOGGER,
    input_fn: Callable[[str], str] = input,
    terminal_fn: Callable[[], bool] = at_a_terminal,
) -> bool:
    """Ask a yes/no question at a terminal, and answer it for everyone else.

    ``default`` is what an unattended run does, so it is always the behaviour
    the command had before the question existed.
    """

    if assume_yes:
        logger.info(f"{question} yes (--yes)")
        return True
    if not terminal_fn():
        logger.info(f"{question} {_word(default)} (not a terminal; taking the default)")
        return default

    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input_fn(f"{question} {suffix} ").strip().casefold()
        except EOFError:
            logger.info(f"{question} {_word(default)} (end of input; taking the default)")
            return default
        if not answer:
            logger.info(f"{question} {_word(default)}")
            return default
        if answer in AFFIRMATIVE or answer in NEGATIVE:
            chosen = answer in AFFIRMATIVE
            logger.info(f"{question} {_word(chosen)}")
            return chosen
        _say("Please answer yes or no.")


def choose(
    question: str,
    options: Sequence[str],
    *,
    default: int | None = None,
    assume_yes: bool = False,
    logger: logging.Logger = LOGGER,
    input_fn: Callable[[str], str] = input,
    terminal_fn: Callable[[], bool] = at_a_terminal,
) -> int | None:
    """Offer a numbered list and return the chosen index, or None for "none of them".

    Off a terminal, and with ``--yes``, nobody is choosing: the default index
    stands, and ``None`` means the caller carries on without a choice.
    """

    if not options:
        return None
    if assume_yes or not terminal_fn():
        logger.info(f"{question} {default if default is None else options[default]} (not asked)")
        return default

    while True:
        _say("")
        for index, option in enumerate(options, start=1):
            _say(f"  {index}. {option}")
        _say("")
        try:
            answer = input_fn(f"{question} [1-{len(options)}, q to quit] ").strip().casefold()
        except EOFError:
            return default
        if answer in QUIT:
            return None
        if not answer:
            if default is not None:
                return default
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        _say(f"Please enter a number between 1 and {len(options)}, or q to quit.")
