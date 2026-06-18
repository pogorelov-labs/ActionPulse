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

A=(-H "Authorization: Bearer ${MM_PAT}")
val() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }
say() { printf '\n=== %s ===\n' "$1"; }

say "0  liveness — GET /users/me"
ME=$(curl -sS -m20 "${A[@]}" "$MM_BASE/api/v4/users/me") \
  || { echo "STOP: request failed (server reachable? running from inside corp?)"; exit 1; }
UID=$(printf '%s' "$ME" | val id)
[ -n "$UID" ] || { echo "STOP: no user id — auth failed (token invalid/disabled, or edge-proxy-blocked). Aborting."; exit 1; }
echo "me: ${UID:0:6}…  user=$(printf '%s' "$ME" | val username)  roles=$(printf '%s' "$ME" | val roles)"

say "1  read scope — counts only, no content"
TID=$(curl -sS -m20 "${A[@]}" "$MM_BASE/api/v4/users/me/teams" \
  | python3 -c "import sys,json;t=json.load(sys.stdin);print(t[0]['id'] if t else '')")
if [ -n "$TID" ]; then
  curl -sS -m20 "${A[@]}" "$MM_BASE/api/v4/users/me/teams/$TID/channels" \
    | python3 -c "import sys,json,collections;print('channels by type:',dict(collections.Counter(c['type'] for c in json.load(sys.stdin))))"
fi

say "2  UNREAD non-mutation proof (self-DM)"
DM=$(curl -sS -m20 "${A[@]}" -d "[\"$UID\",\"$UID\"]" "$MM_BASE/api/v4/channels/direct" | val id)
[ -n "$DM" ] || { echo "STOP: could not open self-DM channel."; exit 1; }
PID=$(curl -sS -m20 "${A[@]}" \
  -d "{\"channel_id\":\"$DM\",\"message\":\"ActionPulse self-test (safe to delete)\"}" \
  "$MM_BASE/api/v4/posts" | val id)
[ -n "$PID" ] || { echo "STOP: could not post to self-DM."; exit 1; }
BEFORE=$(curl -sS -m20 "${A[@]}" "$MM_BASE/api/v4/channels/$DM/members/me" | val last_viewed_at)
curl -sS -m20 "${A[@]}" -o /dev/null "$MM_BASE/api/v4/channels/$DM/posts"   # <-- the GET under test
AFTER=$(curl -sS -m20 "${A[@]}" "$MM_BASE/api/v4/channels/$DM/members/me" | val last_viewed_at)
if [ "$BEFORE" = "$AFTER" ]; then
  echo "last_viewed_at $BEFORE unchanged after GET  ->  GET does NOT mark read ✅"
else
  echo "last_viewed_at moved $BEFORE -> $AFTER after GET  ->  ⚠ investigate (something cleared unread)"
fi

say "3  reactions read-back (EP-15 data path)"
curl -sS -m20 "${A[@]}" -o /dev/null \
  -d "{\"user_id\":\"$UID\",\"post_id\":\"$PID\",\"emoji_name\":\"thumbsup\"}" \
  "$MM_BASE/api/v4/reactions"
curl -sS -m20 "${A[@]}" "$MM_BASE/api/v4/posts/$PID/reactions" \
  | python3 -c "import sys,json;print('reactions read back:',[x.get('emoji_name') for x in json.load(sys.stdin)])"

say "4  self-DM delivery + @-escape (pings only you)"
U=$(printf '%s' "$ME" | val username)
# Build the body in Python so the backtick code-span survives bash quoting intact.
BODY=$(DM="$DM" U="$U" python3 -c 'import json,os;print(json.dumps({"channel_id":os.environ["DM"],"message":"escaped:`@nobody` literal-self:@"+os.environ["U"]}))')
PID2=$(curl -sS -m20 "${A[@]}" -d "$BODY" "$MM_BASE/api/v4/posts" | val id)
echo "posted ${PID2:0:6}… — open your self-DM: \`@nobody\` should render as code (no mention); @$U as a real mention."

say "5  cleanup — delete test posts"
for p in "$PID2" "$PID"; do
  [ -n "$p" ] && curl -sS -m20 "${A[@]}" -X DELETE -o /dev/null -w "deleted $p -> %{http_code}\n" "$MM_BASE/api/v4/posts/$p"
done
echo "done — the self-DM channel remains (your own notes-to-self); test posts removed."
