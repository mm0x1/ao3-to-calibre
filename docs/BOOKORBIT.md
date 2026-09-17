# Publishing to BookOrbit

Optional. [BookOrbit](https://github.com/bookorbit/bookorbit) is a self-hosted
library server; this page is for anyone who reads there rather than in Calibre.
Nothing else in this project touches it, and without `BOOKORBIT_URL` the sync
says so and exits cleanly.

1. [What the file carries and what it cannot](#what-the-file-carries-and-what-it-cannot)
2. [One-time setup: the custom fields](#one-time-setup-the-custom-fields)
3. [Credentials](#credentials)
4. [Running the sync](#running-the-sync)
5. [What BookOrbit's own docs do not say](#what-bookorbits-own-docs-do-not-say)

## What the file carries and what it cannot

BookOrbit's scanner reads titles, authors, tags and genres out of each file, so
those arrive with the file itself — **including the AO3 rating**, which this
project writes as a tag and BookOrbit shows as a Genre, filterable with nothing
to configure.

Numbers are different. BookOrbit can sort and filter by a custom field, but its
scanner never reads custom-field values out of a file, so kudos, hits and word
counts have to be sent to its API once per book. That is all `bookorbit-sync`
does, reading every value from the EPUBs themselves.

## One-time setup: the custom fields

In BookOrbit, as an admin, go to **Settings → Metadata → Custom Metadata** and
add the fields you want, with these exact labels:

| Label | Type | Also accepted |
| --- | --- | --- |
| AO3 Kudos | Number | |
| AO3 Hits | Number | |
| AO3 Comments | Number | |
| AO3 Bookmarks | Number | |
| AO3 Words | Number | |
| AO3 Chapters | Text | |
| AO3 Rating | Text | |
| AO3 Category | Text | |
| AO3 Status | Text | |
| AO3 Published | Date | |
| AO3 Updated | Date | |
| Local Words | Number | `Words`, `Local Word Count` |
| Gunning Fog | Number | `Gfog`, `Gunning Fog Index` |
| AO3 Link | URL | |

Create only the ones you care about. The sync lists the rest, asks at a terminal
whether to publish the ones that do exist, and carries on.

The label is what the sync matches on, so type it exactly, give or take the
alternatives in the last column — naming a field after this project's Calibre
column (`Words`, `Gfog`) works just as well. A field the sync does not recognise
is never touched, so your own fields are safe.

Enable each field for the library holding the fanfic. If a field turns out to be
the wrong type — a `text` field will not sort numerically — the sync says so and
asks before writing to it.

**Dates** must be `YYYY-MM-DD`. AO3 words some completion dates differently, and
those are left empty rather than sent in a form BookOrbit rejects.

## Credentials

BookOrbit has no API keys or personal access tokens: the only way in is a
sign-in that mints a short-lived token. So `.env` takes either a token you
already have, or an account to sign in as:

```sh
BOOKORBIT_URL=http://myserver:8080
BOOKORBIT_USERNAME=archiver
BOOKORBIT_PASSWORD=...
# or, instead of the username and password:
BOOKORBIT_TOKEN=...
BOOKORBIT_LIBRARY_ID=2      # optional: only this library
```

Use a dedicated account, not your own: in **Settings → Admin → Users**, create
one with just **Edit metadata** (`library_edit_metadata`) and **Download**
(`library_download`), and give it access only to that library. Those are the two
permissions the sync needs — to write the values, and to read the list of books
and their current values.

`BOOKORBIT_TOKEN` is the `accessToken` a sign-in returns. By default it expires
15 minutes later, which is why a username and password are easier: the sync
mints its own token and renews it before it ages out, mid-run if the run is long.
A token is worth using only if your server sets a long `JWT_EXPIRES_IN`.

Sign-ins are rate-limited to five a minute, and the sync says so plainly if it
hits that. Neither the password nor the token ever appears in a log line, an
error message, or a URL.

## Running the sync

Scan the library in BookOrbit first, so every book exists and has an id, then:

```sh
python3 backfill.py bookorbit-sync --library downloaded --dry-run
python3 backfill.py bookorbit-sync --library downloaded
```

Point `--library` at whichever folder holds the EPUBs you sent, `downloaded/` or
a Calibre library. The dry run writes nothing and reports what it would do, and
needs no approval of any kind.

The real run **writes to a server**, so it is gated like every other writing
command here: at a terminal it asks first; off a terminal it needs `--yes`.

```text
books updated: 14816
already up to date: 0
not found in BookOrbit: 3
custom fields not created in BookOrbit: AO3 Published, AO3 Updated
```

- Books are matched to the server's copies by **folder and filename**, so local
  and remote paths do not have to agree. A flat folder such as `downloaded/`
  matches on the filename alone. Anything that cannot be matched unambiguously
  is listed and skipped, never guessed.
- **Values already correct are left alone**, so a second run sends nothing.
- One book is one request, so stopping mid-run always leaves a book either fully
  written or untouched, and rerunning carries on from there.
- Ten failed writes in a row stop the run: that is a permission or the wrong
  library, not one odd book.
- `--limit N` bounds a first run. `--bookorbit-library-id N` looks at one library
  rather than every library the account can see.

## What BookOrbit's own docs do not say

Three things worth knowing, all of them the reason this command exists:

- **A scan never imports custom values, and never clears them.** Custom values
  live in their own table, which the scanner does not write, so a value set once
  survives every later scan — and no scan will ever fill one in for you.
- **The rating rides along as a Genre.** It is the one AO3 value that needs none
  of this, because it is a tag on the file.
- **An EPUB whose OPF uses prefixed element names renders as an empty book** in
  BookOrbit's reader, even though Calibre opens it. That is what
  [`repair-epubs`](BACKFILL.md#repair-epubs) fixes, and it is worth running
  before wondering why a book looks blank.

---

Built with [Claude Code](https://claude.com/claude-code). The code, tests and
documentation in this repository were written by Claude under human direction
and review.
