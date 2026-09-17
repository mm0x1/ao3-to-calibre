"""The front door: what this project does, whether it is set up, and what to run.

Deliberately a teacher rather than a wrapper. Every flow prints the exact
command it would run before offering to run it, because the commands are what
the guides in ``docs/`` document and what an unattended run needs. Nothing here
is required: ``download.py`` and ``backfill.py`` behave exactly as they always
have, with or without this screen.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from ao3archiver import prompts
from ao3archiver.bookorbit import BookOrbitError, load_bookorbit_config
from ao3archiver.common import ArchiverError, REPO_ROOT
from ao3archiver.credentials import (
    CredentialError,
    DEFAULT_DOTENV_PATH,
    DEFAULT_INI_PATH,
    load_dotenv_values,
    resolve_credentials,
)
from ao3archiver.download import DEFAULT_LINKS_DIR, DEFAULT_OUTPUT_DIR, load_link_inventory

# Where the Calibre library is, so the commands printed here are ready to run.
# Optional: without it they carry a placeholder path instead.
CALIBRE_LIBRARY_KEY = "CALIBRE_LIBRARY"
LIBRARY_PLACEHOLDER = "/path/to/Calibre Library"
EPUB_BACKUP_DIR = "~/.local/share/ao3-calibre-backfill/epub-originals-repair"

SUMMARY = (
    "Downloads Archive of Our Own works as EPUBs with their AO3 statistics baked in:",
    "kudos, hits, words, a local word count and a readability score, ready to sort on.",
)

DOCS = (
    ("docs/TUTORIAL.md", "the whole run, end to end"),
    ("docs/DOWNLOADING.md", "every download.py option, and unattended runs"),
    ("docs/BACKFILL.md", "every backfill.py subcommand"),
    ("docs/BOOKORBIT.md", "publishing to a BookOrbit library"),
    ("docs/CONFIGURATION.md", ".env keys, paths, and the SSH alias"),
)

MARKS = {True: "ok", False: "--", None: "  "}


@dataclass(frozen=True)
class Check:
    """One line of the setup check. ``ok`` is None for an optional part."""

    label: str
    ok: bool | None
    detail: str


@dataclass(frozen=True)
class Flow:
    """One thing someone might want to do, and the commands that do it."""

    title: str
    explanation: tuple[str, ...]
    commands: tuple[str, ...] = ()
    # A read-only first step (a dry run) is the only thing offered to run from
    # here; anything that writes is printed for the person to run themselves.
    preview: str | None = None


def library_path(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path | None = DEFAULT_DOTENV_PATH,
) -> Path | None:
    """The Calibre library named in ``CALIBRE_LIBRARY``, if there is one."""

    values = dict(load_dotenv_values(dotenv_path, keys=(CALIBRE_LIBRARY_KEY,)) if dotenv_path is not None else {})
    process = os.environ if environ is None else environ
    if CALIBRE_LIBRARY_KEY in process:
        values[CALIBRE_LIBRARY_KEY] = process[CALIBRE_LIBRARY_KEY]
    raw = (values.get(CALIBRE_LIBRARY_KEY) or "").strip()
    return Path(raw).expanduser() if raw else None


def setup_checks(
    *,
    dotenv_path: Path = DEFAULT_DOTENV_PATH,
    ini_path: Path = DEFAULT_INI_PATH,
    environ: Mapping[str, str] | None = None,
    links_dir: Path = DEFAULT_LINKS_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    library: Path | None = None,
) -> tuple[Check, ...]:
    """Answer, one line each, every question someone asks before their first run."""

    checks = [Check(".env", dotenv_path.is_file(), str(dotenv_path) if dotenv_path.is_file() else "copy .env.example to .env")]

    try:
        credentials = resolve_credentials(dotenv_path=dotenv_path, ini_path=ini_path, environ=environ)
        checks.append(Check("AO3 credentials", True, f"from {credentials.source}"))
    except (CredentialError, OSError) as error:
        checks.append(Check("AO3 credentials", False, str(error).split(".")[0]))

    if library is None:
        checks.append(Check("Calibre library", None, f"{CALIBRE_LIBRARY_KEY} is not set (only the backfill needs it)"))
    elif (library / "metadata.db").is_file():
        checks.append(Check("Calibre library", True, str(library)))
    else:
        checks.append(Check("Calibre library", False, f"no metadata.db in {library}"))

    try:
        config = load_bookorbit_config(environ, dotenv_path=dotenv_path)
        checks.append(
            Check("BookOrbit", None, "not configured (optional)")
            if config is None
            else Check("BookOrbit", True, config.base_url)
        )
    except (BookOrbitError, OSError) as error:
        checks.append(Check("BookOrbit", False, str(error)))

    if links_dir.is_dir():
        inventory = load_link_inventory(links_dir)
        checks.append(
            Check(
                "links/",
                bool(inventory.targets),
                f"{len(inventory.targets)} works in {inventory.files} file(s)"
                if inventory.targets
                else f"no work links in {links_dir}",
            )
        )
    else:
        checks.append(Check("links/", False, f"no {links_dir} yet"))

    downloaded = len(list(output_dir.glob("*.epub"))) if output_dir.is_dir() else 0
    checks.append(Check("downloaded/", downloaded > 0, f"{downloaded} EPUB(s)"))
    return tuple(checks)


def flows(library: Path | None = None) -> tuple[Flow, ...]:
    """The things this project does, in the order someone does them."""

    shown = f'"{library}"' if library is not None else f'"{LIBRARY_PLACEHOLDER}"'
    return (
        Flow(
            "Harvest links from AO3",
            (
                "ao3downloader (github.com/nianeyna/ao3downloader) saves every work link",
                "from an AO3 search as a .txt file. Copy those files into links/.",
                "Nothing in this project needs to run for this step.",
            ),
        ),
        Flow(
            "Download the works",
            (
                "Reads each link, fetches the work page and its EPUB, calculates the local",
                "word count and readability score, and writes it all into the file.",
                "The dry run shows the plan and an estimated finish time, and asks for nothing.",
            ),
            commands=("python3 download.py --dry-run", "python3 download.py"),
            preview="python3 download.py --dry-run",
        ),
        Flow(
            "Import into Calibre",
            (
                "Drag the EPUBs from downloaded/ into Calibre, or add them from a terminal.",
                "The custom columns fill in on import - create them first, either in Calibre",
                "or with `backfill.py setup-columns`. See docs/TUTORIAL.md.",
            ),
            commands=(f"calibredb add --library-path {shown} downloaded/",),
        ),
        Flow(
            "Repair EPUBs already in the library",
            (
                "Rewrites files no reader but Calibre can open, and adds the AO3 rating tag",
                "where it is missing. Offline, and every original is copied first.",
            ),
            commands=(
                f"python3 backfill.py repair-epubs --library {shown} \\\n"
                f"    --epub-backup-dir {EPUB_BACKUP_DIR} --dry-run",
                f"python3 backfill.py repair-epubs --library {shown} \\\n"
                f"    --epub-backup-dir {EPUB_BACKUP_DIR} --approve-epub-write",
            ),
        ),
        Flow(
            "Copy the files to a reading server",
            (
                "rsync sends only what changed. Define the server once in ~/.ssh/config so no",
                "host or key appears in the command, then run the dry run first.",
            ),
            commands=(
                "rsync -rt --stats --dry-run downloaded/ myserver:/path/to/library/fanfiction/",
                "rsync -rt --stats downloaded/ myserver:/path/to/library/fanfiction/",
            ),
        ),
        Flow(
            "Publish the numbers to BookOrbit",
            (
                "A library server reads tags out of each file, but never custom-field values,",
                "so kudos, hits and word counts are sent to its API once per book.",
                "Optional: without BOOKORBIT_URL the command says so and stops.",
            ),
            commands=(
                "python3 backfill.py bookorbit-sync --library downloaded --dry-run",
                "python3 backfill.py bookorbit-sync --library downloaded",
            ),
            preview="python3 backfill.py bookorbit-sync --library downloaded --dry-run",
        ),
        Flow(
            "Read the documentation",
            tuple(f"{path:<24} {what}" for path, what in DOCS),
        ),
    )


def render(lines: Sequence[str], emit: Callable[[str], None]) -> None:
    for line in lines:
        emit(line)


def screen(checks: Sequence[Check]) -> tuple[str, ...]:
    """The banner and the setup check, as printed lines."""

    width = max(len(check.label) for check in checks)
    return (
        "",
        "ao3Archiver",
        *(f"  {line}" for line in SUMMARY),
        "",
        "Setup",
        *(f"  [{MARKS[check.ok]}]  {check.label.ljust(width)}  {check.detail}" for check in checks),
    )


def describe(flow: Flow) -> tuple[str, ...]:
    lines = ["", flow.title, *(f"  {line}" for line in flow.explanation)]
    if flow.commands:
        lines.append("")
        lines += [f"  $ {command}" for command in flow.commands]
    return tuple(lines)


def run_command(command: str, *, runner: Callable[[Sequence[str]], int] | None = None) -> int:
    """Run a printed command from the repository root, exactly as it was shown."""

    if runner is None:
        return subprocess.call(command.split(), cwd=REPO_ROOT)
    return runner(command.split())


def main(
    argv: Sequence[str] | None = None,
    *,
    emit: Callable[[str], None] = print,
    runner: Callable[[Sequence[str]], int] | None = None,
    assume_yes: bool = False,
) -> int:
    """Show the setup check and the menu, and keep showing it until asked to stop."""

    library = library_path()
    render(screen(setup_checks(library=library)), emit)
    available = flows(library)
    titles = tuple(flow.title for flow in available)

    while True:
        chosen = prompts.choose("What would you like to do?", titles, assume_yes=assume_yes)
        if chosen is None:
            if not prompts.at_a_terminal() or assume_yes:
                # Nobody is choosing: list what there is and leave.
                render(("", "What this project does:", *(f"  - {title}" for title in titles), ""), emit)
            return 0
        flow = available[chosen]
        render(describe(flow), emit)
        if flow.preview is None:
            emit("")
            continue
        emit("")
        if prompts.confirm(f"Run `{flow.preview}` now?", default=False, assume_yes=assume_yes):
            run_command(flow.preview, runner=runner)


def run() -> None:
    """Command-line entry point used by ``archiver.py``."""

    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except ArchiverError as error:
        print(error)
        raise SystemExit(2) from error
