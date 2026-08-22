# Title

SmtpSender never validates the EHLO hostname smtplib picks for it, and Gmail rejects the corrupted result

# Body

## Gateway change

`SmtpSender._session()` (`glc/channels/catalogue/imap/smtp_sender.py`) opens
`smtplib.SMTP(self.host, self.port, timeout=30)` with no `local_hostname`. smtplib then derives
one itself from `socket.getfqdn()` and uses it directly whenever the result contains a `.` —
with **zero validation of its actual character content**. On Windows, `socket.getfqdn()` can
return a name with embedded control characters (observed live:
`'LAPTOP-TGJ7B0SF.\x08\x08\x04\x04'`). Gmail rejects an EHLO carrying that with a **501**, which
also means STARTTLS is never advertised — so the very next call fails as
`SMTPNotSupportedError: STARTTLS extension not supported by server`, an error that points at the
server rather than at the name that was actually sent to it. No test file imported `SmtpSender`
before this change; the entire SMTP transport path was untested.

This fix was already written and verified live against Gmail (`ehlo: 250`, `starttls: OK`) on an
earlier branch, `part1-channel-bridges` (`0066ed4`) — but that commit bundles 8 unrelated files
(`+506/−6`). Re-authored here off current `main` as a single-purpose change touching only this
one file.

## Reproduction and result

```
$ uv run pytest tests/test_smtp_ehlo_hostname.py -q   # before any fix
AttributeError: module 'glc.channels.catalogue.imap.smtp_sender' has no attribute 'socket'
5 failed in 6.21s
```

While closing the fix out, tracing exactly what smtplib's own default does for a dotless
hostname (see "The fix" below) surfaced one more gap, so 2 more tests were added and the same
before/after cycle was repeated for them:

```
$ uv run pytest tests/test_smtp_ehlo_hostname.py -q   # fix applied, gap not yet closed
AssertionError: assert 'labhost' == '[192.168.1.5]'
2 failed, 5 passed in 1.09s

$ uv run pytest tests/test_smtp_ehlo_hostname.py -q   # gap closed
7 passed in 0.80s
```

## The fix

One file, `glc/channels/catalogue/imap/smtp_sender.py`:

- New `_ehlo_name()`, validating `socket.getfqdn()` against a strict `^[A-Za-z0-9.-]+$` pattern:
  - **Valid, with a domain suffix** → used as-is — identical to what smtplib's own default
    already does correctly for a normal FQDN.
  - **Valid, no domain suffix** → promoted to a bracketed IP address-literal (`[x.x.x.x]`, RFC
    5321 §4.1.3), matching smtplib's *own* existing fallback for exactly this case (it checks
    `'.' in fqdn` internally) rather than sending a bare name — falling back to `[127.0.0.1]` if
    the resolution itself fails, again matching smtplib's own default.
  - **Invalid** (empty, a raised exception, or — the actual bug — fails the safety pattern) →
    falls back to `"localhost"`, the value already proven live against Gmail.
- `_session()` now passes `local_hostname=_ehlo_name()` explicitly, instead of letting smtplib
  compute an unvalidated default itself.

This is deliberately a **strict superset** of smtplib's existing behaviour, not a simplification
of it: the common case (a clean FQDN) is untouched, the dotless case is replicated rather than
traded away, and only the genuinely corrupted case gets a new, safe fallback. Confirmed this
matters for every platform, not only Windows — `socket.getfqdn()` returning a dotless hostname
(no configured domain suffix) is a realistic case on minimal Linux containers too.

## Tests

7 new tests in a new `tests/test_smtp_ehlo_hostname.py` (no prior test file imported
`SmtpSender`): a clean FQDN passthrough; the exact corrupted string observed live on Windows,
falling back to `localhost`; empty-string and raised-exception fallbacks; a dotless-but-safe
hostname promoted to an IP literal; that promotion itself falling back to the loopback literal
if resolution fails; and that `_session()` actually wires the computed name into the
`smtplib.SMTP(...)` call, not just that `_ehlo_name()` computes one correctly in isolation.

## Boundary checklist

- [x] Provider credentials remain inside `glc_v3` — no change to how any credential is held;
      this only changes the EHLO announcement, never touches `user`/`password`.
- [x] No agent graph, memory, semantic-indexing, or A2A runtime was added here.
- [x] Existing `/v1/*` callers remain compatible — this is internal to `SmtpSender`, no API
      surface changed.
- [x] No `.env`, credentials, local databases, audit records, or pairing state are committed.
- [x] `uv run ruff check .` passes on the touched files.
- [x] `uv run pytest -q` — 541 passed, 2 pre-existing failures unrelated to this change
      (`test_channel_setup.py::test_secret_save_never_round_trips_and_requires_restart` —
      Windows POSIX permission bits — and
      `voice/tts/test_system_fallback.py::test_synthesize_handles_empty_text`), identical to the
      pair already cited as pre-existing and unrelated on PR #29/#30. 1 skipped. Zero new
      failures.
