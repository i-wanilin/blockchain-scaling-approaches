// zkSync full-chain bridge benchmark: deposit L1->L2 (gas + e2e latency) and
// withdrawal initiation L2->L1 (gas + latency). Local zkstack chain 271.
const { Provider, Wallet, utils } = require("zksync-ethers");
const { ethers } = require("ethers");

const L1 = "http://127.0.0.1:8945";
const L2 = "http://127.0.0.1:3050";
// reth-genesis rich key (zkstack deployer)
const RICH = "0x7726827caac94a7f9e1b160f7ea819f172f7b6f9d2a97f992c38edeab82d4110";

async function main() {
  const l1p = new ethers.JsonRpcProvider(L1);
  const l2p = new Provider(L2);
  const w = new Wallet(RICH, l2p, l1p);
  const me = await w.getAddress();

  console.log("=== DEPOSIT L1->L2 (3 runs, 1 ETH each) ===");
  for (let i = 0; i < 3; i++) {
    const balBefore = await l2p.getBalance(me);
    const t0 = Date.now();
    const dep = await w.deposit({ token: utils.ETH_ADDRESS, amount: ethers.parseEther("1") });
    console.log(`deposit tx ${dep.hash}`);
    const l1rcpt = await dep.waitL1Commit();
    const tL1 = Date.now();
    // poll L2 balance until credited
    while ((await l2p.getBalance(me)) <= balBefore) {
      await new Promise(r => setTimeout(r, 100));
    }
    const tL2 = Date.now();
    console.log(`run ${i + 1}: L1 gas=${l1rcpt.gasUsed}  L1 inclusion=${((tL1 - t0) / 1000).toFixed(2)}s  L1->L2 credited e2e=${((tL2 - t0) / 1000).toFixed(2)}s`);
  }

  console.log("=== WITHDRAWAL INITIATION L2->L1 (3 runs, 0.1 ETH) ===");
  for (let i = 0; i < 3; i++) {
    const t0 = Date.now();
    const wd = await w.withdraw({ token: utils.ETH_ADDRESS, amount: ethers.parseEther("0.1") });
    console.log(`withdraw tx ${wd.hash}`);
    const rcpt = await wd.wait();
    console.log(`run ${i + 1}: L2 gas=${rcpt.gasUsed}  receipt latency=${((Date.now() - t0) / 1000).toFixed(2)}s`);
  }

  // fund the python-bench key for the latency benchmark
  const t = await w.transfer({ to: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266", amount: ethers.parseEther("0.5") });
  await t.wait();
  console.log("bench key funded on L2");
}
main().catch(e => { console.error("FATAL:", e.message); process.exit(1); });
