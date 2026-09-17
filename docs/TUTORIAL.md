# Tutorial: from an AO3 search to a sortable library

The whole thing, end to end, once. Every step links to the reference guide that
holds its options. Nothing here needs the step before it to have gone perfectly:
each command can be rerun, and each one says what it would do before it does it.

1. [Before you start](#before-you-start)
2. [Harvest links](#1-harvest-links)
3. [Download](#2-download)
4. [Import into Calibre](#3-import-into-calibre)
5. [Repair what a reader cannot open](#4-repair-what-a-reader-cannot-open)
6. [Copy to a reading server](#5-copy-to-a-reading-server-optional)
7. [Publish the numbers](#6-publish-the-numbers-optional)
8. [What goes into each EPUB](#what-goes-into-each-epub)

## Before you start

```sh
git clone https://github.com/mm0x1/ao3-to-calibre.git && cd ao3-to-calibre
python3 -m venv venv && source venv/bin/activate   # optional
pip install -r requirements.txt
cp .env.example .env
```

Fill in `AO3_USERNAME` and `AO3_PASSWORD` in `.env`. It is ignored by Git, and
no tool here prints, logs or stores those values.
[docs/CONFIGURATION.md](CONFIGURATION.md) lists every key.

`python3 archiver.py` checks all of this and prints the command for each step
below, so you can follow along from there instead of reading this file.

**Calibre custom columns must exist before you import anything.** Calibre
silently drops values for columns a library does not have. Create them once,
either in *Preferences → Add your own columns* or with
[`backfill.py setup-columns`](BACKFILL.md#2-create-the-columns-after-a-verified-backup):

| Lookup name | Heading | Type |
| --- | --- | --- |
| `ao3_kudos` | AO3 Kudos | Integers |
| `ao3_hits` | AO3 Hits | Integers |
| `ao3_bookmarks` | AO3 Bookmarks | Integers |
| `ao3_comments` | AO3 Comments | Integers |
| `ao3_words` | AO3 Words | Integers |
| `ao3_chapters` | AO3 Chapters | Text |
| `ao3_status` | AO3 Status | Text |
| `ao3_category` | AO3 Category | Text |
| `words` | Words | Integers |
| `gfog` | Gfog | Floating point numbers |

## 1. Harvest links

1. Set up [ao3downloader](https://github.com/nianeyna/ao3downloader) following
   its README, including the Python version it asks for.
2. Run `python ao3downloader.py` and choose
   `l: get all work links from an ao3 listing (saves links only)`.
3. Paste the URL of an AO3 search results page. Logging in is optional.
4. Repeat for as many searches as you like.

Copy the resulting `.txt` files into a `links/` folder in this repository.
Duplicate links across files are fine: they are counted and collapsed.

## 2. Download

Preview the plan first, then run it:

```sh
python3 download.py --dry-run
python3 download.py
```

The plan shows how many links there are, how many are duplicates, how many are
already done, and an estimated finish time. At a terminal the real run shows the
same plan and asks once before the first request; under `nohup` there is nobody
to ask, so it starts. Every run after the first resumes automatically: finished
EPUBs are skipped and half-finished ones are completed.

For each work the script reads the AO3 work page, skips and records anything AO3
reports as deleted or unrevealed, downloads the EPUB from the page's own
Download link, calculates local Words and Gunning Fog from the file, then writes
everything into the EPUB and reads it back to confirm Calibre will see it.

A run of tens of thousands of works takes days. Start it in the background and
follow the log:

```sh
nohup python3 download.py &
tail -f "$(ls -t ~/.local/share/ao3-calibre-backfill/logs/download-*.log | head -1)"
```

Ctrl-C (or `kill`) stops at the next safe point, prints a summary, and leaves
the run resumable. [docs/DOWNLOADING.md](DOWNLOADING.md) covers every option and
what happens when AO3 has a bad day.

## 3. Import into Calibre

Drag the EPUBs from `downloaded/` into Calibre, or add them with `calibredb`:

```sh
calibredb add --library-path "/path/to/Calibre Library" downloaded/
```

The ten custom columns fill in on import, and each book also gets its AO3 work
URL as an `ao3` identifier. Sort by **AO3 Kudos**, or search numerically:
`#ao3_kudos:>1000`, `#words:<20000`, `#gfog:<8`. `#ao3_chapters:"~[?]"` finds
works whose final chapter count is still unknown (`3/?`), which covers most
works in progress; a work with a declared total, such as `3/10`, does not match.
(A plain `"?"` matches every book.)

**Calibre-Web** reads the same columns from Calibre's database. Restart it or
rescan after importing, and check that its setting for ignored custom columns
does not match `ao3_`.

**A library you already have** is a different job: see
[docs/BACKFILL.md](BACKFILL.md), which fills the same columns for books that are
already in Calibre and then bakes each book's metadata into its file.

## 4. Repair what a reader cannot open

Only needed for files that came from somewhere else, or from an older version of
this project. It makes no AO3 requests and reads nothing from Calibre's
database:

```sh
python3 backfill.py repair-epubs --library "/path/to/Calibre Library" \
  --epub-backup-dir ~/.local/share/ao3-calibre-backfill/epub-originals-repair \
  --dry-run
```

The dry run prints what each file needs and writes nothing. At a terminal, the
same command without `--dry-run` shows those counts and asks before rewriting;
in a script, pass `--approve-epub-write`. Every original is copied first. See
[docs/BACKFILL.md](BACKFILL.md#repair-epubs).

## 5. Copy to a reading server (optional)

Define the server once in `~/.ssh/config`, so no host, user or key ever appears
in a command:

```sshconfig
Host myserver
    HostName 192.0.2.10
    User reader
    IdentityFile ~/.ssh/myserver
```

Then copy the freshly downloaded EPUBs. `-t` keeps each file's timestamp, which
is how a library server notices which files changed:

```sh
rsync -rt --stats --dry-run downloaded/ myserver:/path/to/library/fanfiction/
rsync -rt --stats downloaded/ myserver:/path/to/library/fanfiction/
```

Run the dry run first: it prints exactly how many files would be sent and how
much data, and changes nothing. Nothing is ever deleted on the server, because
`--delete` is not used.

To send a whole Calibre library instead, keeping its `Author/Title (id)/`
folders and skipping Calibre's own working directories:

```sh
rsync -rt --stats --prune-empty-dirs \
  --exclude='/.caltrash/' --exclude='/.calnotes/' \
  --include='*/' --include='*.epub' --include='cover.jpg' --exclude='*' \
  "/path/to/Calibre Library/" myserver:/path/to/library/fanfiction/
```

Both commands are safe to repeat: rsync sends only what changed, and for a book
whose metadata was rewritten it sends only the changed part of the file.

## 6. Publish the numbers (optional)

A library server reads titles, authors, tags and genres out of each file, so
those arrive with the file itself — including the AO3 rating, which is a tag.
Numbers are different: a server can sort by a custom field, but its scanner
never reads custom-field values out of a file, so they have to be sent to its
API once per book.

```sh
python3 backfill.py bookorbit-sync --library downloaded --dry-run
python3 backfill.py bookorbit-sync --library downloaded
```

Without `BOOKORBIT_URL` the command says so and stops; nothing else in this
project touches BookOrbit or needs it. [docs/BOOKORBIT.md](BOOKORBIT.md) has the
fields to create, the account permissions, and what to expect from a rescan.

## What goes into each EPUB

AO3's EPUB export does not include the work's statistics, so they are read from
the work page and written into the file in three forms:

- **Calibre column values** (`calibre:user_metadata`), which fill the custom
  columns on import;
- **portable `ao3:*` metadata** and an `ao3` identifier, which travel with the
  file outside Calibre;
- **a visible "AO3 statistics" block** in the description, shown in Calibre's
  comments.

| Column | From | Notes |
| --- | --- | --- |
| `#ao3_kudos`, `#ao3_hits`, `#ao3_bookmarks`, `#ao3_comments` | AO3 work page | AO3 omits a Kudos, Comments, or Bookmarks row when the count is zero, so a missing row is recorded as 0. |
| `#ao3_words` | AO3 work page | AO3's own count. |
| `#ao3_chapters` | AO3 work page | For example `12/12`, or `3/?` while in progress. |
| `#ao3_status` | AO3 work page | The date AO3 shows as *Completed* or *Updated*. One-chapter works have none. |
| `#ao3_category` | AO3 work page | For example `F/F` or `Gen, M/M`. |
| `#words` | the EPUB | Every spine body, so it includes AO3's preface and notes. |
| `#gfog` | the EPUB | Gunning Fog index, two decimals (`count-pages-compatible-pure-python-v1`). |

Nothing is written for a value AO3 does not show, so a missing statistic never
blanks a column.

The work's **rating** is a tag rather than a column: it is written as a
`dc:subject`, beside the tags AO3's own export already puts there, so Calibre
shows it as a Tag and other libraries as a Genre with nothing to configure.
AO3's exports usually carry it already; when one does not, it is taken from the
work page that was fetched anyway, or from the EPUB's own front page. A work
whose rating cannot be found anywhere is still saved, with a warning and a count
in the run summary.

EPUB metadata is written with unprefixed element names under a default
namespace, the form AO3 and Calibre both produce. `<opf:package>` means the same
thing, but readers that look elements up by their literal name disagree:
Calibre's importer skips a prefixed custom column, and some readers show an
empty book. That is what [`repair-epubs`](BACKFILL.md#repair-epubs) fixes.

---

Built with [Claude Code](https://claude.com/claude-code). The code, tests and
documentation in this repository were written by Claude under human direction
and review.
