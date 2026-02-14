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
        """Initialize Playwright headless Chromium browser."""
        try:
            from playwright.sync_api import sync_playwright
            print("[BROWSER] Starting headless Chromium...")
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._browser_context = self._browser.new_context(
                user_agent=self.config.user_agent,
                viewport={"width": 1920, "height": 1080},
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

    def _expand_toc_tree(self) -> None:
        """Expand all collapsed TOC tree nodes inside the limitation container.

        Uses limitation_type and limitation_text from config to locate the
        container div (e.g. div[aria-label="TOC navigation"]), then iteratively
        clicks all collapsed nodes (aria-expanded="false") within it until the
        entire tree is visible.  Only runs on the original input address.

        The container is re-queried each round because clicking a node may
        update the DOM (SPA navigation), invalidating previous references.
        """
        if self._page is None:
            return
        if not self.config.limitation_type or not self.config.limitation_text:
            return

        # Build CSS selector for the container div
        attr = self.config.limitation_type
        val = self.config.limitation_text
        container_sel = f'div[{attr}="{val}"]'

        container = self._page.query_selector(container_sel)
        if not container:
            print(f"  [TOC] Container not found: {container_sel}")
            _flush()
            return

        print(f"  [TOC] Found container: {container_sel}")
        print("  [TOC] Expanding tree nodes to discover all topic links...")
        _flush()

        max_rounds = 80  # Safety limit
        total_expanded = 0

        for round_num in range(1, max_rounds + 1):
            # Re-query the container each round (DOM may have changed)
            container = self._page.query_selector(container_sel)
            if not container:
                break

            # Find collapsed nodes inside the container
            collapsed = container.query_selector_all('[aria-expanded="false"]')
            if not collapsed:
                break

            expanded_this_round = 0
            for node in collapsed:
                try:
                    node.scroll_into_view_if_needed(timeout=2000)
                    node.click(timeout=2000)
                    expanded_this_round += 1
                    # Brief pause between clicks to let children load
                    time.sleep(0.3)
                except Exception:
                    continue

            total_expanded += expanded_this_round

            if expanded_this_round == 0:
                break

            # Wait for children to load after this round
            time.sleep(1.5)
            print(f"  [TOC] Round {round_num}: expanded {expanded_this_round} nodes "
                  f"(total: {total_expanded})")
            _flush()

        time.sleep(2.0)
        print(f"  [TOC] Tree expansion complete. {total_expanded} nodes expanded.")
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

        # Download the page
        if self.config.browser:
            html = self._download_browser(url)
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
                if self.config.extra_wait > 0:
                    time.sleep(self.config.extra_wait)

                # Best-effort secondary networkidle wait (short timeout)
                try:
                    self._page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass  # Best effort - proceed with what we have

                # On the original input address only, expand the TOC tree
                # to discover all topic URLs before we start crawling
                is_input = url.rstrip("/") == self.config.input_address.rstrip("/")
                if is_input and not self._toc_expanded:
                    self._expand_toc_tree()
                    self._toc_expanded = True

                # Get the fully rendered DOM (not the initial HTML source)
                html = self._page.content()
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
        """Parse HTML, apply filters, extract links, and add new ones to queue.

        Args:
            html: Raw HTML content.
            page_url: The URL this HTML was downloaded from (for resolving relative links).
        """
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            # Fallback to html.parser if lxml fails
            soup = BeautifulSoup(html, "html.parser")

        # Apply HTML filtering only on the original input address.
        # All other pages use the full document for link extraction.
        is_input_page = page_url.rstrip("/") == self.config.input_address.rstrip("/")
        filtered_elements = filter_html(
            soup,
            limitation_type=self.config.limitation_type if is_input_page else None,
            limitation_text=self.config.limitation_text if is_input_page else None,
        )

        # Extract links from filtered content
        hrefs = extract_links(filtered_elements, content_types=self.config.content_types)

        new_count = 0
        for href in hrefs:
            resolved = resolve_url(page_url, href)
            if not resolved:
                continue

            if resolved in self.visited:
                continue

            # PDF links may point to external CDN domains — skip scope check for PDFs
            is_pdf = resolved.lower().endswith(".pdf")
            if not is_pdf and not is_in_scope(resolved, self.config.input_address):
                if self.config.verbose:
                    print(f"  [OUT-OF-SCOPE] {resolved}")
                continue

            # Avoid adding duplicates to the queue
            if resolved not in self.visited:
                self.queue.append(resolved)
                new_count += 1
                print(f"    + {resolved}")
                _flush()

        if new_count > 0:
            print(f"  [LINKS] Found {len(hrefs)} links, queued {new_count} new in-scope URLs")
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
        print(f"  Pages failed:  {len(self.failed)}")
        print(f"  Output folder: {self.config.output_folder}")
        print("=" * 70)

        if self.failed:
            print()
            print("  Failed URLs:")
            for url, reason in self.failed.items():
                print(f"    [{reason}] {url}")
            print()
