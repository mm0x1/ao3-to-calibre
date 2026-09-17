"""Publish each work's AO3 numbers to BookOrbit's custom metadata fields.

BookOrbit is optional. A library that does not use it simply never sets
``BOOKORBIT_URL``, and every entry point here reports "not configured" and
stops rather than failing.

Why this needs the API at all: BookOrbit's scanner reads titles, authors, tags
and genres out of each file, but never custom-field values, so the numbers this
project bakes into an EPUB cannot reach a sortable column on their own. They are
sent once per book. Custom values live in their own table, which a scan never
writes and never clears, so a value set once survives every later scan and a
rerun of this sync sends nothing.

The rating, and only the rating, is also a tag on the file itself, so it is
filterable without any of this.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import logging
import os
from pathlib import Path, PurePath
import re
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

import requests

from ao3archiver.common import ArchiverError, REPO_ROOT
from ao3archiver.credentials import load_dotenv_values
from ao3archiver.metadata import canonical_rating, epub_rating_subjects, read_ao3_metadata
from ao3archiver import prompts
from ao3archiver.run_log import redact_secrets, register_secret

LOGGER = logging.getLogger("ao3.bookorbit")

DEFAULT_DOTENV_PATH = REPO_ROOT / ".env"
CONFIG_KEYS = (
    "BOOKORBIT_URL",
    "BOOKORBIT_TOKEN",
    "BOOKORBIT_USERNAME",
    "BOOKORBIT_PASSWORD",
    "BOOKORBIT_LIBRARY_ID",
)

DEFAULT_TIMEOUT_SECONDS = 30.0
# The server's own default is 15 minutes (JWT_EXPIRES_IN); re-mint early rather
# than discover the expiry as a failed write part-way through a long run.
TOKEN_LIFETIME_SECONDS = 12 * 60.0
SIGN_IN_PATH = "/auth/login"
PROGRESS_EVERY = 500
# A run that cannot write this many books in a row is hitting something systemic
# (a permission, a wrong library), not one odd book.
CONSECUTIVE_FAILURE_LIMIT = 10

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# What to do about it when the command is run without BookOrbit set up. Not an
# error: most libraries never use BookOrbit, and every other command works
# without it.
SETUP_STEPS = (
    "BookOrbit is not configured (no BOOKORBIT_URL), so nothing was published.",
    "To publish to it, copy .env.example to .env and fill in:",
    "    BOOKORBIT_URL=http://your-server:8080",
    "    BOOKORBIT_USERNAME= and BOOKORBIT_PASSWORD=   (or BOOKORBIT_TOKEN=)",
    "Then add the custom fields in BookOrbit, under Settings -> Metadata -> Custom Metadata.",
    "docs/BOOKORBIT.md lists the fields, their types, and the two permissions the account needs.",
)

# Each BookOrbit field, by the exact label it is created with, and where its
# value comes from on an enriched EPUB. Types match what BookOrbit stores:
# `number` sorts numerically, `text` and `url` sort as text, `date` needs
# YYYY-MM-DD and is dropped when AO3 phrased it any other way.
FIELD_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("AO3 Rating", "text", "rating"),
    ("AO3 Category", "text", "category"),
    ("AO3 Kudos", "number", "kudos"),
    ("AO3 Hits", "number", "hits"),
    ("AO3 Comments", "number", "comments"),
    ("AO3 Bookmarks", "number", "bookmarks"),
    ("AO3 Words", "number", "words"),
    ("AO3 Chapters", "text", "chapters"),
    ("AO3 Published", "date", "published"),
    ("AO3 Updated", "date", "updated"),
    ("AO3 Status", "text", "status"),
    ("Local Words", "number", "local_words"),
    ("Gunning Fog", "number", "local_gfog"),
    ("AO3 Link", "url", "work_url"),
)

# Other labels that mean the same value. A field named after this project's own
# Calibre columns (#words, #gfog) is at least as natural a choice as the label
# in the README, so both fill.
FIELD_ALIASES: dict[str, str] = {
    "Words": "Local Words",
    "Local Word Count": "Local Words",
    "Gfog": "Gunning Fog",
    "Gunning Fog Index": "Gunning Fog",
}

EPUB_ERRORS = (OSError, ValueError, zipfile.BadZipFile, ET.ParseError)


class BookOrbitError(ArchiverError):
    """A BookOrbit request failed, or its configuration is unusable."""


@dataclass(frozen=True)
class BookOrbitConfig:
    """Where BookOrbit is and how to authenticate to it."""

    base_url: str
    token: str | None
    username: str | None
    password: str | None
    library_id: int | None
    source: str


@dataclass(frozen=True)
class CustomField:
    """One custom metadata field as BookOrbit defines it."""

    field_id: int
    label: str
    type: str
    key: str


@dataclass(frozen=True)
class SyncSummary:
    """What one sync did, whether or not BookOrbit was configured."""

    configured: bool
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    unmatched: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    interrupted: bool = False


def normalize_base_url(value: str) -> str:
    """Accept a bare host or a full API URL and return the API root."""

    base = value.strip().rstrip("/")
    if not base:
        raise BookOrbitError("BOOKORBIT_URL is empty")
    if not base.startswith(("http://", "https://")):
        base = f"http://{base}"
    return base if base.endswith("/api/v1") else f"{base}/api/v1"


def load_bookorbit_config(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path | None = DEFAULT_DOTENV_PATH,
) -> BookOrbitConfig | None:
    """Read the BookOrbit settings, or return None when there are none.

    None is the normal case for anyone who does not run BookOrbit; it is not an
    error and callers treat it as "nothing to publish to".
    """

    values = dict(load_dotenv_values(dotenv_path, keys=CONFIG_KEYS) if dotenv_path is not None else {})
    process_values = os.environ if environ is None else environ
    from_environment = [key for key in CONFIG_KEYS if key in process_values]
    for key in from_environment:
        values[key] = process_values[key]

    url = (values.get("BOOKORBIT_URL") or "").strip()
    if not url:
        return None

    token = (values.get("BOOKORBIT_TOKEN") or "").strip() or None
    username = (values.get("BOOKORBIT_USERNAME") or "").strip() or None
    password = values.get("BOOKORBIT_PASSWORD") or None
    if token is None and not (username and password):
        raise BookOrbitError(
            "BOOKORBIT_URL is set but no credentials are. Set BOOKORBIT_TOKEN, or "
            "BOOKORBIT_USERNAME and BOOKORBIT_PASSWORD."
        )
    raw_library = (values.get("BOOKORBIT_LIBRARY_ID") or "").strip()
    if raw_library and not raw_library.isdigit():
        raise BookOrbitError("BOOKORBIT_LIBRARY_ID must be a number")

    for secret in (token, password):
        if secret:
            register_secret(secret)
    source = (
        f"process environment ({', '.join(from_environment)})"
        if from_environment
        else str(dotenv_path)
    )
    return BookOrbitConfig(
        base_url=normalize_base_url(url),
        token=token,
        username=username,
        password=password,
        library_id=int(raw_library) if raw_library else None,
        source=source,
    )


def slugify_label(label: str) -> str:
    """Mirror the key BookOrbit derives from a field's label when it is created."""

    normalized = unicodedata.normalize("NFKD", label).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:100]
    return slug or "custom_field"


class BookOrbitClient:
    """The few BookOrbit calls this project makes, and nothing else."""

    def __init__(
        self,
        config: BookOrbitConfig,
        *,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        monotonic_fn=time.monotonic,
    ) -> None:
        self.config = config
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.monotonic_fn = monotonic_fn
        self._token: str | None = None
        self._minted_at = 0.0

    # --- authentication ---------------------------------------------------

    def _sign_in(self) -> None:
        if self.config.token:
            self._token, self._minted_at = self.config.token, self.monotonic_fn()
            return
        response = self.session.post(
            f"{self.config.base_url}{SIGN_IN_PATH}",
            json={"username": self.config.username, "password": self.config.password},
            timeout=self.timeout,
        )
        if response.status_code == 429:
            raise BookOrbitError(
                "BookOrbit is rate-limiting sign-ins (it allows 5 a minute); wait a minute and rerun"
            )
        if response.status_code >= 400:
            raise BookOrbitError(
                f"BookOrbit rejected the sign-in with HTTP {response.status_code}. "
                "Check BOOKORBIT_USERNAME and BOOKORBIT_PASSWORD."
            )
        token = (response.json() or {}).get("accessToken")
        if not isinstance(token, str) or not token:
            raise BookOrbitError("BookOrbit's sign-in response contained no access token")
        register_secret(token)
        self._token, self._minted_at = token, self.monotonic_fn()

    def _scrub(self, text: str) -> str:
        """Strip this client's own credentials out of a message.

        ``redact_secrets`` only knows what a configured run registered with it,
        and this class is usable without one, so the values it holds are
        removed directly as well.
        """

        scrubbed = redact_secrets(text)
        for secret in (self.config.password, self.config.token, self._token):
            if secret:
                scrubbed = scrubbed.replace(secret, "***")
        return scrubbed

    def _authorization(self) -> str:
        expired = self.monotonic_fn() - self._minted_at >= TOKEN_LIFETIME_SECONDS
        if self._token is None or (expired and not self.config.token):
            self._sign_in()
        return f"Bearer {self._token}"

    # --- requests ---------------------------------------------------------

    def _request(self, method: str, path: str, *, json_body: object | None = None, retry: bool = True):
        try:
            response = self.session.request(
                method,
                f"{self.config.base_url}{path}",
                json=json_body,
                headers={"Authorization": self._authorization()},
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise BookOrbitError(f"Could not reach BookOrbit at {self.config.base_url}: {error}") from error

        if response.status_code == 401 and retry and not self.config.token:
            # The token aged out mid-run; mint a new one and try once more.
            self._token = None
            return self._request(method, path, json_body=json_body, retry=False)
        if response.status_code == 403:
            raise BookOrbitError(
                f"BookOrbit refused {method} {path}: the account lacks the permission for it "
                "(library_edit_metadata to write, library_download to export)"
            )
        if response.status_code >= 400:
            detail = self._scrub(response.text or "")[:200]
            raise BookOrbitError(f"BookOrbit {method} {path} failed: HTTP {response.status_code} {detail}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise BookOrbitError(f"BookOrbit {method} {path} did not return JSON") from error

    # --- the calls --------------------------------------------------------

    def custom_fields(self) -> tuple[CustomField, ...]:
        """Active custom fields, with their real keys when the account may read them.

        The unprivileged listing omits each field's key, so it is derived from
        the label the same way BookOrbit derives it at creation. That only
        matters for recognising a field's values in an export, never for
        writing them, which goes by id.
        """

        rows = None
        try:
            rows = self._request("GET", "/custom-metadata/fields")
        except BookOrbitError:
            rows = None
        derived_keys = rows is None
        if rows is None:
            rows = self._request("GET", "/custom-metadata/fields/active")
        fields = []
        for row in rows or []:
            if not isinstance(row, dict) or row.get("archivedAt"):
                continue
            try:
                field_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                # A field this client cannot address is a field it skips: a
                # malformed row must not end the run with a bare traceback.
                LOGGER.warning(f"ignoring a BookOrbit custom field with no usable id: {row!r:.120}")
                continue
            label = str(row.get("label") or "")
            key = str(row.get("key") or "") if not derived_keys else ""
            if not label:
                LOGGER.warning(f"ignoring BookOrbit custom field {field_id} because it has no label")
                continue
            fields.append(
                CustomField(
                    field_id=field_id,
                    label=label,
                    type=str(row.get("type") or ""),
                    key=key or slugify_label(label),
                )
            )
        return tuple(fields)

    def library_books(self, library_id: int | None = None) -> tuple[dict, ...]:
        """Every book BookOrbit holds, with its file paths and current custom values."""

        query: dict[str, object] = {}
        if library_id is not None:
            query["libraryId"] = library_id
        payload = self._request(
            "POST",
            "/books/metadata-export/download",
            json_body={
                "format": "json",
                "query": query,
                "options": {"includeFilePaths": True, "includeContextMeta": False, "columnsMode": "canonical"},
            },
        )
        items = (payload or {}).get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise BookOrbitError("BookOrbit's metadata export did not contain an item list")
        return tuple(item for item in items if isinstance(item, dict))

    def set_custom_values(self, book_id: int, values: Sequence[dict[str, object]]) -> None:
        """Write custom field values, leaving every other field of the book alone."""

        self._request("PATCH", f"/books/{book_id}/metadata", json_body={"customMetadata": list(values)})


# --- reading the values off an EPUB ----------------------------------------


def epub_field_values(epub_path: Path) -> dict[str, object]:
    """``{field label: value}`` for one enriched EPUB, skipping what it does not carry."""

    metadata = read_ao3_metadata(epub_path)
    rating = next((canonical_rating(subject) for subject in epub_rating_subjects(epub_path)), None)
    metadata = replace(metadata, rating=rating)
    values: dict[str, object] = {}
    for label, kind, attribute in FIELD_SOURCES:
        value = getattr(metadata, attribute, None)
        if value is None or value == "":
            continue
        if kind == "date" and not DATE_PATTERN.match(str(value)):
            # AO3 words some of these as "Completed: 12 Mar 2019"; BookOrbit
            # rejects anything but YYYY-MM-DD, so leave the field empty.
            continue
        values[label] = value
    return values


# --- matching a local file to the book BookOrbit scanned --------------------


def _match_keys(path: str) -> tuple[str, ...]:
    """The path tails that identify a book across two different library roots.

    The server's absolute paths are not the local ones, but rsync preserves the
    tail, and a Calibre folder name carries the book's own id, so folder plus
    filename is as good as a key. The filename alone is the fallback for a flat
    folder such as ``downloaded/``.
    """

    parts = PurePath(path).parts
    name = parts[-1].casefold() if parts else ""
    if not name:
        return ()
    if len(parts) >= 2:
        return (f"{parts[-2].casefold()}/{name}", name)
    return (name,)


def index_by_path(items: Iterable[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    """Index exported books by path tail, dropping any tail that is not unique."""

    index: dict[str, Mapping[str, object]] = {}
    ambiguous: set[str] = set()
    for item in items:
        paths = item.get("filePaths")
        for path in paths if isinstance(paths, list) else []:
            if not isinstance(path, str):
                continue
            for key in _match_keys(path):
                if key in index and index[key] is not item:
                    ambiguous.add(key)
                index[key] = item
    for key in ambiguous:
        index.pop(key, None)
    return index


def find_book(index: Mapping[str, Mapping[str, object]], epub_path: Path) -> Mapping[str, object] | None:
    for key in _match_keys(str(epub_path)):
        found = index.get(key)
        if found is not None:
            return found
    return None


def resolve_fields(fields: Iterable[CustomField]) -> tuple[tuple[str, CustomField], ...]:
    """Pair each BookOrbit field with the value it should hold.

    A value can be paired with more than one field, since a library may have
    both "Local Words" and a "Words" named after the Calibre column.
    """

    known = {label for label, _, _ in FIELD_SOURCES}
    pairs = []
    for field in fields:
        canonical = field.label if field.label in known else FIELD_ALIASES.get(field.label)
        if canonical is not None:
            pairs.append((canonical, field))
    return tuple(pairs)


def changed_values(
    wanted: Mapping[str, object],
    book: Mapping[str, object],
    fields: Sequence[tuple[str, CustomField]],
) -> list[dict[str, object]]:
    """The field writes this book still needs, as BookOrbit's PATCH body wants them."""

    changes = []
    for label, field in fields:
        if label not in wanted:
            continue
        value = wanted[label]
        current = book.get(f"custom.{field.key}")
        if current is not None and _same_value(current, value, field.type):
            continue
        changes.append({"fieldId": field.field_id, "value": value})
    return changes


def _same_value(current: object, wanted: object, field_type: str) -> bool:
    if field_type == "number":
        try:
            return float(current) == float(wanted)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
    return str(current) == str(wanted)


# --- the sync ---------------------------------------------------------------


def sync_epubs(
    epub_paths: Sequence[Path],
    *,
    config: BookOrbitConfig | None = None,
    client: BookOrbitClient | None = None,
    dry_run: bool = False,
    library_id: int | None = None,
    assume_yes: bool = False,
) -> SyncSummary:
    """Send each EPUB's AO3 numbers to the matching BookOrbit book.

    Returns a summary with ``configured=False``, and does nothing at all, when
    BookOrbit is not set up: that is the normal state for anyone who does not
    run it.
    """

    if client is None:
        config = config if config is not None else load_bookorbit_config()
        if config is None:
            for line in SETUP_STEPS:
                LOGGER.info(line)
            return SyncSummary(configured=False)
        client = BookOrbitClient(config)
    resolved_library = library_id if library_id is not None else (client.config.library_id)

    usable = resolve_fields(client.custom_fields())
    filled = {label for label, _ in usable}
    missing = tuple(label for label, _, _ in FIELD_SOURCES if label not in filled)
    if not usable:
        LOGGER.warning(
            "BookOrbit has none of the expected custom fields yet; create them in "
            "Settings -> Metadata -> Custom Metadata first"
        )
        return SyncSummary(configured=True, missing_fields=missing)
    if missing:
        for label in missing:
            LOGGER.warning(f"BookOrbit has no custom field labelled {label!r}; its value is not published")
        if not prompts.confirm(
            f"{len(missing)} of the {len(FIELD_SOURCES)} fields do not exist in BookOrbit; "
            f"publish the {len(usable)} that do?",
            default=True,
            assume_yes=assume_yes,
        ):
            return SyncSummary(configured=True, missing_fields=missing)

    checked: list[tuple[str, CustomField]] = []
    for label, field in usable:
        expected = next(kind for name, kind, _ in FIELD_SOURCES if name == label)
        if field.label != label:
            LOGGER.info(f"publishing {label!r} into BookOrbit's {field.label!r} field")
        if field.type != expected:
            LOGGER.warning(
                f"BookOrbit field {field.label!r} is a {field.type} field, not {expected}; "
                "sorting may not behave as expected"
            )
            if not prompts.confirm(
                f"Publish {label!r} into it anyway?", default=True, assume_yes=assume_yes
            ):
                continue
        checked.append((label, field))
    usable = tuple(checked)
    if not usable:
        LOGGER.info("no BookOrbit fields left to publish to; nothing was written")
        return SyncSummary(configured=True, missing_fields=missing)

    books = client.library_books(resolved_library)
    index = index_by_path(books)
    LOGGER.info(f"BookOrbit holds {len(books)} books; publishing {len(usable)} fields per book")

    updated: list[str] = []
    unchanged: list[str] = []
    unmatched: list[str] = []
    unreadable: list[str] = []
    failed: list[tuple[str, str]] = []
    streak = 0
    interrupted = False
    # One book is one request, so stopping between books always leaves a book
    # either fully written or untouched, and the next run carries on.
    try:
        for checked, epub_path in enumerate(epub_paths, start=1):
            if checked % PROGRESS_EVERY == 0:
                LOGGER.info(f"  {checked}/{len(epub_paths)} checked, {len(updated)} updated")
            name = epub_path.name
            try:
                wanted = epub_field_values(epub_path)
            except EPUB_ERRORS:
                unreadable.append(str(epub_path))
                continue
            book = find_book(index, epub_path)
            if book is None:
                unmatched.append(str(epub_path))
                continue
            changes = changed_values(wanted, book, usable)
            if not changes:
                unchanged.append(name)
                continue
            if dry_run:
                updated.append(name)
                continue
            try:
                client.set_custom_values(int(book["bookId"]), changes)
            except (BookOrbitError, KeyError, TypeError, ValueError) as error:
                failed.append((name, str(error)))
                streak += 1
                if streak >= CONSECUTIVE_FAILURE_LIMIT:
                    raise BookOrbitError(
                        f"{streak} BookOrbit writes failed in a row; stopping. Last error: {error}"
                    ) from error
                continue
            streak = 0
            updated.append(name)
    except KeyboardInterrupt:
        interrupted = True
        LOGGER.warning("stopped; rerun the same command to carry on from what is already published")

    return SyncSummary(
        configured=True,
        updated=tuple(updated),
        unchanged=tuple(unchanged),
        unmatched=tuple(unmatched),
        unreadable=tuple(unreadable),
        missing_fields=missing,
        failed=tuple(failed),
        interrupted=interrupted,
    )
