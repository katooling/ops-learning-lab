import { expect } from "@playwright/test";


export async function tabTo(page, locator, limit = 100) {
  for (let attempt = 0; attempt < limit; attempt += 1) {
    if (await locator.evaluate((element) => element === document.activeElement)) {
      return;
    }
    await page.keyboard.press("Tab");
  }
  throw new Error("keyboard focus did not reach the expected control");
}


export async function chooseWithKeyboard(page, locator) {
  await tabTo(page, locator);
  await page.keyboard.press("Space");
  await expect(locator).toBeChecked();
}
