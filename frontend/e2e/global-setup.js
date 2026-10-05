// Starts the throwaway instance (harness.js) before the tests; the function
// it returns is the teardown. The tests reach it through FEEDVAULT_E2E_URL,
// which the workers inherit (playwright.config.js's baseURL).
import path from "node:path";
import { startInstance, stopInstance } from "./harness.js";

export default async function globalSetup(config) {
  const inst = await startInstance({ log: msg => console.log(msg) });
  process.env.FEEDVAULT_E2E_URL = inst.url;
  const saveLog = path.join(config.rootDir, "..", "test-results", "backend.log");
  return async () => {
    await stopInstance(inst, { saveLog });
    console.log(`e2e: backend pid ${inst.pid} stopped, ${inst.root} deleted`);
  };
}
