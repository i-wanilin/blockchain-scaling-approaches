#!/bin/bash
# clean-init.sh adapted for the desktop. Same sequence, but the laptop's
# hardcoded ZKPATH does not resolve here:
#   * foundry-zksync lives at ~/.foundry-zksync/.foundry/bin (not .../bin)
#   * node is v24.15.0 (not v24.14.1)
# Both are auto-detected so this does not rot again.
set -x
cd ~/zksync-ecosystem/thesis_local || exit 1

FZK=$(dirname "$(find "$HOME/.foundry-zksync" -name forge -type f 2>/dev/null | head -1)")
NODEBIN=$(dirname "$(find "$HOME/.nvm/versions/node" -maxdepth 3 -name node -type f 2>/dev/null | head -1)")
ZKPATH="$FZK:$HOME/.cargo/bin:$HOME/.local/bin:$NODEBIN:/usr/local/bin:/usr/bin:/bin"
CAST=$HOME/.foundry/bin/cast
DK=0x7726827caac94a7f9e1b160f7ea819f172f7b6f9d2a97f992c38edeab82d4110
echo "ZKPATH=$ZKPATH"

echo "=== 1. fresh L1 + postgres ==="
docker compose down -v
docker compose up -d
sleep 10
$CAST chain-id --rpc-url http://127.0.0.1:8945 || exit 1

echo "=== 2. wipe stale local chain state ==="
rm -rf chains/thesis_chain/db

echo "=== 3. fund every wallet (InFile mode skips auto-distribution) ==="
for a in $(cat chains/thesis_chain/configs/wallets.yaml configs/wallets.yaml | grep -oE '0x[a-fA-F0-9]{40}' | sort -u); do
  $CAST send "$a" --value 5000000ether --gas-price 2gwei --priority-gas-price 1gwei \
    --private-key $DK --rpc-url http://127.0.0.1:8945 > /dev/null 2>&1
done

echo "=== 4. ecosystem init (no --dev, pty for prompts) ==="
printf '\n%.0s' {1..60} | script -qec "env PATH=$ZKPATH nice -n 10 $HOME/.local/bin/zkstack ecosystem init \
  --deploy-ecosystem true --deploy-erc20 true --deploy-paymaster true \
  --observability false --update-submodules false \
  --l1-rpc-url http://127.0.0.1:8945 \
  --server-db-url postgres://postgres:notsecurepassword@127.0.0.1:5432 \
  --server-db-name zksync_server_thesis_chain \
  --validium-type no-da --no-port-reallocation --verify false" /dev/null
RC=$?
echo "=== init exit: $RC ==="

echo "=== 5. widen eth_watch window (was omitted in the first desktop run) ==="
# Default event_expiration_blocks is 1000. reth mines every 300 ms here, so L1
# events expire after ~5 minutes and L1->L2 priority ops (deposits) can be
# missed. clean-init.sh has always done this; the first desktop adaptation
# dropped it by mistake.
python3 - <<'PYEOF'
import re
p='/home/i_wanilin/zksync-ecosystem/thesis_local/chains/thesis_chain/configs/general.yaml'
s=open(p).read()
if re.search(r'event_expiration_blocks:\s*\d+', s):
    s=re.sub(r'event_expiration_blocks:\s*\d+', 'event_expiration_blocks: 1000000', s)
elif 'watcher:' in s:
    s=re.sub(r'(  watcher:\n)', r'\1    event_expiration_blocks: 1000000\n', s, count=1)
else:
    s=re.sub(r'(^eth:\n)', r'\1  watcher:\n    event_expiration_blocks: 1000000\n', s, count=1, flags=re.M)
open(p,'w').write(s)
print('watcher window patched -> 1000000')
PYEOF
echo "=== DONE (restart the server for this to take effect) ==="
