// Desktop, 1440x900: a source whose script cannot run says so on its row,
// and the script picker offers only what the source may run (#138).
//
// Script files are written only in the throwaway instance's scripts folder
// (beside its tmp config.json, as GET /api/scripts names it), checked to be
// inside the run's tmp dir first, and removed after. None of them is ever
// run: the one sync here fails at once, before anything starts.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, idle } from "./fixtures.js";
import { findPython, protectedDirs } from "./harness.js";

const H = { "X-FeedVault": "1" };

// The instance's scripts folder, refused unless it is in the e2e run's tmp dir.
async function scriptsDir(request) {
  const { dir } = await (await request.get("/api/scripts", { headers: H })).json();
  const real = fs.realpathSync(dir);
  const tmp = fs.realpathSync(os.tmpdir());
  const rel = path.relative(tmp, real);
  if (!dir || rel.startsWith("..") || path.isAbsolute(rel) || !rel.split(path.sep)[0].startsWith("feedvault-e2e-")) {
    throw new Error(`e2e: the scripts folder ${dir} is not the throwaway instance's`);
  }
  for (const p of protectedDirs()) {
    const r = path.relative(p, real);
    if (r === "" || (!r.startsWith("..") && !path.isAbsolute(r))) throw new Error(`e2e: refusing ${dir}`);
  }
  return dir;
}

// greet.sh: a harmless script (it would only echo), later made group-writable,
// which refuses it before it is read. noheader.sh: refused for its missing
// "# needs:" line, its text read (View).
const GREET = "#!/bin/sh\n# name: Greet\n# needs: target\necho hello\n";
const NOHEADER = "#!/bin/sh\necho no header here\n";

async function withScriptSource(request, run) {
  const dir = await scriptsDir(request);
  const files = { greet: path.join(dir, "greet.sh"), noheader: path.join(dir, "noheader.sh") };
  fs.writeFileSync(files.greet, GREET, { mode: 0o755 });
  fs.chmodSync(files.greet, 0o755);
  fs.writeFileSync(files.noheader, NOHEADER, { mode: 0o755 });
  fs.chmodSync(files.noheader, 0o755);
  const target = `e2e.script.${Date.now()}`;
  let id = null;
  try {
    const made = await (await request.post("/api/sources", { headers: H, data: { tool: "instaloader", target } })).json();
    expect(made.ok, JSON.stringify(made)).toBe(true);
    id = made.source.id;
    const set = await request.post(`/api/sources/${id}`, { headers: H, data: { options: { script: "greet" } } });
    expect(set.ok(), await set.text()).toBe(true);
    await run({ id, target, files });
  } finally {
    if (id != null) await request.delete(`/api/sources/${id}`, { headers: H });
    for (const f of Object.values(files)) fs.rmSync(f, { force: true });
  }
}

test("a source whose script is refused warns on its row and links to the script (#138)", async ({ page, request }) => {
  await withScriptSource(request, async ({ id, target, files }) => {
    fs.chmodSync(files.greet, 0o775);         // writable by group: refused
    await page.goto("/creators");
    const row = page.locator(`.source-row[data-source-id="${id}"]`);
    await expect(row).toBeVisible();
    await expect(row).toContainText(target);
    const warn = row.locator(".source-script-warn");
    await expect(warn).toContainText("script greet: refused");
    await expect(warn).toContainText("greet.sh is refused: writable by group or others");
    await expect(warn).toContainText("chmod go-w");
    // Sync fails at once with the reason: never "queued", nothing waited out.
    const r = await (await request.post(`/api/sources/${id}/sync`, { headers: H, data: {} })).json();
    expect(r.ok, JSON.stringify(r)).toBe(true);
    expect(r.job.state).toBe("failed");
    expect(r.job.message).toContain("greet.sh is refused");
    await warn.getByRole("link", { name: "See Scripts" }).click();
    await expect(page).toHaveURL(/\/scripts#script-greet$/);
    const entry = page.locator("#script-greet");
    await expect(entry).toHaveClass(/is-flash/);
    await expect(entry).toBeInViewport();
    await expect(entry).toContainText("refused");
    // Its text could not be read (refused before it is opened): nothing to view, nothing to run.
    await expect(entry.getByRole("button", { name: "View" })).toHaveCount(0);
    await expect(entry.getByRole("button", { name: "Run…" })).toHaveCount(0);
    // A refused file whose text was read: View shows it, read-only; still no Run.
    const other = page.locator("#script-noheader");
    await expect(other).toContainText("refused");
    await expect(other.getByRole("button", { name: "Run…" })).toHaveCount(0);
    await other.getByRole("button", { name: "View" }).click();
    await expect(other.locator("pre.script-content")).toHaveText(NOHEADER.trimEnd());
    await idle(page);
  });
});

test("the script picker groups scripts by what they run and hides other tools' (#138)", async ({ page, request }) => {
  await withScriptSource(request, async ({ id }) => {
    await page.goto("/creators");
    const row = page.locator(`.source-row[data-source-id="${id}"]`);
    await expect(row).toBeVisible();
    await expect(row.locator(".source-script-warn")).toHaveCount(0);   // greet can run
    await row.getByRole("button", { name: "Options" }).click();
    const pick = page.getByRole("combobox", { name: "Command a sync runs" });
    await expect(pick).toHaveValue("greet");
    const groups = await pick.locator("optgroup").evaluateAll(gs => gs.map(g => ({
      label: g.label,
      options: [...g.querySelectorAll("option")].map(o => ({ value: o.value, text: o.textContent, disabled: o.disabled })),
    })));
    const byLabel = Object.fromEntries(groups.map(g => [g.label, g.options]));
    expect(Object.keys(byLabel)).toEqual(["Runs instaloader", "Runs another program (not a downloader)", "Refused (see Scripts)"]);
    expect(byLabel["Runs instaloader"].map(o => o.value)).toContain("builtin:instaloader-profile");
    expect(byLabel["Runs instaloader"].every(o => o.value.startsWith("builtin:instaloader-") || !o.value.startsWith("builtin:"))).toBe(true);
    expect(byLabel["Runs another program (not a downloader)"].map(o => o.value)).toEqual(["greet"]);
    expect(byLabel["Refused (see Scripts)"]).toContainEqual({ value: "noheader", text: "noheader · refused", disabled: true });
    // Other tools' scripts are not offered at all, and a note says why.
    const values = await pick.locator("option").evaluateAll(os => os.map(o => o.value));
    expect(values.filter(v => /yt-dlp|gallery-dl/.test(v))).toEqual([]);
    await expect(page.locator(".script-pick-note")).toContainText("gallery-dl, yt-dlp, not shown");
    await expect(page.locator(".script-pick-note")).toContainText("instaloader’s lock group");
    // The select stays inside the dialog: short options, the reasons below it.
    const box = await pick.boundingBox();
    const dialog = await page.getByRole("alertdialog").boundingBox();
    expect(box.x + box.width).toBeLessThanOrEqual(dialog.x + dialog.width);
    await page.keyboard.press("Escape");
    await idle(page);
  });
});

// #139: a command's program path keeps its case, and a long script started
// on Scripts can be cancelled from its log, with focus kept off <body>, a
// neutral toast and no raw -15 in Jobs' history. ticker.sh prints a line a
// second until it is cancelled, in the throwaway instance; its #! is the
// e2e Python, the only interpreter the test guard lets a script use.
const TICKER = `#!${findPython()}\n# name: Ticker\n# needs: none\nimport time\nwhile True:\n    print("tick", flush=True)\n    time.sleep(1)\n`;
const onBody = page => page.evaluate(() => !document.activeElement || document.activeElement === document.body);
const getJob = async (request, id) => (await request.get(`/api/jobs/${id}`, { headers: H })).json();

async function withTicker(request, run) {
  const dir = await scriptsDir(request);
  const file = path.join(dir, "ticker.sh");
  fs.writeFileSync(file, TICKER, { mode: 0o755 });
  fs.chmodSync(file, 0o755);
  const jobs = [];
  try {
    await run({ jobs });
  } finally {
    for (const id of jobs) {
      if (!["done", "failed", "cancelled", "interrupted"].includes((await getJob(request, id)).state)) {
        await request.post(`/api/jobs/${id}/cancel`, { headers: H });
      }
    }
    fs.rmSync(file, { force: true });
  }
}

test("a command's program path shows as typed, not lowercased (#139)", async ({ page, request }) => {
  const dir = await scriptsDir(request);
  const tool = path.join(dir, "QA2Tools", "Hello.sh");
  const file = path.join(dir, "mixedcase.json");
  fs.writeFileSync(file, JSON.stringify({ name: "Mixed case", needs: "none", argv: [tool] }), { mode: 0o644 });
  fs.chmodSync(file, 0o644);
  try {
    await page.goto("/scripts");
    const chip = page.locator("#script-mixedcase .script-tool");
    await expect(chip).toHaveText(tool, { useInnerText: true });
    expect(await chip.evaluate(el => getComputedStyle(el).textTransform)).toBe("none");
    // It wraps in its row's head rather than running out of it.
    const row = await page.locator("#script-mixedcase").boundingBox();
    const box = await chip.boundingBox();
    expect(box.x + box.width).toBeLessThanOrEqual(row.x + row.width + 0.5);
    await idle(page);
  } finally {
    fs.rmSync(file, { force: true });
  }
});

test("Scripts: Cancel… on the log stops a long script (#139)", async ({ page, request }) => {
  await withTicker(request, async ({ jobs }) => {
    await page.goto("/scripts");
    const entry = page.locator("#script-ticker");
    await entry.getByRole("button", { name: "Run…" }).click();
    // QA pass 3: the form opens on its first field, and the log, once it
    // shows, has focus (not the list of files the form was in).
    await expect(entry.getByRole("textbox", { name: "Folder (optional)" })).toBeFocused();
    const started = page.waitForResponse(r => r.url().endsWith("/api/scripts/ticker/run"));
    await entry.getByRole("button", { name: "Run", exact: true }).click();
    const { job } = await (await started).json();
    jobs.push(job.id);
    const log = page.getByRole("region", { name: "Script log" });
    await expect(log).toBeFocused();
    await expect(log.locator(".job-state")).toHaveText("running", { timeout: 20_000 });
    await expect(log.locator(".job-log")).toContainText("tick");
    const cancel = log.getByRole("button", { name: "Cancel…" });
    await cancel.focus();
    await page.keyboard.press("Enter");
    const dialog = page.locator(".modal-overlay .modal");
    await expect(dialog).toContainText(`Script ticker (#${job.id}) is stopped`);
    await dialog.getByRole("button", { name: "Cancel job" }).focus();
    await page.keyboard.press("Enter");
    await expect(dialog).toHaveCount(0);
    await expect(log.locator(".job-state")).toHaveText("cancelled", { timeout: 20_000 });
    await expect(cancel).toHaveCount(0);
    await expect.poll(() => onBody(page), "focus after Cancel… went").toBe(false);
    await expect(log).toBeFocused();
    // Cancelled is not a success: the neutral toast, not the green check.
    await expect(page.locator(".toast", { hasText: "cancelled" })).toHaveClass(/toast-info/);
    expect((await getJob(request, job.id)).exit_code).toBe(-15);

    // Jobs' history: no raw signal in the Exit column; the title has it.
    await page.goto("/jobs");
    const exit = page.locator(".job-table tbody tr", { hasText: `#${job.id}` }).locator('td[data-label="Exit"]');
    await expect(exit).toHaveText("—");
    await expect(exit).toHaveAttribute("title", "Cancelled: ended by signal 15 (exit code -15)");
    await idle(page);
  });
});

test("an open Jobs page shows a job started elsewhere within seconds (#139)", async ({ page, request }) => {
  await withTicker(request, async ({ jobs }) => {
    await page.goto("/jobs");
    await expect(page.getByRole("region", { name: "Running and queued jobs" }).locator(".job-list, .empty")).toBeVisible();
    await idle(page, 500);
    // Started from another client: the page hears of it only by polling.
    const r = await (await request.post("/api/scripts/ticker/run", { headers: H, data: {} })).json();
    expect(r.ok, JSON.stringify(r)).toBe(true);
    jobs.push(r.job.id);
    const row = page.locator(".job-row", { hasText: `#${r.job.id}` });
    await expect(row).toBeVisible({ timeout: 6_000 });
    await request.post(`/api/jobs/${r.job.id}/cancel`, { headers: H });
    await expect(row).toHaveCount(0, { timeout: 20_000 });
    await idle(page);
  });
});
