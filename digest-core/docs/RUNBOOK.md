# ActionPulse — Production Runbook

How to run and operate the daily digest **in production**, reflecting the
multi-agent redesign (PR1–PR12, merged 2026-06). For first-time install/config
mechanics see [`DEPLOYMENT.md`](DEPLOYMENT.md); for the one-time corp dog-fooding
walkthrough see [`CORP_SESSION_RUNBOOK.md`](CORP_SESSION_RUNBOOK.md); for the
architecture/roadmap see [`ARCHITECTURE.md`](ARCHITECTURE.md) and
[`REDESIGN_PLAN.md`](REDESIGN_PLAN.md).

## What this deployment runs today

| Precondition | Status | Effect |
|---|---|---|
| **PC-1** service-account role | ✅ **Personal** | Extractor = `qwen35-397b-a17b` @ **15 RPM** — already the config default, no change |
| **PC-2** per-endpoint data-handling ADR | ⏳ unresolved | The **fleet stays off** (reranker / embeddings / judge) |

The pipeline runs in the plan's **Conservative variant**: deterministic IDs,
content-aware idempotency, per-session TLS, per-stage degradation, traceable
Mattermost delivery, verbatim evidence spans, and the **P2 citation gate in shadow
mode**. The fleet enhancements are built but **off by default and not wired** — no
action needed.

## 1. Three hard preconditions

1. **Run on a host inside the corp network.** EWS *and* the LLM gateway are
   corp-only; Mattermost is reachable anywhere. (CI VPS runners are *not* a valid
   host for the digest.)
2. **The corp CA must be trusted for the LLM gateway *and* Mattermost.** Since PR3,
   `ews.verify_ssl=false` no longer leaks `verify=False` into the gateway/MM httpx
   clients — they verify independently. Point `ews.verify_ca` at the corp CA (or
   install it in the system trust store), or those calls fail. The setup wizard
   auto-detects a CA.
3. **Secrets via ENV only** (never in YAML): `EWS_PASSWORD`, `LLM_TOKEN`,
   `MM_WEBHOOK_URL`. The wizard writes them to `~/.config/actionpulse/env`.

## 2. Deploy from `main`

```bash
cd ~/ActionPulse && git fetch origin && git checkout main && git pull
cd digest-core && make setup     # uv sync --native-tls + interactive wizard
```

`make setup` writes `~/.config/actionpulse/env` and `configs/config.yaml`. Confirm
in `configs/config.yaml`:

- `llm.model: qwen35-397b-a17b` (✅ Personal extractor — **do not change**)
- `llm.endpoint`: must point at the gateway's **OpenAI front** —
  `https://<gateway-host>/v1/chat/completions` (per the CIB endpoint reference:
  official API doc + live probe 2026-06-09). Older configs carry a legacy
  `/api/v1/chat` path — **unverified**; if a run fails at the LLM stage (404),
  check this first. See
  [`CORP_VALIDATION_FINDINGS_2026-06.md`](CORP_VALIDATION_FINDINGS_2026-06.md) F-04.
- `llm.max_output_tokens: 6000`, `llm.temperature: 0.0` — defaults are correct (a
  real prod day measured 5,226 output tokens). Raise the cap only up to the gateway
  ceiling (16384) if a digest degrades with an "output truncated" banner.
- `ews.endpoint / user_upn / user_login / user_domain / user_aliases / verify_ca`
- `ews.folders` (default `["Inbox"]`), `time.user_timezone`, `time.window`
- `deliver.mattermost.enabled: true`, `webhook_url_env: "MM_WEBHOOK_URL"`

## 3. Verify before the first real run

Load secrets first — a manual shell does **not** auto-read the wizard's env file
(only the systemd unit does, via `EnvironmentFile`); without this, `run` fails with
exit 1 `Environment variable EWS_PASSWORD not set` (seen in the 2026-06-10 corp run):

```bash
set -a; source ~/.config/actionpulse/env; set +a
```

```bash
python -m digest_core.cli diagnose       # env + CA + tools
python -m digest_core.cli mm-ping        # MM webhook reachable + its TLS (works anywhere)
python -m digest_core.cli run --dry-run  # ingest+normalize only: proves EWS + corp CA (no LLM/MM)
```

`--dry-run` exit **0** ⇒ EWS, CA, and connectivity are good. Exit **1** ⇒ a real
misconfig (bad CA path, creds, endpoint) — fix it. A misconfigured run now **fails
loud**; it does *not* silently produce an empty digest.

## 4. First real run (manual, observe)

```bash
python -m digest_core.cli run --from-date today --sources ews ; echo "exit=$?"
```

Check:

- **Artifacts** in `out/`: `digest-YYYY-MM-DD.json`, `.md`, the
  `digest-YYYY-MM-DD.idem.json` sidecar, and `trace-<id>.meta.json`
  (`support_recall`, `items_weak`, `citation_validation_ok`).
- **Mattermost message** carries per-item **`↳ ev: <id> | [json](…#<id>)`** and a
  `⚠ слабое обоснование` badge on weakly-supported items — the P2 win, live.

Re-running the same day is a **no-op** (idempotency skips it). Use `--force` to
rebuild.

## 5. Schedule (systemd timer — canonical)

Templated units ship in [`deploy/`](../deploy). As the runtime user:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/actionpulse-digest.service ~/.config/systemd/user/actionpulse-digest@.service
cp deploy/actionpulse-digest.timer   ~/.config/systemd/user/actionpulse-digest.timer
systemctl --user daemon-reload
systemctl --user enable --now actionpulse-digest.timer
loginctl enable-linger "$USER"      # run even when logged out
```

- Daily **08:00**, `Persistent=true` (catches a missed day), `RandomizedDelaySec=120`.
- Runs `cli run --from-date today --sources ews` (no `--validate-citations` flag ⇒
  **default-on** gate at `recall_floor=0.0` ⇒ exit-neutral). Secrets from the
  `EnvironmentFile`.
- Manual trigger: `systemctl --user start actionpulse-digest@$USER.service`
- Logs: `journalctl --user -u 'actionpulse-digest@*' -f`

## 6. Operate

- **Exit codes:** `0` success · `1` error / misconfig / crash · `2` citation gate —
  fires **only** when `support_recall < reranker.recall_floor` *and*
  `--validate-citations` is on. The floor defaults to `0.0`, so **exit 2 cannot fire
  yet** (see §8). A single bad citation or a weak item never trips it.
- **Idempotency:** a run skips unless `config_sha256 + content_sha256 +
  pipeline_version` changed (state in the `.idem.json` sidecar). `--force` bypasses.
- **Rate limit:** extractor is 15 RPM (Personal); a daily run makes ≤2 LLM calls —
  far under cap; the RateBroker paces it. Nothing to tune.
- **Logs:** structured JSON (structlog), emails redacted. Watch `status: partial`
  (a stage degraded) and `items_weak`.
- **Metrics:** Prometheus on `:9108`, health on `:9109` — but the run is `oneshot`,
  so the server is up only *during* the run. For a batch job rely on
  `trace-*.meta.json` + journald, or add a textfile/pushgateway exporter. New gate
  metrics: `citation_support_score_histogram`, `citation_weak_evidence_total`,
  `reranker_calls_total` (reranker stays 0 — off).
- **Diagnostics bundle:** `python -m digest_core.cli export-diagnostics --trace-id <id> --send-mm`

## 7. What's ON vs OFF

**ON (no PC-2 needed):** deterministic IDs · content-aware idempotency · per-session
TLS · per-stage degradation · traceable MM delivery · **verbatim evidence spans**
(the prompt requires them; items lacking a verifiable span are kept and badged,
never dropped) · **shadow P2 gate** (offset-fidelity + `weak_evidence`) ·
`--validate-citations` default-on at floor `0.0`.

**OFF (until PC-2 + run.py wiring):** reranker support scores · embedding
relevance/threading · LLM-judge repair. Off by default and unwired — **no action**.

## 8. Turning P2 from shadow → enforcing (later; no PC-2 required)

1. Let the daily job run for a few weeks (shadow gate annotating).
2. Export 👍/👎 Mattermost reactions to a JSONL, then:
   ```bash
   python -m digest_core.cli eval-gold --reactions reactions.jsonl
   python -m digest_core.cli eval-calibrate --scored scored.jsonl --out calibration.json
   ```
3. When `gates_p8` is true, raise `reranker.recall_floor` above `0.0` in
   `configs/config.yaml`. A recall drop now exits **2** — a real quality gate.

The fleet wave (reranker / embeddings / judge) is a separate activation gated by
**PC-2** *and* the missing `run.py` wiring; see `REDESIGN_PLAN.md` §2.9–2.13.

## 9. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Exit `1`, `FileNotFoundError` on `verify_ca` | bad/absent corp CA path | fix `ews.verify_ca` or install the CA; re-run `diagnose` |
| Exit `1` on a scheduled run, no digest | EWS creds/endpoint or gateway/MM cert not CA-trusted | check `~/.config/actionpulse/env` + §1.2 |
| `status: partial`, `Статус` banner in the digest | a stage degraded (threads/evidence/select) or LLM failed | inspect `trace-*.meta.json.degraded_stages` and journald |
| Empty digest, exit `0` | genuinely no actionable mail, **or** EWS unreachable (operational → degrades) | confirm via `--dry-run`; a *config* error would have crashed |
| MM message missing `↳ ev:` lines | item has no verifiable span (badged) or is a system/status item | expected; check `items_weak` |
| Replayed digest empty | pre-PR1 recording (stale uuid IDs) | re-record `--record-llm` inside corp |
