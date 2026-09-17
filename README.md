# ao3Archiver

Bulk-download works from Archive of Our Own as EPUBs that arrive in Calibre
with their AO3 statistics already filled in: kudos, hits, bookmarks, comments,
word count, chapters, status, and category, plus a local word count and Gunning
Fog readability score calculated from the file itself.

It started as a substitute for Calibre's FanFicFare plugin, which cannot get
past AO3's Cloudflare protection. For a library that already holds thousands of
AO3 EPUBs, `backfill.py` fills the same columns in place, once, and then bakes
each book's full metadata into its EPUB file.

Runs are built to be left alone: a download of tens of thousands of works takes
days, so a failed work is recorded and skipped, an AO3-wide problem pauses the
run instead of ending it, and rerunning the same command resumes from what is
already on disk.

## What it looks like

```text
18:19:39  ========================================================================
18:19:39  AO3 download
18:19:39    link files          1
18:19:39    link lines          35596
18:19:39    unique works        32525
18:19:39    duplicate lines     3071
18:19:39    unparseable lines   0
18:19:39    already complete    0
18:19:39    unavailable on AO3  0
18:19:39    to download         32524
18:19:39    to enrich only      1
18:19:39    output folder       /path/to/ao3-to-calibre/downloaded
18:19:39    delay               10s between requests
18:19:39    estimated finish    7d12h (about 2026-09-25 07:02)
18:19:39    failure log         ~/.local/share/ao3-calibre-backfill/download-failures.jsonl
18:19:39  ========================================================================
Start this download now? [Y/n]
18:19:41  [    1/32524]   0.0% work=36776911 downloaded kudos=68 hits=1040 words=1315 gfog=7.42 | --/h | elapsed 2s | eta unknown
18:19:59  [    2/32524]   0.0% work=30033096 downloaded kudos=1204 hits=22194 words=48210 gfog=8.91 | 180/h | elapsed 20s | eta 09-25 07:02
```

Every line is also written to a timestamped log file as it happens, so the run
can be started with `nohup` and followed from anywhere. Off a terminal there is
no question to answer: the run starts.

## Quickstart

**Python 3** (developed and tested on 3.14) with `requests`:

```sh
git clone https://github.com/mm0x1/ao3-to-calibre.git && cd ao3-to-calibre
python3 -m venv venv && source venv/bin/activate   # optional
pip install -r requirements.txt
cp .env.example .env        # then fill in your AO3 username and password
```

Then start at the front door, which checks the setup and prints the exact
command for whatever you want to do next:

```sh
python3 archiver.py
```

```text
ao3Archiver
  Downloads Archive of Our Own works as EPUBs with their AO3 statistics baked in:
  kudos, hits, words, a local word count and a readability score, ready to sort on.

Setup
  [ok]  .env             /path/to/ao3-to-calibre/.env
  [ok]  AO3 credentials  from /path/to/ao3-to-calibre/.env
  [  ]  Calibre library  CALIBRE_LIBRARY is not set (only the backfill needs it)
  [  ]  BookOrbit        not configured (optional)
  [ok]  links/           32525 works in 1 file(s)
  [--]  downloaded/      0 EPUB(s)
```

`archiver.py` is purely additive: `download.py` and `backfill.py` behave exactly
as they always have, so nothing scripted around them changes.

The three commands most of it comes down to:

```sh
python3 download.py --dry-run     # the plan and an estimated finish time
python3 download.py               # the run itself; resume by running it again
python3 backfill.py --help        # everything for a library you already have
```

## The guides

| Guide | What it covers |
| --- | --- |
| [docs/TUTORIAL.md](docs/TUTORIAL.md) | The whole thing end to end: harvest links, download, import into Calibre, repair, copy to a server, publish |
| [docs/DOWNLOADING.md](docs/DOWNLOADING.md) | Every `download.py` option, unattended runs, and what happens when a work or AO3 fails |
| [docs/BACKFILL.md](docs/BACKFILL.md) | Every `backfill.py` subcommand, for a Calibre library that already holds AO3 EPUBs |
| [docs/BOOKORBIT.md](docs/BOOKORBIT.md) | Publishing the numbers to a BookOrbit library, and the things its own docs do not cover |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every `.env` key, the SSH alias, and the files kept outside the repository |

## What goes into each EPUB

AO3's EPUB export does not include the work's statistics, so they are read from
the work page and written into the file in three forms: Calibre column values
(`calibre:user_metadata`), which fill the custom columns on import; portable
`ao3:*` metadata and an `ao3` identifier, which travel with the file outside
Calibre; and a visible "AO3 statistics" block in the description. The work's
rating is written as a tag, so Calibre shows it as a Tag and other readers as a
Genre, with nothing to configure.

[docs/TUTORIAL.md](docs/TUTORIAL.md#what-goes-into-each-epub) lists every column
and where its value comes from.

## Project layout

```text
archiver.py                 entry point: the setup check and the menu
download.py                 entry point: download works
backfill.py                 entry point: one-time library backfill
ao3archiver/
  ao3_client.py             everything that talks to AO3: login, request policy,
                            page classification, EPUB downloads, run lock, work queue
  download.py               the download workflow
  backfill.py               the backfill workflow
  bookorbit.py              publishing the numbers to a BookOrbit library
  calibre_library.py        columns, calibredb, backups, the Calibre scripts, verification
  calibre_scripts/
    bulk_write.py           run under calibre-debug, kept apart from the package:
    embed_metadata.py       column writes, and cover-free metadata embedding
  metadata.py               AO3 page statistics and EPUB metadata
  metrics.py                local Words and Gunning Fog
  credentials.py            .env / environment / personal.ini
  prompts.py                confirmations that never block an unattended run
  welcome.py                the front door behind archiver.py
  run_log.py                logging, progress and ETA, interrupts
  common.py                 shared paths, base error, small helpers
docs/                       the guides above
tests/                      one test module per package module, fakes in support.py
```

## Development

```sh
python3 -m pip install pytest
python3 -m pytest
```

The suite runs offline against a fake AO3 and needs no credentials. A few
integration tests also exercise the real `calibre-debug` and `calibredb`, and
are skipped when Calibre is not installed. [CONTRIBUTING.md](CONTRIBUTING.md)
has the house style.

## Being kind to AO3

One page at a time, with a delay between requests (10 seconds by default, never
below 5), `Retry-After` always honoured, and a long back-off when AO3 says it is
struggling. Please keep it that way.

## Licence

[MIT](LICENSE).

---

Built with [Claude Code](https://claude.com/claude-code). The code, tests and
documentation in this repository were written by Claude under human direction
and review.
