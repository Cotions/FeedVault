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
import { protectedDirs } from "./harness.js";

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
