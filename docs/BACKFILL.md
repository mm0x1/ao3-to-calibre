# Backfilling a Calibre library

`backfill.py` fills the AO3 columns for EPUBs that are **already in a Calibre
library**, once, and then bakes each book's full metadata into its EPUB file. It
is a one-time job with a stage per command, each behind its own gate.

Steps 1–5 change only Calibre's custom columns. Step 6 rewrites the EPUB files,
after backing up every original. Every stage that talks to AO3 or changes the
library needs an explicit `--approve-…` flag.

**Close Calibre and Calibre-Web first.** The commands that change the library
check this and refuse to run otherwise; `backfill.py processes` says what is
still holding it.

1. [The walkthrough](#the-walkthrough)
2. [Every subcommand](#every-subcommand)
3. [repair-epubs](#repair-epubs)
4. [Common options](#common-options)

## The walkthrough

### 1. Scan the library (read-only)

Maps every EPUB to its AO3 work from the preface and saves a report:

```sh
python3 backfill.py scan --library "/path/to/Calibre Library"
```

Local metrics are calculated for the first 25 works by default. Use
`--metrics-limit N`, or `--metrics-all` for every EPUB, if you want more.
`calculate-missing-metrics` later fills just the blanks, which is usually all
that is needed.

### 2. Create the columns, after a verified backup

```sh
python3 backfill.py backup --library "/path/to/Calibre Library"
python3 backfill.py setup-columns --library "/path/to/Calibre Library" \
  --backup "<path printed by backup>" --approve-columns
```

Each `backup` creates a new timestamped file, verifies it, and prints the exact
`--backup "…"` to pass next. Backups are never overwritten.

### 3. Check the login

One round trip, before starting a fetch that runs for days:

```sh
python3 backfill.py check-auth --approve-network
```

### 4. Fetch AO3 statistics

Into an append-only cache. At 10 seconds per work, about 15,000 works take
roughly two days:

```sh
nohup python3 backfill.py fetch --library "/path/to/Calibre Library" \
  --use-env-credentials --approve-network \
  --continue-after-review --keep-going --delay 10 --limit 15000 &
```

- Without `--continue-after-review` a batch is capped at 25 works, so you can
  review a small run first. `--delay` defaults to 30 seconds, with a minimum of 5.
- `--keep-going` makes the run unattended, with the same failure handling as
  [downloads](DOWNLOADING.md#leaving-it-running). Without it, the first failure
  stops the run.
- `--retry-failed-once` also re-requests works that came back incomplete or
  unavailable, and retries an unrecognised page once.
- `--refresh` re-fetches works that are already cached, and
  `--include-ambiguous` includes EPUBs whose preface links more than one work.
- Every result is written to disk as it arrives. To resume after a stop, run the
  same command again.

Check progress from another terminal at any time:

```sh
python3 backfill.py validate-cache
```

### 5. Write to Calibre

Fill in any missing local metrics, take a backup immediately before writing, and
write one book first:

```sh
python3 backfill.py calculate-missing-metrics --library "/path/to/Calibre Library"
python3 backfill.py backup --library "/path/to/Calibre Library"
python3 backfill.py write --library "/path/to/Calibre Library" \
  --backup "<path printed by backup>" \
  --write-local-metrics --approve-write --limit 1
```

Check that book in Calibre. Then take a fresh backup, since any write
invalidates the previous one, and write the rest:

```sh
python3 backfill.py backup --library "/path/to/Calibre Library"
python3 backfill.py write --library "/path/to/Calibre Library" \
  --backup "<path printed by backup>" \
  --write-local-metrics --approve-write --limit 20000
python3 backfill.py verify-library --library "/path/to/Calibre Library"
```

- The write uses Calibre's own API in a single `calibre-debug` process, one call
  per column: about 110,000 values in seconds. Every value is then read back
  through both `calibredb` and SQLite.
- Only works that came back complete are written, and empty values are never
  written.
- `--write-local-metrics` fills `#words`/`#gfog` only where they are blank. Add
  `--replace-local-metrics` to overwrite existing values. Add `--allow-partial`
  if some works are not cached.
- The write refuses to start unless the backup matches the current `metadata.db`
  exactly. Calibre empties its trash once a day whenever a library is opened,
  which counts as a change. If that happens between the backup and the write,
  take a new backup.
- Calibre refreshes each book's `metadata.opf` the next time it opens the
  library, just as it does after any `calibredb` edit.

### 6. Bake the metadata into the EPUBs

Until now the values live only in Calibre's database. `enrich-epubs` writes each
book's complete metadata into its EPUB file, so the file alone carries
everything and can be imported into another Calibre library, or any other
reader, with nothing lost:

- Calibre's own metadata: title, authors, tags, series, rating, publisher,
  publication date, languages, description, and every custom column, including
  ones this project does not manage, such as `#pages`. **Covers are not
  embedded**; they stay in each book folder's `cover.jpg`.
- The AO3 identifier, `ao3:*` fields, and statistics block described in
  [the tutorial](TUTORIAL.md#what-goes-into-each-epub).

As with the write, take a backup and bake one book first:

```sh
python3 backfill.py backup --library "/path/to/Calibre Library"
python3 backfill.py enrich-epubs --library "/path/to/Calibre Library" \
  --backup "<path printed by backup>" \
  --epub-backup-dir ~/.local/share/ao3-calibre-backfill/epub-originals \
  --approve-epub-write --limit 1
```

Check that book, then take a fresh backup and bake the rest with `--limit 20000`.
The run works in four passes:

1. copies every original EPUB into `--epub-backup-dir` (a rerun never overwrites
   a copy);
2. has Calibre write its metadata into the files, through its own API, without
   the cover;
3. adds the AO3 metadata and reads every file back, stopping at the first one
   whose columns differ from the library;
4. has Calibre record the files' new sizes.

- On a 14,800-book library this took about 7½ minutes and added about 1 KB per
  book. The copies of the originals take as much space as the EPUBs themselves.
- It refuses to start if the cache holds AO3 values that Calibre does not have
  yet; run `write` first. Where Calibre has a `#words` or `#gfog` value, that
  value goes into the file exactly; a calculated value is used only for a blank
  cell.
- A stopped run resumes by running the same command again (after a new backup).
- `--no-calibre-metadata` skips pass 2 and adds only the AO3 metadata.
- EPUBs with no AO3 link, and those whose preface links several works (unless
  `--include-ambiguous`), are left alone.

## Every subcommand

Run `python3 backfill.py <command> --help` for the full option list.

| Command | Writes | What it does |
| --- | --- | --- |
| `scan` | a report file | Reads every EPUB in the library and maps it to its AO3 work. Read-only for Calibre. |
| `processes` | nothing | Lists the Calibre processes that would block a write. |
| `backup` | a backup file | Creates and verifies a timestamped `metadata.db` backup, and prints the `--backup` to pass next. |
| `setup-columns` | the library | Creates the ten custom columns and verifies them. Needs `--approve-columns` and a `--backup`. |
| `verify-columns` | nothing | Checks the columns exist with the right types. |
| `calculate-missing-metrics` | the report | Calculates local Words/Gfog only for mapped books whose values are blank. |
| `check-auth` | nothing | Signs in to AO3 and stops, to prove credentials before a long fetch. Needs `--approve-network`. |
| `fetch` | the cache | Fetches AO3 statistics into an append-only JSONL cache, one work at a time. Needs `--approve-network`. |
| `validate-cache` | nothing | Reports how much of the library is cached, with a sample, and how many fetches failed. |
| `preview-refresh` | nothing | Shows which works the next bounded refresh would re-request. |
| `snapshot-cache` | a copy | Copies the cache to a new external path, never overwriting. |
| `write` | the library | Writes the cached values into the custom columns, then reads every value back. Needs `--approve-write` and a matching `--backup`. |
| `verify-library` | nothing | Counts populated values and proves Calibre's numeric searches work. |
| `enrich-epubs` | EPUB files | Bakes Calibre's metadata and AO3's into each EPUB. Needs `--approve-epub-write`, a `--backup` and an `--epub-backup-dir`. |
| `repair-epubs` | EPUB files | Offline repair; see below. |
| `bookorbit-sync` | a server | Publishes the numbers to BookOrbit; see [docs/BOOKORBIT.md](BOOKORBIT.md). |

## repair-epubs

A separate, offline pass over the library's EPUB files. It makes no AO3
requests, reads nothing from Calibre's database, and changes only two things,
each only where the file needs it:

- an OPF written with prefixed element names is rewritten under a default
  namespace, so every reader can open it;
- a work with no rating in its tag list gets one, read from the file's own front
  page.

A file that needs neither is not touched at all. A rating is never guessed: a
file with no front-page rating, or one whose existing rating disagrees with its
front page, is reported and left as it is.

```sh
python3 backfill.py repair-epubs --library "/path/to/Calibre Library" \
  --epub-backup-dir ~/.local/share/ao3-calibre-backfill/epub-originals-repair \
  --dry-run
```

The dry run prints what each file needs and writes nothing:

```text
EPUB repair (dry run)
  EPUBs found               14830
  namespace and rating      120
  namespace only            14322
  rating only               12
  nothing to do             376
  no rating found           8
  keeping their own rating  4
  unreadable                0
  to rewrite now            14454
  originals backed up to    ~/.local/share/ao3-calibre-backfill/epub-originals-repair
```

**At a terminal**, running it without `--dry-run` prints those same counts and
then asks before writing anything. **Off a terminal** it still requires
`--approve-epub-write`, unchanged, so nothing scripted starts rewriting files on
its own. `--limit 1` is a good first step either way.

Every original is copied into `--epub-backup-dir` before it is written, and each
rewritten file is checked afterwards: it must still be a valid EPUB, keep its
manifest, spine, zip entries, Dublin Core metadata, Calibre columns and AO3
metadata exactly, and hold exactly the rating tags expected. The first failure
stops the run with the original backed up.

On a 14,830-book library the dry run took about 20 seconds and the full rewrite
about 4 minutes.

Afterwards, **do not** run Calibre's *Embed metadata* or `enrich-epubs` over
those books unless you mean to: either rewrites the files again from Calibre's
database, which does not hold the rating.

## Common options

| Option | Effect |
| --- | --- |
| `--library PATH` | The Calibre library. A folder with no `metadata.db` is reported before anything else happens. |
| `--cache-dir DIR` | Where the scan report, cache and results live (default `~/.local/share/ao3-calibre-backfill/`). It must be outside the library and outside this repository; `AO3_PROTECTED_PATHS` adds directories of your own. |
| `--report PATH` | Use a scan report from somewhere other than the cache directory. |
| `--limit N` | How many books this run touches. Start at 1. |
| `--include-ambiguous` | Include EPUBs whose preface links more than one work. Off by default: nothing is guessed. |
| `--log-dir DIR` | Where this run's log file goes. |
| `--yes` | Answer any confirmation this command would ask at a terminal. |
| `--calibredb` / `--calibre-debug` | Paths to Calibre's own executables, if they are not on `PATH`. |

---

Built with [Claude Code](https://claude.com/claude-code). The code, tests and
documentation in this repository were written by Claude under human direction
and review.
