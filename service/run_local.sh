#!/usr/bin/env bash
# Local A2A simulation: one uvicorn process per agent, exactly as the codelab
# does before deploying to Cloud Run. Run this from service/.
set -euo pipefail

declare -A AGENTS=(
  [8001]="access-control:lane:deep"
  [8002]="oracle-expert:domain:deep"
  [8003]="execution-trace:lane:deep"
  [8004]="proxy-upgrade:platform:deep"
  [8005]="account-abstraction:platform:deep"
  [8006]="adversarial-verifier:verifier:verify"
)

pids=()
cleanup() { echo; echo "stopping ${#pids[@]} agents"; kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for port in "${!AGENTS[@]}"; do
  IFS=: read -r id role tier <<< "${AGENTS[$port]}"
  echo "starting $id ($role/$tier) on :$port"
  AGENT_ID="$id" AGENT_ROLE="$role" MODEL_TIER="$tier" PUBLIC_URL="http://localhost:$port" \
    uvicorn audit_mas.a2a_server:app --host 0.0.0.0 --port "$port" --log-level warning &
  pids+=($!)
done

sleep 2
echo
echo "agent cards:"
for port in "${!AGENTS[@]}"; do
  printf '  :%s  ' "$port"
  curl -s "http://localhost:$port/.well-known/agent-card.json" | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(d["name"], "-", d["description"][:70])' 2>/dev/null || echo "(not ready)"
done
echo
echo "export these for the orchestrator:"
for port in "${!AGENTS[@]}"; do
  IFS=: read -r id _ _ <<< "${AGENTS[$port]}"
  echo "  export AGENT_CARD_URL_$(echo "$id" | tr 'a-z-' 'A-Z_')=http://localhost:$port"
done
echo
echo "Ctrl-C to stop."
wait
