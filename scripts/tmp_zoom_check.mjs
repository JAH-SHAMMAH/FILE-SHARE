import { chromium } from 'playwright';

const url = 'http://127.0.0.1:8000/presentations/2';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1365, height: 768 } });

page.on('console', (msg) => {
  console.log('[browser]', msg.type(), msg.text());
});

await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(1200);

const resultBefore = await page.evaluate(() => {
  const zoomLevel = document.getElementById('zoom-level')?.textContent?.trim() || null;
  const zoomContent = document.querySelector('#presentation-main-viewer .presentation-zoom-content');
  const slideImg = document.querySelector('#presentation-main-viewer .slide-image, #presentation-main-viewer img, #presentation-main-viewer canvas');
  const zcStyle = zoomContent ? getComputedStyle(zoomContent) : null;
  const imgStyle = slideImg ? getComputedStyle(slideImg) : null;
  return {
    zoomLevel,
    zoomContentTransform: zcStyle?.transform || null,
    slideTransform: imgStyle?.transform || null,
    slideWidth: imgStyle?.width || null,
  };
});

await page.click('#zoom-in');
await page.waitForTimeout(500);

const resultAfterIn = await page.evaluate(() => {
  const zoomLevel = document.getElementById('zoom-level')?.textContent?.trim() || null;
  const zoomContent = document.querySelector('#presentation-main-viewer .presentation-zoom-content');
  const slideImg = document.querySelector('#presentation-main-viewer .slide-image, #presentation-main-viewer img, #presentation-main-viewer canvas');
  const zcStyle = zoomContent ? getComputedStyle(zoomContent) : null;
  const imgStyle = slideImg ? getComputedStyle(slideImg) : null;
  return {
    zoomLevel,
    zoomContentTransform: zcStyle?.transform || null,
    slideTransform: imgStyle?.transform || null,
    slideWidth: imgStyle?.width || null,
  };
});

await page.click('#zoom-out');
await page.waitForTimeout(500);

const resultAfterOut = await page.evaluate(() => {
  const zoomLevel = document.getElementById('zoom-level')?.textContent?.trim() || null;
  const zoomContent = document.querySelector('#presentation-main-viewer .presentation-zoom-content');
  const slideImg = document.querySelector('#presentation-main-viewer .slide-image, #presentation-main-viewer img, #presentation-main-viewer canvas');
  const zcStyle = zoomContent ? getComputedStyle(zoomContent) : null;
  const imgStyle = slideImg ? getComputedStyle(slideImg) : null;
  return {
    zoomLevel,
    zoomContentTransform: zcStyle?.transform || null,
    slideTransform: imgStyle?.transform || null,
    slideWidth: imgStyle?.width || null,
  };
});

console.log('BEFORE', JSON.stringify(resultBefore, null, 2));
console.log('AFTER_IN', JSON.stringify(resultAfterIn, null, 2));
console.log('AFTER_OUT', JSON.stringify(resultAfterOut, null, 2));

await page.screenshot({ path: 'SLIDESHARE/scripts/tmp_zoom_check.png', fullPage: false });
await browser.close();
