import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 120000,
  expect: { timeout: 30000 },
  use: { baseURL: "http://127.0.0.1:8093", trace: "retain-on-failure" },
  webServer: {
    command: process.platform === "win32"
      ? ".venv-local\\Scripts\\python.exe -m tests.web_server"
      : "./.venv-local/bin/python -m tests.web_server",
    cwd: "..",
    url: "http://127.0.0.1:8093/api/ready",
    timeout: 180000,
    reuseExistingServer: false,
  },
});
