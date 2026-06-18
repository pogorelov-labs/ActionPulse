#!/usr/bin/env bash
#
# Mattermost PAT self-test — NON-DANGEROUS capability probe for ActionPulse.
#
# Verifies, without disturbing anyone else, that a Personal Access Token can:
#   0. authenticate                 (GET /users/me)
#   1. read the owner's channels    (counts by type only — never message content)
#   2. read posts WITHOUT marking them read   (the key claim: GET is non-mutating)
#   3. read reactions back          (the EP-15 calibration data path)
#   4. deliver to the owner's own self-DM   (+ confirm @-escaping)
#   5. clean up its own test posts
#
# SAFETY: read-only, plus writes ONLY to your self-DM ([me, me]). It never reads
# or posts to any other person/channel, never opens a websocket, and never calls
# ViewChannel (which would clear "Unread"). Self-DM @mentions reach only you.
# Every API call is checked: a non-2xx response prints the Mattermost error and
# STOPS the script — it never proceeds on a failed call.
#
# RUN FROM INSIDE THE CORPORATE NETWORK. The corp edge proxy returns 403 to
# token-authenticated API calls from outside (verified 2026-06-17), so this must
# run where the authenticated REST API is reachable.
#
# Secrets come from the environment — never hard-code them:
#   export MM_BASE=https://<your-mattermost-host>
#   export MM_PAT=<personal-access-token>
#   bash scripts/mm_pat_selftest.sh
#
set -uo pipefail
: "${MM_BASE:?set MM_BASE to your Mattermost base URL}"
: "${MM_PAT:?set MM_PAT to your Personal Access Token}"

AUTH="Authorization: Bearer ${MM_PAT}"
say() { printf '\n=== %s ===\n' "$1"; }

# api METHOD PATH [JSON_BODY]
#   -> fills global BODY (response text) and CODE (http status).
#   -> on a non-2xx response, prints the Mattermost error id/message and EXITS,
#      so the script never proceeds on a failed call. (The previous version used
#      `val id` which silently captured an error response's "id" field as if it
#      were a real channel/post id, producing false-positive "successes".)
BODY=""; CODE=""
api() {
  local method="$1" path="$2" data="${3:-}" tmp; tmp="$(mktemp)"
  if [ -n "$data" ]; then
    CODE=$(curl -sS -m 20 -H "$AUTH" -X "$method" -d "$data" -o "$tmp" -w '%{http_code}' "$MM_BASE$path")
  else
    CODE=$(curl -sS -m 20 -H "$AUTH" -X "$method" -o "$tmp" -w '%{http_code}' "$MM_BASE$path")
  fi
  BODY="$(cat "$tmp")"; rm -f "$tmp"
  case "$CODE" in
    2*) return 0 ;;
    *)
      echo "STOP: $method $path -> HTTP $CODE"
      printf '%s' "$BODY" | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); print('  Mattermost error:', d.get('id'), '|', d.get('message'))
except Exception:
    print('  (non-JSON body — likely an edge proxy/WAF page; are you inside corp?)')" 2>/dev/null
      exit 1 ;;
  esac
}
# jget KEY  -> top-level scalar from the last BODY (NB: never name a var UID — it is a readonly bash builtin)
jget() { printf '%s' "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }

say "0  liveness — GET /users/me"
api GET /api/v4/users/me
ME_ID="$(jget id)"; ME_USER="$(jget username)"
[ -n "$ME_ID" ] || { echo "STOP: authenticated but no user id in response."; exit 1; }
echo "me: ${ME_ID:0:8}…  user=$ME_USER  roles=$(jget roles)"

say "1  read scope — counts only, no content"
api GET /api/v4/users/me/teams
TID="$(printf '%s' "$BODY" | python3 -c "import sys,json;t=json.load(sys.stdin);print(t[0]['id'] if t else '')")"
if [ -n "$TID" ]; then
  api GET "/api/v4/users/me/teams/$TID/channels"
  printf '%s' "$BODY" | python3 -c "import sys,json,collections;print('channels by type:',dict(collections.Counter(c['type'] for c in json.load(sys.stdin))))"
fi

say "2  UNREAD non-mutation proof (self-DM)"
api POST /api/v4/channels/direct "[\"$ME_ID\",\"$ME_ID\"]"
DM="$(jget id)"; echo "self-DM channel: ${DM:0:8}…"
api POST /api/v4/posts "{\"channel_id\":\"$DM\",\"message\":\"ActionPulse self-test (safe to delete)\"}"
PID="$(jget id)"
api GET "/api/v4/channels/$DM/members/me"; BEFORE="$(jget last_viewed_at)"
api GET "/api/v4/channels/$DM/posts"   # <-- the GET under test (body ignored)
api GET "/api/v4/channels/$DM/members/me"; AFTER="$(jget last_viewed_at)"
if [ "$BEFORE" = "$AFTER" ]; then
  echo "last_viewed_at $BEFORE unchanged after GET  ->  GET does NOT mark read ✅"
else
  echo "last_viewed_at moved $BEFORE -> $AFTER after GET  ->  ⚠ investigate"
fi

say "3  reactions read-back (EP-15 data path)"
api POST /api/v4/reactions "{\"user_id\":\"$ME_ID\",\"post_id\":\"$PID\",\"emoji_name\":\"thumbsup\"}"
api GET "/api/v4/posts/$PID/reactions"
printf '%s' "$BODY" | python3 -c "import sys,json;r=json.load(sys.stdin);print('reactions read back:', [x.get('emoji_name') for x in r] if isinstance(r,list) else r)"

say "4  self-DM delivery + @-escape (pings only you)"
# Build the body in Python so the backtick code-span survives bash quoting intact.
MSG="$(DM="$DM" U="$ME_USER" python3 -c 'import json,os;print(json.dumps({"channel_id":os.environ["DM"],"message":"escaped:`@nobody` literal-self:@"+os.environ["U"]}))')"
api POST /api/v4/posts "$MSG"; PID2="$(jget id)"
echo "posted ${PID2:0:8}… — open your self-DM: \`@nobody\` should render as code (no mention); @$ME_USER as a real mention."

say "5  cleanup — delete test posts (best-effort, never fatal)"
for p in "$PID2" "$PID"; do
  [ -n "$p" ] || continue
  c=$(curl -sS -m 20 -H "$AUTH" -X DELETE -o /dev/null -w '%{http_code}' "$MM_BASE/api/v4/posts/$p")
  echo "deleted ${p:0:8}… -> $c"
done
echo "done — the self-DM channel remains (your own notes-to-self); test posts removed."
