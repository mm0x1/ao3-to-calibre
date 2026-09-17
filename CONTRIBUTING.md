# Contributing

This is a personal project, published because it might be useful to someone
else with a large Archive of Our Own library. Issues and pull requests are
welcome, and so is a bug report that goes no further than "this happened".
Nobody is on call for it.

## Running the tests

```sh
python3 -m pip install pytest
python3 -m pytest
```

The suite runs offline against a fake AO3: it makes no network requests and
needs no credentials. A few integration tests drive the real `calibre-debug`
and `calibredb`, and skip themselves when Calibre is not installed.

Please run the whole suite before opening a pull request.

## House style

- **Small, focused changes.** One logical change per commit, and a pull request
  that can be read in one sitting.
- **Tests alongside the code.** One test module per package module, functional
  rather than mock-heavy: the fakes in `tests/support.py` stand in for AO3, for
  EPUBs and for a scan report, so a test can exercise a real code path.
- **Say why in a comment, not what.** The code says what it does.
- **Nothing personal in the repository.** No real paths, hostnames, addresses or
  library contents, in code, tests or documentation. Logs, caches and backups
  live in `~/.local/share/ao3-calibre-backfill/` for that reason.
- **Do not break an unattended run.** The download and the fetch are started
  with `nohup` and expected to survive for days. A new confirmation is asked
  only at a terminal, only before work starts, and is skippable with `--yes`;
  see `ao3archiver/prompts.py`.
- **Approval flags stay authoritative.** Anything that writes to a Calibre
  library, an EPUB file or a server needs its explicit `--approve-…` flag or
  `--yes`, and a `--dry-run` that shows what it would do first.

## Being kind to AO3

This project fetches one page at a time, with a delay between requests, and
backs off when AO3 says to. Please do not send a change that raises the request
rate, removes a delay, or works around a rate limit.

---

Built with [Claude Code](https://claude.com/claude-code). The code, tests and
documentation in this repository were written by Claude under human direction
and review.
