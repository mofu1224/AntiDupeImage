import { expect, test } from "@playwright/test";
import { access, mkdir, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";

const API_BASE = "http://127.0.0.1:8000/api";
const REUSE_EXISTING_SERVERS = process.env.ANTIDUPEIMAGE_E2E_REUSE_SERVERS === "1";
const API_TOKEN = process.env.ANTIDUPEIMAGE_E2E_TOKEN;
if (!API_TOKEN) throw new Error("ANTIDUPEIMAGE_E2E_TOKEN was not initialized by Playwright config");
const BOOTSTRAP_TOKEN = process.env.ANTIDUPEIMAGE_E2E_BOOTSTRAP ?? "";
const HANDOFF_PATH = process.env.ANTIDUPEIMAGE_E2E_HANDOFF ?? "";
if (!REUSE_EXISTING_SERVERS && (!BOOTSTRAP_TOKEN || !HANDOFF_PATH)) {
  throw new Error("Bootstrap token and handoff path were not initialized for owned E2E servers");
}

test.beforeEach(async ({ request }) => {
  const response = await request.post(`${API_BASE}/system/reset`, {
    headers: { "X-AntiDupeImage-Token": API_TOKEN },
    data: { confirm: true }
  });
  expect(response.ok()).toBe(true);
});

test("scans exact duplicates and moves the selected duplicate into a duplicate folder", async ({
  page,
  request
}, testInfo) => {
  const scanDir = testInfo.outputPath("scan-root");
  await mkdir(scanDir, { recursive: true });
  const keepPath = path.join(scanDir, "a_keep.bmp");
  const duplicatePath = path.join(scanDir, "b_duplicate.bmp");
  const unrelatedPath = path.join(scanDir, "c_other.bmp");
  await writeFile(keepPath, createSolidBmp(24, 24, [190, 45, 54]));
  await writeFile(duplicatePath, createSolidBmp(24, 24, [190, 45, 54]));
  await writeFile(unrelatedPath, createSolidBmp(24, 24, [40, 105, 170]));

  await page.route("**/api/dialogs/folder", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ path: scanDir, cancelled: false })
    });
  });

  if (!REUSE_EXISTING_SERVERS && process.platform !== "win32") {
    expect((await stat(path.dirname(HANDOFF_PATH))).mode & 0o777).toBe(0o700);
    expect((await stat(HANDOFF_PATH)).mode & 0o777).toBe(0o600);
  }

  await page.goto(
    REUSE_EXISTING_SERVERS
      ? `/#token=${encodeURIComponent(API_TOKEN)}`
      : `/#bootstrap=${encodeURIComponent(BOOTSTRAP_TOKEN)}`
  );
  await expect(page).toHaveTitle(/AntiDupeImage/);
  await expect(page).not.toHaveURL(/bootstrap=|token=/);
  await expect(page.getByRole("heading", { name: "AntiDupeImage" })).toBeVisible();
  if (!REUSE_EXISTING_SERVERS) {
    await expect.poll(async () => pathExists(HANDOFF_PATH)).toBe(false);
  }

  await page.getByRole("button", { name: /Choose folder/ }).click();
  await expect(page.getByLabel("Image or folder path")).toHaveValue(scanDir);
  await page.getByLabel("Precision mode").selectOption("exact_pixel");
  await page.getByRole("button", { name: /Start scan/ }).click();

  await expect(page.locator(".progress-detail", { hasText: /Completed with 1 duplicate group/ })).toBeVisible({
    timeout: 30_000
  });
  const scans = await request.get(`${API_BASE}/scans?limit=10&offset=0`, {
    headers: { "X-AntiDupeImage-Token": API_TOKEN }
  });
  expect(scans.ok()).toBe(true);
  const scanList = (await scans.json()) as { scans: Array<{ id: number }>; total: number };
  const scanId = scanList.scans[0].id;
  expect(scanList.total).toBe(1);

  await expect(page).not.toHaveURL(/bootstrap=|token=/);
  await expect(page.getByRole("button", { name: `Use settings from scan ${scanId}` })).toBeVisible();

  // exact match: the workflow-step buttons use aria-labels that embed this phrase
  await page.getByRole("button", { name: "Review duplicates", exact: true }).click();
  await expect(page.getByRole("tab", { name: /Duplicates/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: /Group 1/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Compare inspector" })).toBeVisible();

  await page.getByRole("tab", { name: "Scan" }).click();

  const reuseButton = page.getByRole("button", { name: `Use settings from scan ${scanId}` });
  await reuseButton.focus();
  await reuseButton.press("Enter");
  await expect(page.locator(".scan-template-notice")).toHaveText(
      `Settings from scan #${scanId} loaded.`
  );
  await expect(page.getByRole("heading", { name: "Scan" })).toBeFocused();
  await expect(page.getByLabel("Image or folder path")).toHaveValue(scanDir);
  await expect(page.getByLabel("Precision mode")).toHaveValue("exact_pixel");
  await expect(page.locator(".global-notice")).toHaveCount(0);

  const scansAfterReuse = await request.get(`${API_BASE}/scans?limit=10&offset=0`, {
    headers: { "X-AntiDupeImage-Token": API_TOKEN }
  });
  expect(scansAfterReuse.ok()).toBe(true);
  expect((await scansAfterReuse.json()) as { total: number }).toMatchObject({ total: 1 });

  await page.getByRole("tab", { name: /Exact matches/ }).click();
  await expect(page.getByRole("button", { name: /Group 1/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Compare inspector" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Move to folder" })).toBeEnabled();

  await page.getByRole("button", { name: "Move to folder" }).click();
  const moveDialog = page.getByRole("dialog");
  await expect(moveDialog).toContainText("Move 1 file(s) to duplicate folder?");
  await expect(moveDialog).toContainText("a_keep.bmp");
  await expect(moveDialog).toContainText("b_duplicate.bmp");
  await expect(moveDialog).toContainText("AntiDupeImage_Duplicates_*");
  await expect(moveDialog).toContainText("does not provide automatic restore");
  await expect(moveDialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await moveDialog.getByRole("button", { name: "Move" }).click();

  await expect(page.locator(".global-notice", { hasText: /Moved 1 file to duplicate folder/ })).toBeVisible({
    timeout: 30_000
  });
  await expect(page.getByText("No duplicate groups")).toBeVisible();

  await expect
    .poll(async () => fileExists(duplicatePath), {
      message: "selected duplicate should no longer exist at the original path"
    })
    .toBe(false);
  await expect.poll(async () => fileExists(keepPath)).toBe(true);
  const duplicateFolders = (await readdir(scanDir)).filter((entry) =>
    entry.startsWith("AntiDupeImage_Duplicates_")
  );
  expect(duplicateFolders).toHaveLength(1);

  const groups = await request.get(`${API_BASE}/scans/${scanId}/group-summaries`, {
    headers: { "X-AntiDupeImage-Token": API_TOKEN }
  });
  expect(groups.ok()).toBe(true);
  expect((await groups.json()) as { total: number }).toMatchObject({ total: 0 });
});

test("shows structural evidence and keeps visual matches unselected at narrow width", async ({
  page
}, testInfo) => {
  const scanDir = testInfo.outputPath("visual-scan-root");
  await mkdir(scanDir, { recursive: true });
  await writeFile(path.join(scanDir, "a_original.bmp"), createPatternBmp(192, 160, 0));
  await writeFile(path.join(scanDir, "b_light-edit.bmp"), createPatternBmp(192, 160, 2));

  await page.route("**/api/dialogs/folder", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ path: scanDir, cancelled: false })
    });
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#token=${encodeURIComponent(API_TOKEN)}`);
  await expect(page).not.toHaveURL(/bootstrap=|token=/);
  await page.getByRole("tab", { name: "Scan" }).click();
  await page.getByRole("button", { name: /Choose folder/ }).click();
  await expect(page.getByLabel("Image or folder path")).toHaveValue(scanDir);
  await expect(page.getByLabel("CLIP check")).toHaveCount(0);
  await page.getByRole("button", { name: "Advanced settings" }).click();
  await expect(page.getByRole("spinbutton", { name: "pHash distance value" })).toHaveValue("10");
  await expect(page.getByRole("spinbutton", { name: "Histogram value" })).toHaveValue("0.90");
  await page.getByRole("button", { name: /Start scan/ }).click();

  await expect(page.locator(".progress-detail", { hasText: /Completed with 1 duplicate group/ })).toBeVisible({
    timeout: 30_000
  });
  await page.getByRole("tab", { name: /Duplicates/ }).click();
  await expect(page.getByRole("heading", { name: "Compare inspector" })).toBeVisible();
  await page.getByText("一致の根拠", { exact: true }).click();
  await expect(page.getByRole("row", { name: /Structure \d+% Passed across 1 candidate/ })).toBeVisible();
  await expect(page.getByText("Visual candidates are never preselected.")).toBeVisible();

  const reviewGeometry = await page.locator(".member-row").first().evaluate((row) => {
    const rowRect = row.getBoundingClientRect();
    const thumbRect = row.querySelector(".member-thumb")?.getBoundingClientRect();
    const metaRect = row.querySelector(".member-meta")?.getBoundingClientRect();
    if (!thumbRect || !metaRect) throw new Error("Review row geometry is incomplete");
    return {
      thumbRight: thumbRect.right,
      metaLeft: metaRect.left,
      rowLeft: rowRect.left,
      rowRight: rowRect.right,
      thumbLeft: thumbRect.left,
      metaRight: metaRect.right
    };
  });
  expect(reviewGeometry.thumbRight).toBeLessThanOrEqual(reviewGeometry.metaLeft);
  expect(reviewGeometry.thumbLeft).toBeGreaterThanOrEqual(reviewGeometry.rowLeft);
  expect(reviewGeometry.metaRight).toBeLessThanOrEqual(reviewGeometry.rowRight);
  await expect
    .poll(() =>
      page.locator(".evidence-table-wrap").evaluate((table) => table.scrollWidth <= table.clientWidth)
    )
    .toBe(true);

  const moveCheckbox = page.getByRole("checkbox", { name: /^Move / });
  await expect(moveCheckbox).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Move to folder" })).toBeDisabled();
  await moveCheckbox.focus();
  await moveCheckbox.press("Space");
  await expect(moveCheckbox).toBeChecked();
  const moveToFolderButton = page.getByRole("button", { name: "Move to folder" });
  await expect(moveToFolderButton).toBeEnabled();
  await moveToFolderButton.click();
  const moveDialog = page.getByRole("dialog");
  await expect(moveDialog).toBeVisible();
  await expect(moveDialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth))
    .toBe(true);
  await moveDialog.getByRole("button", { name: "Cancel" }).click();
  await expect(moveToFolderButton).toBeFocused();
});

test("shows failure details and reuses settings without rerunning", async ({ page, request }, testInfo) => {
  const scanDir = testInfo.outputPath("failure-scan-root");
  await mkdir(scanDir, { recursive: true });
  const brokenPath = path.join(scanDir, "broken.png");
  await writeFile(brokenPath, Buffer.from("not an image", "utf8"));

  await page.route("**/api/dialogs/folder", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ path: scanDir, cancelled: false })
    });
  });

  await page.goto(`/#token=${encodeURIComponent(API_TOKEN)}`);
  await page.getByRole("tab", { name: "Scan" }).click();
  await page.getByRole("button", { name: /Choose folder/ }).click();
  await page.getByRole("button", { name: /Start scan/ }).click();

  await expect(page.locator(".error-box", { hasText: "No images could be decoded successfully." })).toBeVisible({
    timeout: 30_000
  });
  await page.getByRole("tab", { name: /Warnings/ }).click();
  const failurePath = page.getByTitle(brokenPath);
  await expect(failurePath).toBeVisible();
  await failurePath.click();
  await expect(page.getByText("Full error")).toBeVisible();

  const beforeReuse = await request.get(`${API_BASE}/scans?limit=10&offset=0`, {
    headers: { "X-AntiDupeImage-Token": API_TOKEN }
  });
  expect(beforeReuse.ok()).toBe(true);
  expect((await beforeReuse.json()) as { total: number }).toMatchObject({ total: 1 });

  await page.getByRole("button", { name: "Use scan settings" }).click();
  await expect(page.getByRole("tab", { name: "Scan" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Image or folder path")).toHaveValue(scanDir);
  await expect(page.getByRole("button", { name: "Advanced settings" })).toHaveAttribute(
    "aria-expanded",
    "true"
  );

  const afterReuse = await request.get(`${API_BASE}/scans?limit=10&offset=0`, {
    headers: { "X-AntiDupeImage-Token": API_TOKEN }
  });
  expect(afterReuse.ok()).toBe(true);
  expect((await afterReuse.json()) as { total: number }).toMatchObject({ total: 1 });
});

async function fileExists(filePath: string): Promise<boolean> {
  try {
    await readdir(path.dirname(filePath));
    await import("node:fs/promises").then((fs) => fs.access(filePath));
    return true;
  } catch {
    return false;
  }
}

async function pathExists(filePath: string): Promise<boolean> {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

function createSolidBmp(width: number, height: number, rgb: [number, number, number]): Buffer {
  return createBmp(width, height, () => rgb);
}

function createPatternBmp(width: number, height: number, toneShift: number): Buffer {
  return createBmp(width, height, (x, y) => {
    const stripe = Math.floor(x / 30) % 3;
    const insideEllipse = ((x - 96) / 52) ** 2 + ((y - 80) / 46) ** 2 <= 1;
    const base: [number, number, number] = insideEllipse
      ? [35 + stripe * 55, 105 + stripe * 25, 185 - stripe * 35]
      : [218 - stripe * 24, 228 - (Math.floor(y / 28) % 3) * 22, 235 - stripe * 18];
    return base.map((value) => Math.min(255, value + toneShift)) as [number, number, number];
  });
}

function createBmp(
  width: number,
  height: number,
  pixelAt: (x: number, y: number) => [number, number, number]
): Buffer {
  const bytesPerPixel = 3;
  const rowSize = Math.ceil((width * bytesPerPixel) / 4) * 4;
  const pixelArraySize = rowSize * height;
  const fileHeaderSize = 14;
  const dibHeaderSize = 40;
  const pixelOffset = fileHeaderSize + dibHeaderSize;
  const fileSize = pixelOffset + pixelArraySize;
  const buffer = Buffer.alloc(fileSize);

  buffer.write("BM", 0, "ascii");
  buffer.writeUInt32LE(fileSize, 2);
  buffer.writeUInt32LE(pixelOffset, 10);
  buffer.writeUInt32LE(dibHeaderSize, 14);
  buffer.writeInt32LE(width, 18);
  buffer.writeInt32LE(height, 22);
  buffer.writeUInt16LE(1, 26);
  buffer.writeUInt16LE(24, 28);
  buffer.writeUInt32LE(pixelArraySize, 34);

  for (let y = 0; y < height; y += 1) {
    const rowStart = pixelOffset + y * rowSize;
    for (let x = 0; x < width; x += 1) {
      const pixelStart = rowStart + x * bytesPerPixel;
      const [red, green, blue] = pixelAt(x, y);
      buffer[pixelStart] = blue;
      buffer[pixelStart + 1] = green;
      buffer[pixelStart + 2] = red;
    }
  }
  return buffer;
}
