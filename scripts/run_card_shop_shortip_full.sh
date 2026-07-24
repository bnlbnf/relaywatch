#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/root/relaywatch-deploy}"
source "$DEPLOY_DIR/scripts/common.sh"

mkdir -p "$DEPLOY_DIR/logs" "$DEPLOY_DIR/state"
LOG="$DEPLOY_DIR/logs/refresh_card_shop_goods_shortip_full.log"
exec >> "$LOG" 2>&1

echo
START_TS="$(date -Is)"
echo "===== shortip full refresh start $START_TS ====="

exec 8>/tmp/relaywatch-card-refresh.lock
if ! flock -n 8; then
  echo "another card goods refresh is running, skip"
  exit 0
fi

require_env DATABASE_URL
cd "$APP_DIR"

TOTAL="$($PYTHON39_BIN - <<'PY'
import csv, server
seen=set()
for r in csv.DictReader(open(server.LDXP_SHOPS_PATH, encoding='utf-8-sig')):
    token=(r.get('token') or r.get('token_aliases') or '').strip()
    if not token:
        url=(r.get('shop_url') or '').strip().rstrip('/')
        token=url.split('/')[-1] if url else ''
    if token:
        seen.add(token.lower())
print(len(seen))
PY
)"

BATCH_SIZE="${CARD_SHORTIP_BATCH_SIZE:-600}"
WORKERS="${CARD_SHORTIP_WORKERS:-128}"
PROXY_RETRIES="${CARD_SHORTIP_PROXY_RETRIES:-3}"
TIMEOUT="${CARD_SHOP_API_TIMEOUT:-3}"
API_RETRIES="${CARD_SHOP_API_RETRIES:-0}"
API_RETRY_SLEEP="${CARD_SHOP_API_RETRY_SLEEP:-0}"
SLEEP="${CARD_SHORTIP_SHOP_SLEEP:-0}"
RETRY_ENABLED="${CARD_SHORTIP_RETRY_FAILED:-1}"
RETRY_WORKERS="${CARD_SHORTIP_RETRY_WORKERS:-64}"
RETRY_PROXY_RETRIES="${CARD_SHORTIP_RETRY_PROXY_RETRIES:-5}"
RETRY_TIMEOUT="${CARD_SHORTIP_RETRY_TIMEOUT:-4}"
RETRY_BATCH_SIZE="${CARD_SHORTIP_RETRY_BATCH_SIZE:-300}"
MAX_RETRY_ROUNDS="${CARD_SHORTIP_MAX_RETRY_ROUNDS:-0}"
REMOVED_SHOPS_FILE="$DEPLOY_DIR/state/card_removed_shop_tokens_pending.txt"
CURRENT_REMOVED_SHOPS_FILE="$DEPLOY_DIR/state/card_removed_shop_tokens_current.txt"
BATCH_FAILURES_FILE="$DEPLOY_DIR/state/card_shop_batch_failures.csv"
BATCH_SUCCESSES_FILE="$DEPLOY_DIR/state/card_shop_batch_successes.csv"
RETRY_TOKENS_FILE="$DEPLOY_DIR/state/card_shop_retry_tokens_current.txt"
RETRY_FAILURES_FILE="$DEPLOY_DIR/state/card_shop_retry_failures.csv"
RETRY_SUCCESSES_FILE="$DEPLOY_DIR/state/card_shop_retry_successes.csv"

if [[ -s "$CURRENT_REMOVED_SHOPS_FILE" ]]; then cp "$CURRENT_REMOVED_SHOPS_FILE" "$REMOVED_SHOPS_FILE"; else : > "$REMOVED_SHOPS_FILE"; fi
: > "$RETRY_TOKENS_FILE"
rm -f "$DEPLOY_DIR"/state/card_shop_retry_tokens_part_*

echo "total_shops=$TOTAL batch_size=$BATCH_SIZE workers=$WORKERS proxy_retries=$PROXY_RETRIES timeout=$TIMEOUT api_retries=$API_RETRIES api_retry_sleep=$API_RETRY_SLEEP retry_enabled=$RETRY_ENABLED retry_workers=$RETRY_WORKERS retry_proxy_retries=$RETRY_PROXY_RETRIES retry_timeout=$RETRY_TIMEOUT"

update_removed_and_retry() {
  local failures_file="$1" successes_file="$2" retry_file="$3"
  BATCH_FAILURES_FILE="$failures_file" BATCH_SUCCESSES_FILE="$successes_file" REMOVED_SHOPS_FILE="$REMOVED_SHOPS_FILE" RETRY_TOKENS_FILE="$retry_file" "$PYTHON39_BIN" - <<'PY'
import csv, os
removed_file=os.environ.get('REMOVED_SHOPS_FILE') or ''
failures=os.environ.get('BATCH_FAILURES_FILE') or ''
successes=os.environ.get('BATCH_SUCCESSES_FILE') or ''
retry_file=os.environ.get('RETRY_TOKENS_FILE') or ''
removed_markers=('店铺链接不存在','商家已被关闭','商家已被封禁','该商家已被封禁','商家已被关闭交易','商家已注销')
retry_markers=('timed out','timeout','Connection timed out','Could not connect','Operation timed out','curl:','proxy','RemoteDisconnected','EOF','SSL','Connection reset')
removed=set()
if removed_file and os.path.exists(removed_file):
    with open(removed_file,encoding='utf-8',errors='ignore') as f: removed={x.strip().lower() for x in f if x.strip()}
retry=set()
if retry_file and os.path.exists(retry_file):
    with open(retry_file,encoding='utf-8',errors='ignore') as f: retry={x.strip() for x in f if x.strip()}
if successes and os.path.exists(successes):
    with open(successes,encoding='utf-8-sig',errors='ignore') as f:
        for row in csv.DictReader(f):
            token=(row.get('token') or '').strip()
            if token:
                removed.discard(token.lower()); retry.discard(token)
if failures and os.path.exists(failures):
    with open(failures,encoding='utf-8-sig',errors='ignore') as f:
        for row in csv.DictReader(f):
            token=(row.get('token') or '').strip(); error=row.get('error') or ''
            if not token: continue
            if any(m in error for m in removed_markers):
                removed.add(token.lower()); retry.discard(token)
            elif any(m.lower() in error.lower() for m in retry_markers):
                retry.add(token)
if removed_file:
    with open(removed_file,'w',encoding='utf-8') as w:
        for token in sorted(removed): w.write(token+'\n')
if retry_file:
    with open(retry_file,'w',encoding='utf-8') as w:
        for token in sorted(retry,key=str.lower): w.write(token+'\n')
PY
}

remove_retry_successes() {
  local successes_file="$1" retry_file="$2"
  [[ -s "$successes_file" && -s "$retry_file" ]] || return 0
  SUCC_FILE="$successes_file" TOK_FILE="$retry_file" "$PYTHON39_BIN" - <<'PY'
import csv, os
succ=set()
with open(os.environ['SUCC_FILE'],encoding='utf-8-sig',errors='ignore') as f:
    for row in csv.DictReader(f):
        t=(row.get('token') or '').strip()
        if t: succ.add(t.lower())
left=[]
with open(os.environ['TOK_FILE'],encoding='utf-8',errors='ignore') as f:
    for line in f:
        t=line.strip()
        if t and t.lower() not in succ: left.append(t)
with open(os.environ['TOK_FILE'],'w',encoding='utf-8') as w:
    for t in left: w.write(t+'\n')
PY
}

run_proxy_refresh() {
  CARD_PROXY_POOL_MAX_VALID=200 CARD_PROXY_POOL_MIN_VALID=20 CARD_PROXY_POOL_WORKERS=${CARD_PROXY_POOL_WORKERS:-160} CARD_PROXY_POOL_TIMEOUT=${CARD_PROXY_POOL_TIMEOUT:-3} /bin/bash "$DEPLOY_DIR/scripts/refresh_card_proxy_pool.sh" || echo "proxy_refresh_failed"
  local valid_count
  valid_count="$(grep -cve '^$' "$DEPLOY_DIR/state/card_shop_proxies.txt" 2>/dev/null || true)"
  echo "valid_proxy_count=$valid_count"
  if [[ "$valid_count" -lt "${CARD_PROXY_POOL_MIN_VALID:-20}" ]]; then
    echo "valid_proxy_count_below_min=$valid_count, abort current refresh to avoid stale proxy empty run"
    return 1
  fi
}

OFFSET=0; BATCH=0
while [[ "$OFFSET" -lt "$TOTAL" ]]; do
  BATCH=$((BATCH+1))
  echo; echo "--- batch=$BATCH offset=$OFFSET limit=$BATCH_SIZE $(date -Is) ---"
  run_proxy_refresh || exit 2
  env CARD_SHOP_HTTP_BACKEND=curl_cffi CARD_SHOP_API_TIMEOUT="$TIMEOUT" CARD_SHOP_API_RETRIES="$API_RETRIES" CARD_SHOP_API_RETRY_SLEEP="$API_RETRY_SLEEP" "$PYTHON39_BIN" "$APP_DIR/scripts/refresh_card_shop_goods.py" --offset "$OFFSET" --limit-shops "$BATCH_SIZE" --workers "$WORKERS" --shop-sleep "$SLEEP" --proxy-retries "$PROXY_RETRIES" --proxy-file "$DEPLOY_DIR/state/card_shop_proxies.txt" --failures-out "$BATCH_FAILURES_FILE" --successes-out "$BATCH_SUCCESSES_FILE" --replace-only-shop-api --min-rows 1000 --min-fresh-rows 0 || echo "batch_failed offset=$OFFSET"
  update_removed_and_retry "$BATCH_FAILURES_FILE" "$BATCH_SUCCESSES_FILE" "$RETRY_TOKENS_FILE"
  echo "removed_shop_tokens_pending=$(grep -cve '^$' "$REMOVED_SHOPS_FILE" 2>/dev/null || true) retry_tokens_pending=$(grep -cve '^$' "$RETRY_TOKENS_FILE" 2>/dev/null || true)"
  OFFSET=$((OFFSET+BATCH_SIZE))
  sleep 1
done

if [[ "$RETRY_ENABLED" != "0" && -s "$RETRY_TOKENS_FILE" ]]; then
  RETRY_ROUND=0
  while [[ -s "$RETRY_TOKENS_FILE" ]]; do
    RETRY_ROUND=$((RETRY_ROUND+1))
    if [[ "$MAX_RETRY_ROUNDS" != "0" && "$RETRY_ROUND" -gt "$MAX_RETRY_ROUNDS" ]]; then
      echo "retry_remaining_after_max_rounds=$(grep -cve '^$' "$RETRY_TOKENS_FILE" 2>/dev/null || true) max_retry_rounds=$MAX_RETRY_ROUNDS"
      break
    fi
    echo; echo "--- retry transient failed shops round=$RETRY_ROUND $(date -Is) ---"
    echo "retry_total=$(grep -cve '^$' "$RETRY_TOKENS_FILE" 2>/dev/null || true) retry_batch_size=$RETRY_BATCH_SIZE retry_workers=$RETRY_WORKERS retry_proxy_retries=$RETRY_PROXY_RETRIES retry_timeout=$RETRY_TIMEOUT max_retry_rounds=$MAX_RETRY_ROUNDS"
    rm -f "$DEPLOY_DIR"/state/card_shop_retry_tokens_part_*
    split -l "$RETRY_BATCH_SIZE" -d -a 3 "$RETRY_TOKENS_FILE" "$DEPLOY_DIR/state/card_shop_retry_tokens_part_"
    BEFORE_ROUND="$(grep -cve '^$' "$RETRY_TOKENS_FILE" 2>/dev/null || true)"
    for part in "$DEPLOY_DIR"/state/card_shop_retry_tokens_part_*; do
      [[ -s "$part" ]] || continue
      echo "--- retry part=$(basename "$part") count=$(grep -cve '^$' "$part" || true) $(date -Is) ---"
      run_proxy_refresh || continue
      env CARD_SHOP_HTTP_BACKEND=curl_cffi CARD_SHOP_API_TIMEOUT="$RETRY_TIMEOUT" CARD_SHOP_API_RETRIES=0 CARD_SHOP_API_RETRY_SLEEP=0 "$PYTHON39_BIN" "$APP_DIR/scripts/refresh_card_shop_goods.py" --tokens-file "$part" --workers "$RETRY_WORKERS" --shop-sleep 0 --proxy-retries "$RETRY_PROXY_RETRIES" --proxy-file "$DEPLOY_DIR/state/card_shop_proxies.txt" --failures-out "$RETRY_FAILURES_FILE" --successes-out "$RETRY_SUCCESSES_FILE" --replace-only-shop-api --min-rows 1000 --min-fresh-rows 0 || echo "retry_failed part=$part"
      update_removed_and_retry "$RETRY_FAILURES_FILE" "$RETRY_SUCCESSES_FILE" "$RETRY_TOKENS_FILE"
      remove_retry_successes "$RETRY_SUCCESSES_FILE" "$RETRY_TOKENS_FILE"
    done
    rm -f "$DEPLOY_DIR"/state/card_shop_retry_tokens_part_* "$DEPLOY_DIR/state/card_shop_retry_tokens_ignore.tmp"
    AFTER_ROUND="$(grep -cve '^$' "$RETRY_TOKENS_FILE" 2>/dev/null || true)"
    echo "retry_remaining=$AFTER_ROUND"
    if [[ "$AFTER_ROUND" -eq 0 ]]; then
      break
    fi
    if [[ "$AFTER_ROUND" -ge "$BEFORE_ROUND" ]]; then
      echo "retry_no_progress_this_round=$AFTER_ROUND, refresh proxy and continue"
    fi
  done
fi

echo; echo "--- import db $(date -Is) ---"
cp "$REMOVED_SHOPS_FILE" "$CURRENT_REMOVED_SHOPS_FILE"
env DATABASE_URL="$DATABASE_URL" CARD_STRICT_LDXP_FULL_OVERWRITE=1 CARD_REMOVED_SHOPS_FILE="$CURRENT_REMOVED_SHOPS_FILE" "$PYTHON39_BIN" "$APP_DIR/scripts/import_card_goods.py"
echo "===== shortip full refresh done $(date -Is) ====="
