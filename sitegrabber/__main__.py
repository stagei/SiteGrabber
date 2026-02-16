"""CLI entry point for SiteGrabber.

Usage:
    python -m sitegrabber --input-address URL --output-folder PATH [options]
"""

import argparse
import sys

from .config import CrawlConfig
from .crawler import Crawler


def parse_args(argv: list[str] | None = None) -> CrawlConfig:
    """Parse command-line arguments into a CrawlConfig.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Populated CrawlConfig instance.
    """
    parser = argparse.ArgumentParser(
        prog="sitegrabber",
        description="SiteGrabber - Recursive website content downloader with smart URL resolution and HTML filtering.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic recursive crawl
  python -m sitegrabber --input-address "https://www.ibm.com/docs/en/db2/12.1.x" --output-folder "./output"

  # With HTML filtering (only process links inside divs with class 'ibmdocs-toc-link')
  python -m sitegrabber --input-address "https://www.ibm.com/docs/en/db2/12.1.x" --output-folder "./output" --limitation-type class --limitation-text ibmdocs-toc-link

  # Non-recursive (single page only)
  python -m sitegrabber --input-address "https://example.com/page" --output-folder "./output" --no-recursive

  # Resume a previous crawl
  python -m sitegrabber --input-address "https://example.com" --output-folder "./output" --resume
        """,
    )

    parser.add_argument(
        "--input-address",
        required=True,
        help="Starting URL to crawl (e.g., https://www.ibm.com/docs/en/db2/12.1.x)",
    )

    parser.add_argument(
        "--output-folder",
        required=True,
        help="Local folder to save downloaded pages",
    )

    parser.add_argument(
        "--limitation-type",
        default=None,
        help="Attribute name to filter on (e.g., class, id, aria-label). "
             "If set, only divs where this attribute matches --limitation-text are processed.",
    )

    parser.add_argument(
        "--limitation-text",
        default=None,
        help="Text to search for in div attributes. "
             "Without --limitation-type, searches ALL attributes of every div.",
    )

    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Follow links recursively (default: true). Use --no-recursive for single page.",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to wait between requests (default: 0.5)",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Maximum number of pages to download (0 = unlimited, default: 0)",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Skip already-downloaded files and continue from where you left off",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose logging (shows out-of-scope URLs, skipped content types, etc.)",
    )

    parser.add_argument(
        "--browser",
        action="store_true",
        default=False,
        help="Use headless Chromium browser (Playwright) for JS-rendered SPA sites. "
             "Required for sites like IBM docs that load content via JavaScript.",
    )

    parser.add_argument(
        "--wait-for",
        default="domcontentloaded",
        choices=["networkidle", "domcontentloaded", "load", "commit"],
        help="Playwright wait condition before extracting HTML (default: domcontentloaded). "
             "Only used with --browser.",
    )

    parser.add_argument(
        "--extra-wait",
        type=float,
        default=5.0,
        help="Seconds to wait after page load for JS to render dynamic content (default: 5.0). "
             "Increase for heavy SPA sites. Only used with --browser.",
    )

    parser.add_argument(
        "--content-types",
        default="html",
        choices=["html", "pdf", "all"],
        help="What to download: html (default), pdf (only PDF links), all (both HTML pages and PDF files).",
    )

    parser.add_argument(
        "--login-url",
        default=None,
        help="URL of the login page. When set, the browser navigates here first and authenticates "
             "before crawling. Requires --browser, --login-email, and --login-password.",
    )

    parser.add_argument(
        "--login-email",
        default=None,
        help="Email or username for login.",
    )

    parser.add_argument(
        "--login-password",
        default=None,
        help="Password for login.",
    )

    args = parser.parse_args(argv)

    return CrawlConfig(
        input_address=args.input_address,
        output_folder=args.output_folder,
        limitation_type=args.limitation_type,
        limitation_text=args.limitation_text,
        recursive=args.recursive,
        delay=args.delay,
        max_pages=args.max_pages,
        timeout=args.timeout,
        resume=args.resume,
        verbose=args.verbose,
        browser=args.browser,
        wait_for=args.wait_for,
        extra_wait=args.extra_wait,
        content_types=args.content_types,
        login_url=args.login_url,
        login_email=args.login_email,
        login_password=args.login_password,
    )


def main() -> None:
    """Main entry point."""
    config = parse_args()
    crawler = Crawler(config)

    try:
        crawler.crawl()
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Crawl stopped by user.")
        print(f"  Pages saved so far: {crawler.saved_count}")
        print(f"  Pages visited: {len(crawler.visited)}")
        print(f"  Output folder: {config.output_folder}")
        sys.exit(1)


if __name__ == "__main__":
    main()
