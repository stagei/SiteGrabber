"""Core crawl engine - BFS queue, deduplication, rate limiting, retry logic."""

import time
from collections import deque

import requests
from bs4 import BeautifulSoup

from .config import CrawlConfig
from .url_resolver import resolve_url, is_in_scope
from .html_filter import filter_html, extract_links
from .file_saver import url_to_filepath, save_page, file_exists


class Crawler:
    """Recursive website crawler with BFS traversal.

    Downloads pages starting from input_address, extracts links from
    (optionally filtered) HTML content, and saves each page to the
    local filesystem.
    """

    def __init__(self, config: CrawlConfig):
        self.config = config
        self.queue: deque[str] = deque()
        self.visited: set[str] = set()
        self.failed: dict[str, str] = {}
        self.saved_count: int = 0

        # HTTP session with persistent settings
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,nb;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })

    def crawl(self) -> None:
        """Run the crawl process.

        Starts from config.input_address and recursively follows in-scope
        links until the queue is empty or max_pages is reached.
        """
        input_url = self.config.input_address.rstrip("/")
        self.queue.append(input_url)

        print("=" * 70)
        print(f"  SiteGrabber - Starting crawl")
        print(f"  URL:    {input_url}")
        print(f"  Output: {self.config.output_folder}")
        if self.config.limitation_text:
            ltype = self.config.limitation_type or "(any attribute)"
            print(f"  Filter: {ltype} = '{self.config.limitation_text}'")
        print(f"  Recursive: {self.config.recursive}")
        print(f"  Delay: {self.config.delay}s | Timeout: {self.config.timeout}s")
        if self.config.max_pages > 0:
            print(f"  Max pages: {self.config.max_pages}")
        if self.config.resume:
            print(f"  Resume mode: ON (skipping existing files)")
        print("=" * 70)
        print()

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

        self._print_summary()

    def _process_url(self, url: str) -> None:
        """Download, save, and extract links from a single URL.

        Args:
            url: Fully resolved URL to process.
        """
        queue_size = len(self.queue)
        visited_count = len(self.visited)
        print(f"[{self.saved_count + 1}] Downloading (queue: {queue_size}, visited: {visited_count})")
        print(f"  {url}")

        # Determine local filepath
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
        html = self._download(url)
        if html is None:
            return

        # Save to disk
        if save_page(filepath, html):
            self.saved_count += 1
            print(f"  [SAVED] {filepath}")
        else:
            return

        # Extract and queue new links (only if recursive)
        if self.config.recursive:
            self._extract_and_queue_links(html, url)

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

        # Apply HTML filtering
        filtered_elements = filter_html(
            soup,
            limitation_type=self.config.limitation_type,
            limitation_text=self.config.limitation_text,
        )

        # Extract links from filtered content
        hrefs = extract_links(filtered_elements)

        if self.config.verbose and hrefs:
            print(f"  [DEBUG] Links found (raw hrefs): {len(hrefs)}")
            for i, href in enumerate(hrefs, 1):
                print(f"    {i}. {href}")

        # Use base URL with trailing slash so relative hrefs resolve under current path (e.g. tutorial/index.html -> /3/tutorial/index.html)
        base_for_resolve = page_url.rstrip("/") + "/" if "?" not in page_url and "#" not in page_url else page_url

        new_count = 0
        queued: list[str] = []
        for href in hrefs:
            resolved = resolve_url(base_for_resolve, href)
            if not resolved:
                if self.config.verbose:
                    print(f"  [DEBUG] Skipped (unresolved): {href!r}")
                continue

            if resolved in self.visited:
                continue

            if not is_in_scope(resolved, self.config.input_address):
                if self.config.verbose:
                    print(f"  [OUT-OF-SCOPE] {resolved}")
                continue

            # Avoid adding duplicates to the queue
            if resolved not in self.visited:
                self.queue.append(resolved)
                new_count += 1
                queued.append(resolved)

        if self.config.verbose and queued:
            print(f"  [DEBUG] Resolved in-scope (queued): {len(queued)}")
            for i, url in enumerate(queued, 1):
                print(f"    {i}. {url}")

        if new_count > 0:
            print(f"  [LINKS] Found {len(hrefs)} links, queued {new_count} new in-scope URLs")
        elif self.config.verbose:
            print(f"  [LINKS] Found {len(hrefs)} links, none new in-scope")

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
