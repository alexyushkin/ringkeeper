import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

// The end-to-end tests drive the real stack (web, api, gateway, stt, Postgres,
// a demo client database) started by e2e/global-setup.ts on its own ports.
// Chromium's fake microphone plays the fixture recording, so hold-to-talk
// records real speech.

const FIXTURE = path.resolve(__dirname, "..", "tests", "fixtures", "voice-works.wav");

export default defineConfig({
  testDir: "./e2e",
  timeout: 10 * 60 * 1000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: "http://localhost:13000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    permissions: ["microphone"],
    acceptDownloads: true,
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          args: [
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            `--use-file-for-fake-audio-capture=${FIXTURE}`,
          ],
        },
      },
    },
  ],
});
