# Downloading

Everything `download.py` does, every option it takes, and what it does when
something goes wrong.

```sh
python3 download.py --dry-run     # the plan; no requests, no credentials needed
python3 download.py               # the run; rerun the same command to resume
```

It reads every `*.txt` file in `links/`, keeps the first link to each work, and
for each one: fetches the AO3 work page, downloads the EPUB from the page's own
Download link, calculates local Words and Gunning Fog from the file, writes the
AO3 and Calibre metadata into the EPUB, and reads it back to confirm Calibre
will see it.

## Options

| Option | Effect |
| --- | --- |
| `--dry-run` | Show the plan and estimated finish time; make no requests. |
| `--limit N` | Process at most N works. Useful for a first run. |
| `--delay SECONDS` | Seconds between AO3 requests (default 10, minimum 5). A new download costs two paced requests, an enrich-only pass one. |
| `--links DIR` / `--output DIR` | Read links from, and save EPUBs to, somewhere other than `links/` and `downloaded/`. |
| `--metadata-only` | Enrich EPUBs already on disk; download nothing. |
| `--refresh-metadata` | Re-read AO3 statistics even for EPUBs that are already complete. |
| `--retry-unavailable` | Include works previously found deleted or unrevealed. |
| `--failure-log PATH` | Where failed and unavailable works are recorded. |
| `--log-dir DIR` | Where this run's log file goes. |
| `--yes` | Answer the "start this download now?" question in advance. |

## The plan, before anything happens

Every run — dry or real — prints what it is about to do and stops there if
nothing needs doing:

```text
AO3 download
  link files          1
  link lines          35596
  unique works        32525
  duplicate lines     3071
  unparseable lines   0
  already complete    0
  unavailable on AO3  0
  to download         32524
  to enrich only      1
  output folder       /path/to/ao3-to-calibre/downloaded
  delay               10s between requests
  estimated finish    7d12h (about 2026-09-25 07:02)
  failure log         ~/.local/share/ao3-calibre-backfill/download-failures.jsonl
```

*already complete* is a file that is a valid EPUB, carries `ao3:*` metadata, and
carries Calibre column values. A file that was enriched by an older downloader
has the first two and not the third, so it is enriched again rather than
trusted. A missing, truncated or corrupt file is downloaded again.

**At a terminal**, the real run then asks `Start this download now? [Y/n]` once,
after the plan and before the first request. Answering no makes no requests at
all. **Off a terminal** — `nohup`, cron, a pipe — there is nobody to ask, so the
run starts, exactly as it always did. `--yes` answers in advance.

## Leaving it running

A download of tens of thousands of works takes days, so the script is built to
run unattended:

- **A work that fails** is recorded and skipped. Failed works get one more try at
  the end of the run, and the next run tries them again.
- **A problem that affects every work** — a Cloudflare challenge, repeated rate
  limiting, a dropped login, or five failures in a row — pauses the run for 5,
  15, 30, then 60 minutes, signs in again, and carries on. AO3's `Retry-After`
  is always honoured, and a passing AO3 error (HTTP 5xx, including Cloudflare's
  525) is simply retried.
- **Rejected credentials stop the run**, since retrying them cannot succeed.
- **Only one AO3 run can use the account at a time.** `download.py` refuses to
  start while a backfill fetch or another download is running.

```sh
nohup python3 download.py &
tail -f "$(ls -t ~/.local/share/ao3-calibre-backfill/logs/download-*.log | head -1)"
```

Press Ctrl-C (or `kill` the process) to stop at the next safe point; the run
prints a summary and exits. Run the same command again to resume: it re-plans
from what is on disk, so nothing is fetched twice.

## Progress

Each completed work is one line, and every 25 works a short summary:

```text
[ 1240/32524]   3.8% work=36776911 downloaded kudos=68 hits=1040 words=1315 gfog=7.42 | 179/h | elapsed 6h55m | eta 09-18 04:12
--- progress: 1240/32524 works ---
    downloaded 1238 · unavailable 2
    179.0 works/h · elapsed 6h55m · remaining 31284 · eta 2026-09-25 07:02
```

The rate and ETA are measured from this run, not assumed from the delay, so a
cool-down is reflected in them.

## When a work fails

Failures are appended to `~/.local/share/ao3-calibre-backfill/download-failures.jsonl`,
one JSON object per line, with the work id and URL, the stage it failed at, the
error, and whether it is permanent:

```json
{"error": "HTTP 404", "error_type": "Unavailable", "failed_at": "2026-09-17T18:21:22+00:00", "permanent": true, "stage": "page", "work_id": "111", "work_url": "https://archiveofourown.org/works/111"}
```

**Permanent** means AO3 reports the work as gone or hidden — deleted, or an
unrevealed Mystery Work in a challenge collection. Those are skipped by later
runs unless you pass `--retry-unavailable`. Everything else is retried
automatically: under this request policy a failure is far more likely to be a
passing problem than a lost work.

Credentials are scrubbed from anything written to that file, as they are from
every log line.

## Files that are not EPUBs

A downloaded file is validated before it replaces anything: the bytes are
written to a temporary file in the destination folder, checked as an EPUB, and
only then moved into place. A failed download therefore leaves nothing behind,
and never overwrites a good file with a truncated one.

## Rating tags

The rating is written as a tag rather than a column. AO3's own export usually
carries it; when it does not, it is taken from the work page that was fetched
anyway, or from the EPUB's own front page. A work whose rating cannot be found
anywhere is still saved — the file is worth more than the tag — with a warning
and a count in the run summary.

---

Built with [Claude Code](https://claude.com/claude-code). The code, tests and
documentation in this repository were written by Claude under human direction
and review.
