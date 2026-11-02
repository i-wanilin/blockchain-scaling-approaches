// Deploys the UC2 PointsToken on a zkSync Era chain (untimed run setup).
//
//   node deploy/zk-token/deploy.js --l2 http://H:3050 --key-file F \
//     --zbin contracts/points/zksync/PointsToken.zbin \
//     --abi perun/hub-demo/pointstoken/PointsToken.abi
//
// EraVM needs its own bytecode (the .zbin from zksolc) and an EIP-712 deploy
// through the ContractDeployer system contract, which zksync-ethers'
// ContractFactory builds. The key is read from a file (the caller writes it
// with mode 0600), never taken from the command line. Prints one JSON line
// {"token","tx","gas_used"} and exits non-zero on any failure.
const fs = require("fs");
const { Provider, Wallet, ContractFactory } = require("zksync-ethers");

function args(argv) {
  const need = ["l2", "key-file", "zbin", "abi"];
  const out = {};
  for (let i = 0; i < argv.length; i += 2) {
    const k = argv[i].replace(/^--/, "");
    if (!need.includes(k) || i + 1 >= argv.length) throw new Error(`bad argument ${argv[i]}`);
    out[k] = argv[i + 1];
  }
  const missing = need.filter((k) => !out[k]);
  if (missing.length) throw new Error(`missing --${missing.join(", --")}`);
  return out;
}

async function main() {
  const a = args(process.argv.slice(2));
  const key = fs.readFileSync(a["key-file"], "utf8").trim();
  let bytecode = fs.readFileSync(a.zbin, "utf8").trim();
  if (!bytecode.startsWith("0x")) bytecode = "0x" + bytecode;
  const abi = JSON.parse(fs.readFileSync(a.abi, "utf8"));
  const wallet = new Wallet(key, new Provider(a.l2));
  const factory = new ContractFactory(abi, bytecode, wallet, "create");
  const contract = await factory.deploy();
  const tx = contract.deploymentTransaction();
  const receipt = await tx.wait();
  if (!receipt || receipt.status !== 1) throw new Error(`deploy tx ${tx.hash} failed`);
  const token = await contract.getAddress();
  if ((await wallet.provider.getCode(token)) === "0x") throw new Error(`no code at ${token}`);
  console.log(JSON.stringify({ token, tx: tx.hash, gas_used: Number(receipt.gasUsed) }));
}

main().catch((e) => {
  console.error("deploy.js:", e.message);
  process.exit(1);
});
