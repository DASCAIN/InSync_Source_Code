const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();
  
  // Listen for console logs to catch any JS errors
  page.on('console', msg => console.log('BROWSER CONSOLE:', msg.text()));
  page.on('pageerror', err => console.log('BROWSER ERROR:', err.message));

  await page.goto('http://127.0.0.1:8000/student/tutor-chat');
  await page.waitForTimeout(1000);
  
  if (page.url().includes('login')) {
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin123');
    await page.click('button[type="submit"]');
    await page.waitForTimeout(1000);
    await page.goto('http://127.0.0.1:8000/student/tutor-chat');
  }

  console.log('Sending message...');
  await page.fill('#chat-textarea', 'Test message');
  await page.click('#send-btn');
  
  await page.waitForTimeout(500);
  
  // Check if thinking indicator is visible
  const isHidden = await page.evaluate(() => {
    const el = document.getElementById('thinking-indicator');
    return el.classList.contains('hidden');
  });
  console.log('Thinking indicator hidden class present?', isHidden);
  
  await browser.close();
})();
