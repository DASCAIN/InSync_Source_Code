const { test, expect } = require('@playwright/test');
test('test chat', async ({ page }) => {
  await page.goto('http://127.0.0.1:8000/student/tutor-chat');
  await page.waitForTimeout(1000);
  // Log in if redirected
  if (page.url().includes('login')) {
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin123');
    await page.click('button[type="submit"]');
    await page.waitForTimeout(1000);
    await page.goto('http://127.0.0.1:8000/student/tutor-chat');
  }
  await page.fill('#chat-textarea', 'Hello, how are you?');
  await page.click('#send-btn');
  await page.waitForTimeout(500); // Wait 0.5s to see the thinking indicator
  await page.screenshot({ path: 'chat_thinking.png' });
});
