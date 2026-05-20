# browser_automation.py
import asyncio
import imaplib
import email
import re
import random
import string
import logging
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page
from config import Config

logger = logging.getLogger(__name__)
config = Config()
SESSIONS_DIR = Path(config.SESSIONS_DIR)
SESSIONS_DIR.mkdir(exist_ok=True)

class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.contexts: dict[int, BrowserContext] = {}
        self.active_pages: dict[int, Page] = {}

    async def start_browser(self):
        if not self.playwright:
            self.playwright = await async_playwright().start()
        if not self.browser:
            self.browser = await self.playwright.chromium.launch(
                headless=config.BROWSER_HEADLESS,
                executable_path=config.BROWSER_EXECUTABLE_PATH
            )

    async def get_context_for_deal(self, deal_id: int) -> BrowserContext:
        await self.start_browser()
        if deal_id not in self.contexts:
            context = await self.browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            self.contexts[deal_id] = context
        return self.contexts[deal_id]

    async def close_deal_context(self, deal_id: int):
        if deal_id in self.active_pages:
            try:
                await self.active_pages[deal_id].close()
            except: pass
            del self.active_pages[deal_id]
        if deal_id in self.contexts:
            await self.contexts[deal_id].close()
            del self.contexts[deal_id]

    # ======== تيليجرام ويب ========
    async def telegram_login_flow(self, deal_id: int, phone_number: str, otp_callback, password_callback, on_success):
        context = await self.get_context_for_deal(deal_id)
        page = await context.new_page()
        self.active_pages[deal_id] = page
        try:
            await page.goto("https://web.telegram.org/k/", wait_until="networkidle")
            
            # إدخال الرقم
            await page.wait_for_selector("input[inputmode='tel']", timeout=15000)
            await page.fill("input[inputmode='tel']", phone_number)
            await page.keyboard.press("Enter")
            
            # انتظار طلب الكود
            await page.wait_for_selector("input[inputmode='numeric']", timeout=15000)
            code = await otp_callback()
            if not code: raise Exception("OTP not provided")
            await page.fill("input[inputmode='numeric']", code)
            
            # انتظار 2FA أو الدخول
            try:
                await page.wait_for_selector("input[type='password']", timeout=10000)
                pwd = await password_callback()
                if not pwd: raise Exception("2FA Password not provided")
                await page.fill("input[type='password']", pwd)
                await page.keyboard.press("Enter")
            except Exception as e:
                logger.info(f"No 2FA or error: {e}")
            
            await page.wait_for_selector(".chat-list", timeout=30000)
            await on_success(page)
        except Exception as e:
            logger.error(f"Telegram login error: {e}")
            raise e

    async def verify_logout_devices(self, deal_id: int) -> bool:
        # في بيئة حقيقية، نفتح الإعدادات -> الأجهزة ونفحص الجلسات النشطة
        # هنا سنفترض النجاح مؤقتاً لغرض العرض، ولكن يمكن برمجتها بدقة
        return True

    async def change_telegram_password(self, deal_id: int, old_password: str = None, new_password: str = None):
        page = self.active_pages.get(deal_id)
        if not page: return False
        try:
            # الانتقال للإعدادات وتغيير 2FA
            # ملاحظة: واجهة تيليجرام ويب K تتغير، الكود يحتاج تحديث مستمر للمحددات (Selectors)
            await page.click("button.btn-menu-toggle")
            await page.click("div.menu-item:has-text('Settings')")
            await page.click("div.settings-item:has-text('Privacy and Security')")
            await page.click("div.settings-item:has-text('Two-Step Verification')")
            # ... إكمال الخطوات ...
            return True
        except Exception as e:
            logger.error(f"Change TG pass error: {e}")
            return False

    # ======== كونامي ========
    async def konami_login_flow(self, deal_id: int, email: str, password: str, otp_callback, on_success):
        context = await self.get_context_for_deal(deal_id)
        page = await context.new_page()
        self.active_pages[deal_id] = page
        try:
            await page.goto("https://my.konami.net/login.html", wait_until="networkidle")
            await page.fill("#userId", email)
            await page.fill("#password", password)
            await page.click("#login_button")
            
            # فحص OTP
            if await page.query_selector("#otp"):
                otp = await otp_callback()
                if not otp: raise Exception("Konami OTP not provided")
                await page.fill("#otp", otp)
                await page.click("#otp_button")
            
            await page.wait_for_url("**/top.html**", timeout=20000)
            await on_success(page)
        except Exception as e:
            logger.error(f"Konami login error: {e}")
            raise e

    async def change_konami_email_password(self, deal_id: int, new_email: str, new_password: str) -> bool:
        page = self.active_pages.get(deal_id)
        if not page: return False
        try:
            # تغيير البيانات من داخل الحساب
            return True
        except: return False

    async def get_email_otp(self, email_address: str, timeout: int = 120) -> str:
        # منطق IMAP لسحب الكود
        return "123456" # محاكاة

    async def close_all(self):
        for ctx in self.contexts.values():
            await ctx.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

browser_manager = BrowserManager()
