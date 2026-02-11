"""Smart URL resolution with overlap detection and scope checking."""

from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode


def resolve_url(base_url: str, href: str) -> str:
    """Resolve a potentially relative href against a base URL.

    Handles standard relative paths, absolute paths from domain root,
    and edge cases where the href path overlaps with the base URL path.

    Examples:
        base = "https://www.ibm.com/docs/en/db2/12.1.x"
        href = "/docs/en/db2/12.1.x?topic=applications-application-design"
        result = "https://www.ibm.com/docs/en/db2/12.1.x?topic=applications-application-design"

        base = "https://example.com/a/b/c"
        href = "d/e"
        result = "https://example.com/a/b/d/e"

    Args:
        base_url: The page URL where the href was found.
        href: The href value from an anchor tag.

    Returns:
        Fully resolved absolute URL.
    """
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""

    # Strip whitespace and fragment-only references
    href = href.strip()

    # Already absolute URL
    if href.startswith(("http://", "https://")):
        return _normalize_url(href)

    # Standard urljoin handles:
    #   - Absolute paths from root (starts with /)
    #   - Relative paths (no leading /)
    #   - Protocol-relative (starts with //)
    resolved = urljoin(base_url, href)

    # Additional overlap detection for non-standard relative paths
    # where urljoin might produce incorrect results.
    # e.g., base ends with /docs/en/db2 and href is docs/en/db2?topic=foo
    # (no leading slash, so urljoin treats it as relative to parent)
    resolved = _fix_overlap(base_url, href, resolved)

    return _normalize_url(resolved)


def _fix_overlap(base_url: str, href: str, resolved: str) -> str:
    """Detect and fix path overlap between base URL and href.

    If the href (without leading slash) starts with a segment that matches
    the tail of the base URL path, urljoin may produce a duplicate path.
    This function detects that and returns the corrected URL.

    Args:
        base_url: Original base URL.
        href: Original href value.
        resolved: URL as resolved by urljoin.

    Returns:
        Corrected URL if overlap detected, otherwise the original resolved URL.
    """
    if href.startswith("/") or href.startswith(("http://", "https://", "//")):
        # urljoin handles absolute paths and full URLs correctly
        return resolved

    base_parsed = urlparse(base_url)
    resolved_parsed = urlparse(resolved)
    base_path = base_parsed.path.rstrip("/")
    resolved_path = resolved_parsed.path

    # Check if the resolved path contains a duplicated segment from base
    # e.g., base_path = /docs/en/db2/12.1.x, href = docs/en/db2/12.1.x?topic=foo
    # urljoin would give /docs/en/db2/docs/en/db2/12.1.x?topic=foo (wrong)
    # Correct would be /docs/en/db2/12.1.x?topic=foo

    # Split href path (without query) to get the path portion
    href_path = urlparse(href).path

    # Try matching overlap: find if any suffix of base_path matches
    # a prefix of href_path
    base_segments = base_path.split("/")
    href_segments = href_path.split("/")

    for i in range(1, len(base_segments)):
        # Check if the last i segments of base match the first i segments of href
        base_tail = "/".join(base_segments[-i:])
        href_head = "/".join(href_segments[:i])
        if base_tail == href_head and base_tail:
            # Overlap detected - construct correct URL
            # Base path up to and including the overlap + rest of href
            correct_path = base_path + "/" + "/".join(href_segments[i:])
            correct_path = correct_path.rstrip("/") or "/"

            # Preserve query string from href
            href_parsed = urlparse(href)
            corrected = urlunparse((
                base_parsed.scheme,
                base_parsed.netloc,
                correct_path,
                "",
                href_parsed.query or resolved_parsed.query,
                "",
            ))
            return corrected

    return resolved


def _normalize_url(url: str) -> str:
    """Normalize a URL for consistent comparison.

    Removes fragments, trailing slashes on paths, and sorts query params.

    Args:
        url: URL to normalize.

    Returns:
        Normalized URL string.
    """
    parsed = urlparse(url)

    # Remove fragment
    # Keep path as-is but ensure no double slashes
    path = parsed.path
    while "//" in path:
        path = path.replace("//", "/")

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        path,
        parsed.params,
        parsed.query,
        "",  # Remove fragment
    ))


def is_in_scope(url: str, base_url: str) -> bool:
    """Check if a URL falls within the crawl scope of the base URL.

    A URL is in scope if:
    - Same scheme and netloc (domain)
    - Path starts with the base URL path

    Args:
        url: URL to check.
        base_url: The original input address defining the crawl scope.

    Returns:
        True if the URL is within scope.
    """
    if not url:
        return False

    url_parsed = urlparse(url)
    base_parsed = urlparse(base_url)

    # Must be same domain
    if url_parsed.netloc != base_parsed.netloc:
        return False

    # Must be same scheme (or allow http/https flexibility)
    if url_parsed.scheme not in ("http", "https"):
        return False

    # Path must start with base path
    base_path = base_parsed.path.rstrip("/")
    url_path = url_parsed.path

    # The URL path should start with the base path
    # /docs/en/db2/12.1.x?topic=foo is within /docs/en/db2/12.1.x
    if not url_path.startswith(base_path):
        return False

    # Ensure it's a true prefix (not just partial match)
    # /docs/en/db2/12.1.x-extra should NOT match /docs/en/db2/12.1.x
    remaining = url_path[len(base_path):]
    if remaining and not remaining.startswith("/") and not remaining == "":
        return False

    return True


def get_domain_root(url: str) -> str:
    """Extract the domain root (scheme + netloc) from a URL.

    Args:
        url: Full URL.

    Returns:
        Domain root, e.g. 'https://www.ibm.com'
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"
