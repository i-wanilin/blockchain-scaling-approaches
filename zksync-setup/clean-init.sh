#!/bin/bash
# Clean end-to-end zkstack init — encodes every lesson from attempts 1-9.
set -x
cd ~/zksync-ecosystem/thesis_local || exit 1
ZKPATH="$HOME/.foundry-zksync/bin:$HOME/.cargo/bin:$HOME/.local/bin:$HOME/.nvm/versions/node/v24.14.1/bin:/usr/local/bin:/usr/bin:/bin"
CAST=$HOME/.foundry/bin/cast
DK=0x7726827caac94a7f9e1b160f7ea819f172f7b6f9d2a97f992c38edeab82d4110

echo "=== 1. fresh L1 + postgres ==="
docker compose down -v
docker compose up -d
sleep 8
$CAST chain-id --rpc-url http://127.0.0.1:8945 || exit 1

echo "=== 2. wipe stale local chain state (rocksdb + configs regenerate) ==="
rm -rf chains/thesis_chain/db
# keep ZkStack.yaml (wallet_creation: InFile — skips the broken distribute step)

echo "=== 3. fund every wallet (InFile mode skips auto-distribution) ==="
for a in $(cat chains/thesis_chain/configs/wallets.yaml configs/wallets.yaml | grep -oE '0x[a-fA-F0-9]{40}' | sort -u); do
  $CAST send "$a" --value 5000000ether --gas-price 2gwei --priority-gas-price 1gwei \
    --private-key $DK --rpc-url http://127.0.0.1:8945 > /dev/null 2>&1
done

echo "=== 4. ecosystem init WITHOUT --dev (so --l1-rpc-url is honored), pty for prompts ==="
printf '\n%.0s' {1..60} | script -qec "env PATH=$ZKPATH nice -n 10 $HOME/.local/bin/zkstack ecosystem init \
  --deploy-ecosystem true --deploy-erc20 true --deploy-paymaster true \
  --observability false --update-submodules false \
  --l1-rpc-url http://127.0.0.1:8945 \
  --server-db-url postgres://postgres:notsecurepassword@127.0.0.1:5432 \
  --server-db-name zksync_server_thesis_chain \
  --validium-type no-da --no-port-reallocation --verify false" /dev/null
RC=$?
echo "=== init exit: $RC ==="

echo "=== 5. widen eth_watch window in regenerated general.yaml (harmless safety) ==="
python3 - <<'PYEOF'
import re
p='/home/i_wanilin/zksync-ecosystem/thesis_local/chains/thesis_chain/configs/general.yaml'
s=open(p).read()
if 'watcher:' in s:
    if 'event_expiration_blocks' not in s.split('watcher:')[1][:200]:
        s=re.sub(r'(  watcher:\n)', r'\1    event_expiration_blocks: 1000000\n', s, count=1)
else:
    s=re.sub(r'(^eth:\n)', r'\1  watcher:\n    event_expiration_blocks: 1000000\n', s, count=1, flags=re.M)
open(p,'w').write(s)
print('watcher window patched')
PYEOF
echo "=== DONE ==="
