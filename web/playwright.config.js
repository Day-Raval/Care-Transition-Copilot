import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.CARE_TRANSITION_TEST_PYTHON || (process.platform === "win32"
  ? path.join(root, ".venv-local/Scripts/python.exe")
  : path.join(root, ".venv-local/bin/python"));

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 120000,
  expect: { timeout: 30000 },
  use: { baseURL: "http://127.0.0.1:8093", trace: "retain-on-failure" },
  webServer: {
    command: `"${python}" -m tests.web_server`,
    cwd: root,
    url: "http://127.0.0.1:8093/api/ready",
    timeout: 180000,
    reuseExistingServer: false,
  },
});
