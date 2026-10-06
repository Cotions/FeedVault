// Starts the throwaway instance (harness.js) before the tests, with the
// layout checks' worst cases (make_demo.py --stress, then stress.js through
// its API); the function it returns is the teardown. The tests reach it
// through FEEDVAULT_E2E_URL, and the worst cases through
// FEEDVAULT_E2E_STRESS, which the workers inherit (playwright.config.js's
// baseURL, fixtures.js's stressData).
import path from "node:path";
import { startInstance, stopInstance } from "./harness.js";
import { seedStress } from "./stress.js";

export default async function globalSetup(config) {
  const inst = await startInstance({ log: msg => console.log(msg), stress: true });
  const saveLog = path.join(config.rootDir, "..", "test-results", "backend.log");
  try {
    process.env.FEEDVAULT_E2E_STRESS = JSON.stringify(await seedStress(inst.url));
  } catch (e) {
    await stopInstance(inst, { saveLog }).catch(() => {});
    throw e;
  }
  process.env.FEEDVAULT_E2E_URL = inst.url;
  return async () => {
    await stopInstance(inst, { saveLog });
    console.log(`e2e: backend pid ${inst.pid} stopped, ${inst.root} deleted`);
  };
}
