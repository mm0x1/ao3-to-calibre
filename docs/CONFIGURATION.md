# Configuration

Everything this project reads from outside its own code: the `.env` file, the
environment, the legacy ini file, the SSH alias used to copy files, and the
directory where logs, caches and backups live.

1. [.env](#env)
2. [Where each value can come from](#where-each-value-can-come-from)
3. [Environment variables](#environment-variables)
4. [The SSH alias](#the-ssh-alias)
5. [Files outside the repository](#files-outside-the-repository)
6. [Confirmations and --yes](#confirmations-and---yes)

## .env

Copy `.env.example` to `.env` and fill in what you need. `.env` is ignored by
Git, and no tool here prints, logs or stores its values: credentials are
registered with the logger on load, which redacts them from every later line,
including the ones written to the failure log.

```sh
# AO3, for download.py and backfill.py fetch
AO3_USERNAME=
AO3_PASSWORD=

# Optional: the Calibre library, read only by archiver.py
CALIBRE_LIBRARY=

# Optional: BookOrbit, for backfill.py bookorbit-sync
BOOKORBIT_URL=
BOOKORBIT_USERNAME=
BOOKORBIT_PASSWORD=
# BOOKORBIT_TOKEN=
# BOOKORBIT_LIBRARY_ID=
```

| Key | Needed by | Notes |
| --- | --- | --- |
| `AO3_USERNAME`, `AO3_PASSWORD` | `download.py`, `backfill.py fetch`, `check-auth` | An ordinary AO3 account. Nothing here posts, comments or kudos. |
| `CALIBRE_LIBRARY` | `archiver.py` only | The path to your Calibre library, so the front door can check it and fill it into the commands it prints. Every `backfill.py` command still takes `--library` itself. |
| `BOOKORBIT_URL` | `bookorbit-sync` | A bare host (`myserver:8080`) or a full API root. Empty means BookOrbit is simply not in use. |
| `BOOKORBIT_USERNAME`, `BOOKORBIT_PASSWORD` | `bookorbit-sync` | A dedicated account with **Edit metadata** and **Download**, nothing more. See [docs/BOOKORBIT.md](BOOKORBIT.md#credentials). |
| `BOOKORBIT_TOKEN` | `bookorbit-sync` | An `accessToken` instead of an account. Expires after `JWT_EXPIRES_IN`, 15 minutes by default. |
| `BOOKORBIT_LIBRARY_ID` | `bookorbit-sync` | Restrict the sync to one BookOrbit library. Optional. |

A line must be `KEY=value`. Quotes around a value are stripped, a line whose
first character is `#` is a comment, and any key not listed above is ignored — one file can hold settings
for several tools and each caller only ever sees its own.

## Where each value can come from

In order of precedence:

1. **The process environment.** `AO3_USERNAME=… AO3_PASSWORD=… python3 download.py`
   overrides the file, which is useful for a one-off run or a systemd unit.
2. **`.env`** in the repository root.
3. **`personal.ini`**, the legacy file, for AO3 credentials only:

   ```ini
   [archiveofourown.org]
   username =
   password =
   ```

   `download.py` falls back to it; the backfill does not. Copy it from
   `personal.ini.example` if you already have one from another tool.

Whichever wins, every run logs *which source* supplied the pair — never the
values:

```text
using credentials from /path/to/ao3-to-calibre/.env
```

`python3 archiver.py` prints the same thing as a setup check, and
`backfill.py check-auth --approve-network` proves the pair actually signs in.

## Environment variables

| Variable | Effect |
| --- | --- |
| `AO3_USERNAME`, `AO3_PASSWORD` | Override the credentials from `.env`. |
| `BOOKORBIT_*` | Override the BookOrbit settings from `.env`. |
| `CALIBRE_LIBRARY` | Override the library path `archiver.py` uses. |
| `AO3_PROTECTED_PATHS` | Extra directories a cache, report or backup may never be written inside, separated by `:`. The Calibre library and this repository are always protected. Useful if you keep a sibling clone of the link harvester next door. |

## The SSH alias

Copying files to a reading server is plain `rsync`. Define the server once in
`~/.ssh/config` so no host, user or key ever appears in a command — or in a
shell history, or in anything you paste into a bug report:

```sshconfig
Host myserver
    HostName 192.0.2.10
    User reader
    IdentityFile ~/.ssh/myserver
```

```sh
rsync -rt --stats --dry-run downloaded/ myserver:/path/to/library/fanfiction/
```

[The tutorial](TUTORIAL.md#5-copy-to-a-reading-server-optional) has the variant
that sends a whole Calibre library.

## Files outside the repository

Logs, caches and backups live in `~/.local/share/ao3-calibre-backfill/`, so
nothing personal ends up in the repository:

| Path | Contents |
| --- | --- |
| `logs/` | One timestamped log per run of either tool. |
| `download-failures.jsonl` | Works that failed or are unavailable. Permanent entries (deleted, unrevealed) are skipped by later runs. |
| `scan.json` | The backfill's map of library EPUBs to AO3 works. |
| `ao3-cache.jsonl` | Fetched AO3 statistics. Append-only; the latest record per work wins. |
| `ao3-cache.jsonl.failures.jsonl` | Works the backfill could not fetch. |
| `ao3-cache.jsonl.fetch.lock` | Held by whichever AO3 run is active. |
| `metadata.db.<timestamp>.backup` | Verified `metadata.db` backups, each with a checksum manifest. Never overwritten. |
| `write-result-*.json` | Per-book results of each Calibre write and EPUB bake. |
| `repair-plan-*.json`, `repair-result-*.json` | What `repair-epubs` found, and what it rewrote. |
| `epub-originals/` | The EPUBs as they were before `enrich-epubs`, when passed as `--epub-backup-dir`. |
| `epub-originals-repair/` | The EPUBs as they were before `repair-epubs`, when passed as `--epub-backup-dir`. |

That path is Linux/XDG-shaped and has no macOS or Windows fallback; every
command that uses it takes `--cache-dir`, `--log-dir` or `--epub-backup-dir` to
put it somewhere else.

`links/` and `downloaded/` live in the repository and are both gitignored, along
with `.env`, `personal.ini` and `*.log`.

## Confirmations and `--yes`

Some commands ask a question before they start. Three rules hold everywhere:

1. **A question is asked only at a terminal.** Under `nohup`, cron, a pipe or
   CI, the documented default is taken and the decision is logged instead, so a
   run that is meant to survive for days never blocks on a prompt nobody can see.
2. **It is asked before work starts**, never inside a loop.
3. **`--yes` answers it in advance**, on every command that can ask.

The `--approve-…` flags are unchanged and remain authoritative: a prompt is an
alternative to a flag for someone sitting at a terminal, never a new gate a
script has to satisfy. Two exceptions worth knowing:

- `repair-epubs` off a terminal still requires `--approve-epub-write`, as it
  always has.
- `bookorbit-sync` writes to a server, and off a terminal requires `--yes`.
  `--dry-run` needs nothing.

---

Built with [Claude Code](https://claude.com/claude-code). The code, tests and
documentation in this repository were written by Claude under human direction
and review.
