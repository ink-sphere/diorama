import { expect, test } from "@playwright/test";

test("read a local book, navigate, adjust type and resume", async ({ page, isMobile }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your library" })).toBeVisible();
  const book = page.locator(".book-tile").first();
  await expect(book).toBeVisible();
  const cover = book.locator("img");
  if (await cover.count()) {
    await expect.poll(() => cover.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  }
  await page.screenshot({ path: testInfo.outputPath("library.png"), fullPage: true });
  await book.click();
  const frame = page.locator("iframe.book-frame").first();
  await expect(frame).toBeVisible();
  await expect(frame.contentFrame().locator("body")).not.toBeEmpty();
  await page.screenshot({ path: testInfo.outputPath("reader.png") });
  if (isMobile) await page.getByRole("button", { name: "Toggle contents" }).click();
  const nav = isMobile ? page.locator("dialog[open]") : page.locator(".reader-sidebar");
  const entries = nav.locator(".toc-label");
  await expect(entries.first()).toBeVisible();
  await entries.last().click();
  await expect(page).toHaveURL(/section=/);
  if (isMobile) await expect(page.locator("dialog")).not.toBeVisible();
  await expect(page.locator(".frame-wrap.is-loaded")).toBeVisible();
  await page.getByRole("button", { name: "Increase text size" }).click();
  await expect(page.locator("iframe").first().contentFrame().locator("body")).toHaveCSS("font-size", "22px");
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: testInfo.outputPath("reader-dark.png") });
  await page.mouse.wheel(0, 350); await page.waitForTimeout(300);
  const url = page.url();
  await page.getByRole("link", { name: "Back to library" }).click();
  await expect(page.getByText("Continue reading").first()).toBeVisible();
  await page.locator(".book-tile").first().click();
  await expect(page).toHaveURL(url);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("nested contents and footnote links", async ({ page, isMobile }) => {
  test.skip(!process.env.DIORAMA_E2E_LIBRARY, "Set DIORAMA_E2E_LIBRARY to the synthetic fixture library");
  await page.goto("/books/nested?section=content%2F0");
  await expect(page.locator(".frame-wrap.is-loaded")).toBeVisible();
  if (isMobile) await page.getByRole("button", { name: "Toggle contents" }).click();
  const nav = isMobile ? page.locator("dialog[open]") : page.locator(".reader-sidebar");
  for (const title of ["Act I", "The orchard", "Adhyāya"]) {
    const expand = nav.getByRole("button", { name: `Expand ${title}`, exact: true });
    if (await expand.count()) await expand.click();
  }
  await nav.locator(".toc-label").filter({ hasText: "Śloka" }).click();
  await expect(page).toHaveURL(/sub-sections/);
  await page.locator("iframe").first().contentFrame().getByRole("link", { name: "Read note" }).click();
  await expect(page).toHaveURL(/back_matter/);
  await expect(page.locator("iframe").first().contentFrame().locator("#note")).toBeVisible();
});
