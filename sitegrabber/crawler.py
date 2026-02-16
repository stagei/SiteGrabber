"""Core crawl engine - BFS queue, deduplication, rate limiting, retry logic."""

import sys
import time
from collections import deque

import requests
from bs4 import BeautifulSoup

from .config import CrawlConfig
from .url_resolver import resolve_url, is_in_scope
from .html_filter import filter_html, extract_links
from .file_saver import url_to_filepath, pdf_url_to_filepath, save_page, save_binary, file_exists


def _flush() -> None:
    """Flush stdout so output appears immediately in piped/buffered contexts."""
    sys.stdout.flush()


class Crawler:
    """Recursive website crawler with BFS traversal.

    Downloads pages starting from input_address, extracts links from
    (optionally filtered) HTML content, and saves each page to the
    local filesystem.

    Supports two download modes:
    - requests (default): Fast, lightweight HTTP client for static HTML sites.
    - browser (--browser): Headless Chromium via Playwright for JS-rendered SPAs.

    When using browser mode on SPA sites (e.g. IBM Docs), the crawler
    automatically expands the sidebar TOC tree on the first page to
    discover all topic URLs before crawling individual pages.
    """

    def __init__(self, config: CrawlConfig):
        self.config = config
        self.queue: deque[str] = deque()
        self.visited: set[str] = set()
        self.queued: set[str] = set()   # All URLs ever added to the queue (dedup)
        self.failed: dict[str, str] = {}
        self.saved_count: int = 0
        self._toc_expanded: bool = False  # True after TOC tree expansion on root page

        # HTTP session for non-browser mode
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,nb;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })

        # Playwright browser (lazy init)
        self._playwright = None
        self._browser = None
        self._browser_context = None
        self._page = None

    def crawl(self) -> None:
        """Run the crawl process.

        Starts from config.input_address and recursively follows in-scope
        links until the queue is empty or max_pages is reached.
        """
        input_url = self.config.input_address.rstrip("/")
        self.queue.append(input_url)
        self.queued.add(input_url)

        mode = "Browser (Playwright/Chromium)" if self.config.browser else "HTTP (requests)"

        print("=" * 70)
        print(f"  SiteGrabber - Starting crawl")
        print(f"  URL:    {input_url}")
        print(f"  Output: {self.config.output_folder}")
        print(f"  Mode:   {mode}")
        if self.config.limitation_text:
            ltype = self.config.limitation_type or "(any attribute)"
            print(f"  Filter: {ltype} = '{self.config.limitation_text}'")
        print(f"  Content:   {self.config.content_types}")
        print(f"  Recursive: {self.config.recursive}")
        print(f"  Delay: {self.config.delay}s | Timeout: {self.config.timeout}s")
        if self.config.browser:
            print(f"  Extra wait: {self.config.extra_wait}s")
        if self.config.login_url:
            print(f"  Login URL: {self.config.login_url}")
            print(f"  Login user: {self.config.login_email or '(not set)'}")
        if self.config.max_pages > 0:
            print(f"  Max pages: {self.config.max_pages}")
        if self.config.resume:
            print(f"  Resume mode: ON (skipping existing files)")
        print("=" * 70)
        print()
        _flush()

        # Initialize browser if needed
        if self.config.browser:
            self._init_browser()

            # Perform login before crawling if credentials are provided
            if self.config.login_url and self.config.login_email and self.config.login_password:
                self._browser_login()
                # Transfer browser cookies to the requests session so
                # subsequent pages can be fetched via fast HTTP instead
                # of slow browser navigation.
                self._transfer_browser_cookies()

        try:
            while self.queue:
                # Check max pages limit
                if self.config.max_pages > 0 and self.saved_count >= self.config.max_pages:
                    print(f"\n[LIMIT] Reached max pages limit ({self.config.max_pages}). Stopping.")
                    break

                url = self.queue.popleft()

                if url in self.visited:
                    continue

                self.visited.add(url)
                self._process_url(url)

                # Rate limiting between requests
                if self.queue and self.config.delay > 0:
                    time.sleep(self.config.delay)
        finally:
            self._cleanup_browser()

        self._print_summary()

    def _init_browser(self) -> None:
        """Initialize Playwright Chromium browser.

        When login credentials are provided, the browser launches in visible
        (headed) mode so the site's bot-detection doesn't block the login form.
        Otherwise it runs headless for speed.
        """
        try:
            from playwright.sync_api import sync_playwright
            needs_login = bool(self.config.login_url and self.config.login_email)
            headless = not needs_login
            mode_label = "headed (login)" if needs_login else "headless"
            print(f"[BROWSER] Starting Chromium ({mode_label})...")
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._browser_context = self._browser.new_context(
                user_agent=self.config.user_agent.replace("SiteGrabber/1.0", "").strip(),
                viewport={"width": 1920, "height": 1080},
            )
            # Mask the webdriver property so sites don't detect automation
            self._browser_context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            self._page = self._browser_context.new_page()
            print("[BROWSER] Ready.\n")
        except ImportError:
            print("[ERROR] Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
            raise SystemExit(1)
        except Exception as e:
            print(f"[ERROR] Failed to start browser: {e}")
            raise SystemExit(1)

    def _cleanup_browser(self) -> None:
        """Close Playwright browser and resources."""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
        if self._browser_context:
            try:
                self._browser_context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass

    def _browser_login(self) -> None:
        """Authenticate via the browser before crawling.

        Navigates to config.login_url, fills in email/password fields, and
        submits the form.  Waits for the login to complete by checking that
        the URL has changed away from the login page.

        Common login form patterns are tried in order:
          1. input[type=email] / input[name*=email] / input[name*=user]
          2. input[type=password]
          3. button[type=submit] / input[type=submit] / button with "login"/"sign in" text
        """
        if self._page is None:
            return

        login_url = self.config.login_url
        email = self.config.login_email
        password = self.config.login_password

        print(f"[LOGIN] Navigating to login page: {login_url}")
        _flush()

        try:
            self._page.goto(login_url, wait_until="load", timeout=self.config.timeout * 1000)
            time.sleep(5.0)

            # Best-effort wait for full page render
            try:
                self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            # Log how many inputs are on the page for diagnostics
            all_inputs = self._page.query_selector_all("input")
            print(f"[LOGIN] Page loaded. Found {len(all_inputs)} input element(s).")
            _flush()

            # --- Dismiss cookie consent overlays ---
            cookie_selectors = [
                'button:has-text("Accept All Cookies")',
                'button:has-text("Accept All")',
                'button:has-text("Allow All")',
                'button:has-text("Accept")',
                '#onetrust-accept-btn-handler',
            ]
            for sel in cookie_selectors:
                try:
                    btn = self._page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        print("[LOGIN] Dismissed cookie consent dialog.")
                        _flush()
                        time.sleep(1.5)
                        break
                except Exception:
                    continue

            # --- Find and fill email/username field ---
            email_selectors = [
                'input[name="email"]',
                'input[type="email"]',
                'input[id*="email"]',
                'input[autocomplete="email"]',
                'input[name="username"]',
                'input[id*="user"]',
                'input[autocomplete="username"]',
                'input[name*="email"]',
                'input[name*="user"]',
                'input[placeholder*="email" i]',
                'input[placeholder*="user" i]',
            ]
            email_field = None

            # Wait for the login form to appear (SPA may render it late)
            for sel in email_selectors:
                try:
                    self._page.wait_for_selector(sel, timeout=3000)
                    email_field = self._page.query_selector(sel)
                    if email_field:
                        print(f"[LOGIN] Found email field: {sel}")
                        _flush()
                        break
                except Exception:
                    continue

            if not email_field:
                # Fallback: try any visible text input
                print("[LOGIN] Named selectors failed. Trying first visible text input.")
                _flush()
                for inp in self._page.query_selector_all('input[type="text"]'):
                    if inp.is_visible():
                        email_field = inp
                        break

            if email_field:
                email_field.click()
                email_field.fill(email)
                print(f"[LOGIN] Filled email: {email}")
                _flush()
            else:
                print("[LOGIN] ERROR: No email/username input found on login page.")
                _flush()
                return

            # --- Find and fill password field ---
            pw_field = self._page.query_selector('input[type="password"]')
            if pw_field:
                pw_field.click()
                pw_field.fill(password)
                print("[LOGIN] Filled password: ****")
                _flush()
            else:
                print("[LOGIN] ERROR: No password input found on login page.")
                _flush()
                return

            # --- Find and click submit button ---
            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Log in")',
                'button:has-text("Login")',
                'button:has-text("Sign in")',
                'button:has-text("Sign In")',
                'button:has-text("Submit")',
                '[role="button"]:has-text("Log in")',
                '[role="button"]:has-text("Login")',
            ]
            submit_btn = None
            for sel in submit_selectors:
                submit_btn = self._page.query_selector(sel)
                if submit_btn:
                    break

            if submit_btn:
                submit_btn.click()
                print("[LOGIN] Clicked submit button.")
                _flush()
            else:
                # Fallback: press Enter on the password field
                pw_field.press("Enter")
                print("[LOGIN] No submit button found; pressed Enter on password field.")
                _flush()

            # --- Wait for login to complete ---
            # Wait for navigation away from the login URL
            time.sleep(3.0)
            try:
                self._page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass

            current_url = self._page.url
            if "login" in current_url.lower() or "auth" in current_url.lower():
                # Still on login page - wait a bit more
                time.sleep(5.0)
                current_url = self._page.url

            login_path = "/login" in current_url.lower() or "/signin" in current_url.lower()
            if login_path:
                print(f"[LOGIN] WARNING: May still be on login page: {current_url}")
                print("[LOGIN] Proceeding anyway - authentication cookies may have been set.")
                _flush()
            else:
                print(f"[LOGIN] Success! Redirected to: {current_url}")
                _flush()

        except Exception as e:
            print(f"[LOGIN] ERROR: Login failed: {e}")
            _flush()

    def _transfer_browser_cookies(self) -> None:
        """Copy cookies from the Playwright browser context to the requests session.

        This allows subsequent pages to be fetched via fast HTTP (requests)
        instead of slow browser navigation, while keeping the authenticated state.
        """
        if self._browser_context is None:
            return

        cookies = self._browser_context.cookies()
        for c in cookies:
            self.session.cookies.set(
                name=c["name"],
                value=c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )
        print(f"[AUTH] Transferred {len(cookies)} cookie(s) to HTTP session.")
        _flush()

    def _grab_toc_links(self, html: str) -> list[str]:
        """Extract all hrefs from the TOC container in the rendered HTML.

        Instead of clicking/expanding TOC nodes one by one (slow), this grabs
        the container's innerHTML and uses regex to find all href attributes.
        SPA TOC trees typically have all links in the DOM even when visually
        collapsed -- they are just hidden via CSS.

        Returns:
            List of raw href strings found inside the container.
        """
        import re

        if not self.config.limitation_type or not self.config.limitation_text:
            return []

        attr = self.config.limitation_type
        val = self.config.limitation_text

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Find the container using BeautifulSoup (handles multi-class)
        container = soup.find("div", attrs={attr: lambda v: v and val in (v if isinstance(v, list) else v.split())})
        if not container:
            # Fallback: try any element with the attribute
            container = soup.find(attrs={attr: lambda v: v and val in (v if isinstance(v, list) else v.split())})

        if not container:
            print(f"  [TOC] Container not found for {attr}='{val}' in HTML")
            _flush()
            return []

        container_html = str(container)
        # Regex: find all href="..." values in the container HTML
        # Match group 1: the href value (single or double quotes)
        #   href=       - literal href=
        #   ["']        - opening quote
        #   ([^"']+)    - capture group: one or more chars that aren't quotes
        #   ["']        - closing quote
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', container_html)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for h in hrefs:
            if h not in seen and not h.startswith(("#", "javascript:", "mailto:")):
                seen.add(h)
                unique.append(h)

        print(f"  [TOC] Grabbed {len(unique)} unique link(s) from container ({attr}='{val}')")
        _flush()
        return unique

    def _expand_toc_tree(self) -> None:
        """Legacy TOC expansion via clicking -- only used when no TOC links
        were found by _grab_toc_links (fallback for sites that truly lazy-load).

        Uses limitation_type/limitation_text to locate the container, then
        clicks all [aria-expanded="false"] nodes to reveal hidden links.
        """
        if self._page is None:
            return
        if not self.config.limitation_type or not self.config.limitation_text:
            return

        attr = self.config.limitation_type
        val = self.config.limitation_text
        if attr == "class":
            container_sel = f'div[{attr}~="{val}"]'
        else:
            container_sel = f'div[{attr}="{val}"]'

        container = self._page.query_selector(container_sel)
        if not container:
            print(f"  [TOC] Container not found: {container_sel}")
            _flush()
            return

        print(f"  [TOC] Falling back to click-expansion for: {container_sel}")
        _flush()

        max_rounds = 80
        total_expanded = 0

        for round_num in range(1, max_rounds + 1):
            container = self._page.query_selector(container_sel)
            if not container:
                break

            collapsed = container.query_selector_all('[aria-expanded="false"]')
            if not collapsed:
                break

            expanded_this_round = 0
            for node in collapsed:
                try:
                    node.scroll_into_view_if_needed(timeout=2000)
                    node.click(timeout=2000)
                    expanded_this_round += 1
                    time.sleep(0.3)
                except Exception:
                    continue

            total_expanded += expanded_this_round
            if expanded_this_round == 0:
                break

            time.sleep(1.5)
            print(f"  [TOC] Round {round_num}: expanded {expanded_this_round} nodes "
                  f"(total: {total_expanded})")
            _flush()

        time.sleep(2.0)
        print(f"  [TOC] Click-expansion complete. {total_expanded} nodes expanded.")
        _flush()

    def _is_pdf_url(self, url: str) -> bool:
        """Check if a URL points to a PDF file."""
        from urllib.parse import urlparse
        return urlparse(url).path.lower().endswith(".pdf")

    def _process_url(self, url: str) -> None:
        """Download, save, and extract links from a single URL.

        PDF URLs are downloaded as binary and saved without link extraction.
        HTML URLs are downloaded as text, saved, and optionally parsed for links.

        Args:
            url: Fully resolved URL to process.
        """
        queue_size = len(self.queue)
        visited_count = len(self.visited)
        is_pdf = self._is_pdf_url(url)
        label = "PDF" if is_pdf else "HTML"
        print(f"[{self.saved_count + 1}] Downloading {label} (queue: {queue_size}, visited: {visited_count})")
        print(f"  {url}")
        _flush()

        # Route to PDF or HTML handler
        if is_pdf:
            self._process_pdf_url(url)
        else:
            self._process_html_url(url)

    def _process_pdf_url(self, url: str) -> None:
        """Download and save a PDF file. No link extraction.

        Args:
            url: Fully resolved PDF URL.
        """
        filepath = pdf_url_to_filepath(url, self.config.output_folder)

        # Resume support
        if self.config.resume and file_exists(filepath):
            print(f"  [SKIP] Already exists: {filepath}")
            self.saved_count += 1
            return

        pdf_bytes = self._download_binary(url)
        if pdf_bytes is None:
            return

        if save_binary(filepath, pdf_bytes):
            self.saved_count += 1
            size_mb = len(pdf_bytes) / (1024 * 1024)
            print(f"  [SAVED] {filepath} ({size_mb:.1f} MB)")
            _flush()

    def _process_html_url(self, url: str) -> None:
        """Download, save, and extract links from an HTML page.

        Args:
            url: Fully resolved HTML URL.
        """
        filepath = url_to_filepath(url, self.config.input_address, self.config.output_folder)

        # Resume support: skip if file already exists
        if self.config.resume and file_exists(filepath):
            print(f"  [SKIP] Already exists: {filepath}")
            self.saved_count += 1

            # Even when resuming, we need to parse existing file for links
            if self.config.recursive:
                self._extract_links_from_file(filepath, url)
            return

        # Download the page.
        # When login credentials are provided, keep using the browser for all
        # pages because auth tokens may not transfer cleanly to requests.
        # Otherwise, use the browser only for the first page (TOC grab),
        # then switch to fast HTTP for the rest.
        has_login = bool(self.config.login_url and self.config.login_email)
        if self.config.browser and (has_login or not self._toc_expanded):
            html = self._download_browser(url)
        elif self.config.browser and not has_login and self._toc_expanded:
            # No login needed -- cookies transferred, use fast HTTP
            html = self._download(url)
        else:
            html = self._download(url)

        if html is None:
            return

        # Save to disk
        if save_page(filepath, html):
            self.saved_count += 1
            print(f"  [SAVED] {filepath}")
            _flush()
        else:
            return

        # Extract and queue new links:
        # - Recursive mode: always extract links
        # - Non-recursive with pdf/all: still extract PDF links from this page
        if self.config.recursive or self.config.content_types in ("pdf", "all"):
            self._extract_and_queue_links(html, url)

    def _download_browser(self, url: str) -> str | None:
        """Download a URL using headless Chromium, returning the fully rendered HTML.

        Waits for the page to finish rendering (JS execution, network idle)
        before extracting the DOM content.

        Args:
            url: URL to download.

        Returns:
            Rendered HTML content as string, or None on failure.
        """
        max_retries = 3
        backoff = 2.0

        for attempt in range(1, max_retries + 1):
            try:
                response = self._page.goto(
                    url,
                    wait_until=self.config.wait_for,
                    timeout=self.config.timeout * 1000,  # Playwright uses ms
                )

                if response is None:
                    print(f"  [WARN] No response from browser for: {url}")
                    return None

                status = response.status
                if status == 404:
                    print(f"  [404] Not found: {url}")
                    self.failed[url] = "HTTP 404"
                    return None
                elif status == 429:
                    wait = backoff * 5
                    print(f"  [429] Rate limited. Waiting {wait:.0f}s...")
                    time.sleep(wait)
                    backoff *= 2
                    continue
                elif status >= 400:
                    if attempt < max_retries:
                        print(f"  [HTTP {status}] Retry {attempt}/{max_retries} in {backoff:.0f}s...")
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    else:
                        print(f"  [FAIL] HTTP {status} after {max_retries} attempts: {url}")
                        self.failed[url] = f"HTTP {status}"
                        return None

                # Extra wait for JS to render dynamic content (SPA TOC trees, etc.)
                # Only use full extra_wait on the first page; subsequent pages
                # just need the DOM to load (much faster).
                is_input = url.rstrip("/") == self.config.input_address.rstrip("/")
                if self.config.extra_wait > 0 and not self._toc_expanded:
                    time.sleep(self.config.extra_wait)
                elif self._toc_expanded:
                    time.sleep(min(self.config.extra_wait, 1.0))

                # Best-effort secondary networkidle wait (short timeout)
                try:
                    self._page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass  # Best effort - proceed with what we have

                # Get the fully rendered DOM (not the initial HTML source)
                html = self._page.content()

                # On the original input address, grab TOC links directly
                # from the rendered HTML (fast regex) instead of clicking nodes
                is_input = url.rstrip("/") == self.config.input_address.rstrip("/")
                if is_input and not self._toc_expanded:
                    toc_hrefs = self._grab_toc_links(html)
                    if toc_hrefs:
                        # Queue all TOC links immediately
                        for href in toc_hrefs:
                            resolved = resolve_url(url, href)
                            if resolved and resolved not in self.queued:
                                if is_in_scope(resolved, self.config.input_address):
                                    self.queue.append(resolved)
                                    self.queued.add(resolved)
                                    print(f"    + {resolved}")
                                    _flush()
                        print(f"  [TOC] Queued {len(self.queue)} URLs from TOC")
                        _flush()
                    else:
                        # Fallback: click-expand if no links found in static DOM
                        self._expand_toc_tree()
                    self._toc_expanded = True

                return html

            except Exception as e:
                error_str = str(e)
                if "Timeout" in error_str and attempt < max_retries:
                    print(f"  [TIMEOUT] Retry {attempt}/{max_retries} in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                elif attempt < max_retries:
                    print(f"  [ERROR] {error_str}. Retry {attempt}/{max_retries} in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    print(f"  [FAIL] After {max_retries} attempts: {error_str}")
                    self.failed[url] = error_str[:100]
                    return None

        return None

    def _download(self, url: str) -> str | None:
        """Download a URL with retry logic and exponential backoff.

        Args:
            url: URL to download.

        Returns:
            HTML content as string, or None if all retries failed.
        """
        max_retries = 3
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.config.timeout,
                    allow_redirects=True,
                )

                # Check content type - only process HTML
                content_type = response.headers.get("Content-Type", "")
                if not any(ct in content_type.lower() for ct in ("text/html", "application/xhtml")):
                    if self.config.verbose:
                        print(f"  [SKIP] Non-HTML content: {content_type}")
                    return None

                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
                return response.text

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                if status == 404:
                    print(f"  [404] Not found: {url}")
                    self.failed[url] = f"HTTP {status}"
                    return None
                elif status == 429:
                    # Rate limited - wait longer
                    wait = backoff * 5
                    print(f"  [429] Rate limited. Waiting {wait:.0f}s...")
                    time.sleep(wait)
                    backoff *= 2
                    continue
                elif attempt < max_retries:
                    print(f"  [HTTP {status}] Retry {attempt}/{max_retries} in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    print(f"  [FAIL] HTTP {status} after {max_retries} attempts: {url}")
                    self.failed[url] = f"HTTP {status}"
                    return None

            except requests.exceptions.ConnectionError:
                if attempt < max_retries:
                    print(f"  [CONN] Connection error. Retry {attempt}/{max_retries} in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    print(f"  [FAIL] Connection error after {max_retries} attempts: {url}")
                    self.failed[url] = "Connection error"
                    return None

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    print(f"  [TIMEOUT] Retry {attempt}/{max_retries} in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    print(f"  [FAIL] Timeout after {max_retries} attempts: {url}")
                    self.failed[url] = "Timeout"
                    return None

            except requests.exceptions.RequestException as e:
                print(f"  [ERROR] {e}")
                self.failed[url] = str(e)
                return None

        return None

    def _download_binary(self, url: str) -> bytes | None:
        """Download a URL as raw bytes (for PDF/binary files).

        Args:
            url: URL to download.

        Returns:
            Raw bytes, or None on failure.
        """
        max_retries = 3
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.config.timeout,
                    allow_redirects=True,
                    stream=True,
                )
                response.raise_for_status()
                return response.content

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                if status == 404:
                    print(f"  [404] Not found: {url}")
                    self.failed[url] = f"HTTP {status}"
                    return None
                elif attempt < max_retries:
                    print(f"  [HTTP {status}] Retry {attempt}/{max_retries} in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    print(f"  [FAIL] HTTP {status} after {max_retries} attempts: {url}")
                    self.failed[url] = f"HTTP {status}"
                    return None

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if attempt < max_retries:
                    print(f"  [RETRY] Attempt {attempt}/{max_retries} in {backoff:.0f}s...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    print(f"  [FAIL] After {max_retries} attempts: {url}")
                    self.failed[url] = "Connection/Timeout"
                    return None

            except requests.exceptions.RequestException as e:
                print(f"  [ERROR] {e}")
                self.failed[url] = str(e)
                return None

        return None

    def _extract_and_queue_links(self, html: str, page_url: str) -> None:
        """Parse HTML for links and add new in-scope ones to queue.

        On the input page, uses the BeautifulSoup filter (limitation_type/text).
        On all other pages, uses fast regex to find href attributes in the
        full HTML source -- no DOM parsing overhead.

        Args:
            html: Raw HTML content.
            page_url: The URL this HTML was downloaded from (for resolving relative links).
        """
        import re

        is_input_page = page_url.rstrip("/") == self.config.input_address.rstrip("/")

        if is_input_page and (self.config.limitation_type or self.config.limitation_text):
            # First page: use BeautifulSoup with filter
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception:
                soup = BeautifulSoup(html, "html.parser")

            filtered_elements = filter_html(
                soup,
                limitation_type=self.config.limitation_type,
                limitation_text=self.config.limitation_text,
            )
            hrefs = extract_links(filtered_elements, content_types=self.config.content_types)
        else:
            # Subsequent pages: fast regex search on raw HTML
            # Regex: href="..." or href='...'
            #   href=       - literal href=
            #   ["']        - opening quote
            #   ([^"'#]+)   - capture: one or more chars, not quotes or fragment
            #   [^"']*      - optional fragment
            #   ["']        - closing quote
            raw_hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
            hrefs = []
            for h in raw_hrefs:
                h = h.split("#")[0].strip()
                if not h or h.startswith(("javascript:", "mailto:", "data:")):
                    continue
                # Filter by content type
                if self.config.content_types == "pdf":
                    if h.lower().endswith(".pdf"):
                        hrefs.append(h)
                elif self.config.content_types == "html":
                    if not h.lower().endswith(".pdf"):
                        hrefs.append(h)
                else:
                    hrefs.append(h)

        new_count = 0
        for href in hrefs:
            resolved = resolve_url(page_url, href)
            if not resolved:
                continue

            if resolved in self.queued:
                continue

            # PDF links may point to external CDN domains — skip scope check for PDFs
            is_pdf = resolved.lower().endswith(".pdf")
            if not is_pdf and not is_in_scope(resolved, self.config.input_address):
                if self.config.verbose:
                    print(f"  [OUT-OF-SCOPE] {resolved}")
                continue

            self.queue.append(resolved)
            self.queued.add(resolved)
            new_count += 1
            if self.config.verbose:
                print(f"    + {resolved}")
            _flush()

        if new_count > 0:
            print(f"  [LINKS] Queued {new_count} new in-scope URLs (from {len(hrefs)} hrefs)")
            _flush()
        elif self.config.verbose:
            print(f"  [LINKS] Found {len(hrefs)} links, none new in-scope")
            _flush()

    def _extract_links_from_file(self, filepath: str, page_url: str) -> None:
        """Extract links from an already-saved file (for resume mode).

        Args:
            filepath: Path to the saved HTML file.
            page_url: Original URL of the page.
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                html = f.read()
            self._extract_and_queue_links(html, page_url)
        except OSError as e:
            if self.config.verbose:
                print(f"  [WARN] Could not read cached file for link extraction: {e}")

    def _print_summary(self) -> None:
        """Print a crawl summary after completion."""
        print()
        print("=" * 70)
        print("  Crawl Complete")
        print(f"  Pages saved:   {self.saved_count}")
        print(f"  Pages visited: {len(self.visited)}")
        print(f"  Unique URLs:   {len(self.queued)}")
        print(f"  Pages failed:  {len(self.failed)}")
        print(f"  Output folder: {self.config.output_folder}")
        print("=" * 70)

        if self.failed:
            print()
            print("  Failed URLs:")
            for url, reason in self.failed.items():
                print(f"    [{reason}] {url}")
            print()
