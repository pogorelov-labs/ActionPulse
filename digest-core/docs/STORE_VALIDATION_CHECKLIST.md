# Message Store — Corp "Run Inside" Validation Checklist

The encrypted message store is fully exercised **offline** (mocked EWS/MM, a fake
embedder, a temp SQLCipher DB), but per ADR-012 ("code outside, **run inside**,
debug outside") it has **never been proven against the real corp stack**. This
checklist is the bring-back list — run it from **inside the corp network** and
record the results. EP-14 is the model.

> Status: **UNVALIDATED LIVE.** Everything below is a claim until checked here.

## 0. Prerequisites
- [ ] Inside the corp network (EWS + LLM gateway reachable; MM reachable everywhere).
- [ ] `cd digest-core && uv sync --extra store`
  - Linux: pulls the `sqlcipher3-binary` wheel (no build).
  - macOS: builds `sqlcipher3` from source — needs `brew install sqlcipher openssl@3`
    (and, if the build can't find them, `export CPPFLAGS/LDFLAGS` to the brew prefixes).
  - [ ] `uv run python -c "import sqlcipher3, numpy; print(sqlcipher3.dbapi2.sqlite_version)"` works.
- [ ] `actionpulse store init` generated `DIGEST_STORE_KEY` in `~/.config/actionpulse/env`
      (chmod 600). **Record it somewhere safe — losing it makes the store unreadable.**
- [ ] Enable: `store.enabled: true` in `configs/config.yaml` (or `DIGEST_STORE_ENABLED=1`).

## 1. Live ingest populates the store
- [ ] `actionpulse run` (real EWS + MM) completes; exit 0.
- [ ] `actionpulse store stats` → non-zero `messages`, sensible `by_source` (email + mm),
      `chunks` > 0, an oldest…newest window, a plausible DB size.
- [ ] `run_meta` (`trace-*.meta.json`) has a `store` block with inserted/updated/unchanged.
- [ ] **Record:** message/chunk counts, DB size, ingest wall-clock.

## 2. DM-at-rest privacy (guardrail #9) — the critical privacy check
Only if you ingest DMs (`mm_source.dm_scope != off`):
- [ ] Find a DM row: `actionpulse store stats` then inspect — a `D`/`G` message's
      `body_raw`/`body_normalized` MUST be `"[DM content redacted at rest]"`.
- [ ] That DM has **no rows in `chunks`/`embeddings`** and is NOT returned by
      `actionpulse search "<a phrase you know was in the DM>"`.
- [ ] An open/private **channel** post (`O`/`P`) IS kept and searchable.
- [ ] **Record:** confirmed no third-party DM body at rest. (If any DM body leaks → STOP, it's a blocker.)

## 3. Embeddings via the real gateway (bge-m3)
- [ ] `actionpulse store reembed` → `Embedded N chunk(s); 0 still pending`.
- [ ] `store stats` → `embeddings == chunks`.
- [ ] **Record:** the actual vector **dim** (expected 1024 for bge-m3), reembed wall-clock,
      and whether the 15/30 RPM limit caused throttling/429s on a large backlog.
- [ ] Re-run `store reembed` → embeds 0 (idempotent).

## 4. Search quality (the part with no offline proof)
- [ ] `actionpulse search "<term you know exists>" --keyword` returns the right messages (offline-capable).
- [ ] `--semantic` and `--hybrid` return relevant results; eyeball top-5 relevance + provenance.
- [ ] Cyrillic query works (`--keyword` and `--semantic`).
- [ ] An operator query like `actionpulse search "budget AND status"` does NOT crash.
- [ ] `--source mm` / `--since YYYY-MM-DD` filters behave.
- [ ] **Record:** subjective relevance notes (is chunk-size 512 / RRF sensible on real mail?).

## 5. Incremental load + dedup (all sources)
- [ ] Run twice on the same day. Second run: EWS/MM fetch is **narrowed** (watermark used,
      not a full-window re-fetch — check `mm_fetch_stats.watermark_used` / fewer channels scanned).
- [ ] `store stats` message count does not double (dedup + idempotent upsert).
- [ ] **Record:** watermark files in `var/state/` (`ews.syncstate`, `mm.watermark`).

## 6. Encryption at rest
- [ ] The DB file is ciphertext: `grep -a "<a known subject line>" <data home>/var/store/messages.db`
      finds **nothing**.
- [ ] A wrong `DIGEST_STORE_KEY` makes `actionpulse store stats` fail with a clear error
      (not garbage).

## 7. CI / platform
- [ ] The `test-store` CI job is green on the Linux runner (the `sqlcipher3-binary`
      wheel resolved; the ~45 store tests ran, not skipped).

## 8. Retention (optional)
- [ ] With a short `store.ttl_days`, `actionpulse store purge --ttl-days N --yes` deletes
      old rows (cascades to chunks/embeddings/FTS) and reports a count.

---

### Bring back
message/chunk/embedding counts · DB size · **bge-m3 dim** · ingest & reembed timing ·
any 429/rate-limit behavior on first backfill · search-relevance impressions ·
**confirmation that no DM body is at rest** · any dim/contract surprise from the real gateway.
