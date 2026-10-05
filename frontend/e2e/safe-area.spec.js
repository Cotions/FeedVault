// A phone with a notch and a home bar (safe-area insets top 47, bottom 34,
// through Chromium's DevTools protocol): the header's buttons and Review's
// bar stay out of both bands, as #88 checked by hand.
import { test, expect } from "./fixtures.js";

const TOP = 47, BOTTOM = 34;

test("the header and Review's bar keep their buttons out of the notch and the home bar", async ({ page }) => {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setSafeAreaInsetsOverride", { insets: { top: TOP, bottom: BOTTOM, left: 0, right: 0 } });
  await page.goto("/review");
  await expect(page.locator(".review-left")).toHaveText(/^\d[\d,]*$/);

  // The override is in force: env() gives the insets.
  const insets = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.style.cssText = "position:fixed;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)";
    document.body.append(probe);
    const s = getComputedStyle(probe);
    const out = [s.paddingTop, s.paddingBottom];
    probe.remove();
    return out;
  });
  expect(insets).toEqual([`${TOP}px`, `${BOTTOM}px`]);

  const boxes = await page.evaluate(() => {
    const shown = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; };
    const box = el => { const r = el.getBoundingClientRect(); return { what: el.getAttribute("aria-label") || el.textContent.trim(), top: r.top, bottom: r.bottom }; };
    const pick = sel => [...document.querySelectorAll(sel)].filter(shown).map(box);
    return {
      height: window.innerHeight,
      header: pick("header button, header a, header input"),
      bar: pick(".review-actions button"),
    };
  });
  expect(boxes.header.length).toBeGreaterThan(0);
  expect(boxes.bar.length).toBeGreaterThan(2);
  for (const b of [...boxes.header, ...boxes.bar]) {
    expect.soft(b.top, `${b.what}: top`).toBeGreaterThanOrEqual(TOP);
    expect.soft(b.bottom, `${b.what}: bottom`).toBeLessThanOrEqual(boxes.height - BOTTOM);
  }
  // Review's bar sits on the home bar's band, not below the screen.
  const lowest = Math.max(...boxes.bar.map(b => b.bottom));
  expect(lowest).toBeGreaterThan(boxes.height - BOTTOM - 80);
});
