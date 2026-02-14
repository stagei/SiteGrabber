"""URL-to-filesystem path mapping and file saving."""

import os
import re
from urllib.parse import urlparse, unquote


def url_to_filepath(url: str, base_url: str, output_folder: str) -> str:
    """Map a URL to a local filesystem path relative to the output folder.

    The file is placed in a directory structure that mirrors the URL path
    relative to the base (input) URL.

    Examples:
        base = "https://www.ibm.com/docs/en/db2/12.1.x"
        url  = "https://www.ibm.com/docs/en/db2/12.1.x"
        -> output_folder/index.html

        base = "https://www.ibm.com/docs/en/db2/12.1.x"
        url  = "https://www.ibm.com/docs/en/db2/12.1.x?topic=applications-application-design"
        -> output_folder/topic--applications-application-design.html

        base = "https://www.ibm.com/docs/en/db2/12.1.x"
        url  = "https://www.ibm.com/docs/en/db2/12.1.x/subpage"
        -> output_folder/subpage/index.html

    Args:
        url: The fully resolved URL to map.
        base_url: The original input address (crawl root).
        output_folder: Local filesystem folder for output.

    Returns:
        Absolute filesystem path for saving the downloaded content.
    """
    url_parsed = urlparse(url)
    base_parsed = urlparse(base_url)

    # Get the relative path by removing the base path prefix
    base_path = base_parsed.path.rstrip("/")
    url_path = url_parsed.path

    if url_path.startswith(base_path):
        relative_path = url_path[len(base_path):]
    else:
        relative_path = url_path

    # Clean up the relative path
    relative_path = relative_path.strip("/")
    relative_path = unquote(relative_path)

    # Build filename from query parameters if present
    query_part = ""
    if url_parsed.query:
        # Convert query string to filename-safe format
        # e.g., "topic=applications-application-design" -> "topic--applications-application-design"
        query_part = _sanitize_query(url_parsed.query)

    # Determine the final filename
    if not relative_path and not query_part:
        # Root page
        filename = "index.html"
        sub_dir = ""
    elif query_part and not relative_path:
        # Same path as base but with query params
        filename = f"{query_part}.html"
        sub_dir = ""
    elif query_part:
        # Subpath with query params
        filename = f"{query_part}.html"
        sub_dir = relative_path
    else:
        # Subpath without query params
        filename = "index.html"
        sub_dir = relative_path

    # Sanitize all path components
    if sub_dir:
        sub_dir = _sanitize_path(sub_dir)
    filename = _sanitize_filename(filename)

    # Build full filesystem path
    full_path = os.path.join(output_folder, sub_dir, filename) if sub_dir else os.path.join(output_folder, filename)

    return full_path


def save_page(filepath: str, content: str) -> bool:
    """Save HTML content to a file, creating directories as needed.

    Args:
        filepath: Full filesystem path for the file.
        content: HTML content to write.

    Returns:
        True if saved successfully, False on error.
    """
    try:
        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except OSError as e:
        print(f"  [ERROR] Failed to save {filepath}: {e}")
        return False


def save_binary(filepath: str, content: bytes) -> bool:
    """Save binary content (e.g. PDF) to a file, creating directories as needed.

    Args:
        filepath: Full filesystem path for the file.
        content: Raw bytes to write.

    Returns:
        True if saved successfully, False on error.
    """
    try:
        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(filepath, "wb") as f:
            f.write(content)

        return True
    except OSError as e:
        print(f"  [ERROR] Failed to save {filepath}: {e}")
        return False


def file_exists(filepath: str) -> bool:
    """Check if a file already exists (for resume support).

    Args:
        filepath: Full filesystem path to check.

    Returns:
        True if the file exists and has content.
    """
    return os.path.isfile(filepath) and os.path.getsize(filepath) > 0


def pdf_url_to_filepath(url: str, output_folder: str) -> str:
    """Map a PDF URL to a local filesystem path.

    Uses the original filename from the URL path (e.g. db2_sec_guide.pdf).

    Args:
        url: Fully resolved PDF URL.
        output_folder: Local filesystem folder for output.

    Returns:
        Absolute filesystem path for saving the PDF.
    """
    url_parsed = urlparse(url)
    # Get the filename from the URL path
    path = unquote(url_parsed.path)
    filename = os.path.basename(path) or "download.pdf"
    filename = _sanitize_filename(filename)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return os.path.join(output_folder, filename)


def _sanitize_query(query: str) -> str:
    """Convert a URL query string into a filename-safe string.

    e.g., "topic=applications-application-design&ref=nav"
    -> "topic--applications-application-design_ref--nav"

    Args:
        query: URL query string (without leading '?').

    Returns:
        Sanitized string suitable for use in filenames.
    """
    # Replace = with -- and & with _
    result = query.replace("=", "--").replace("&", "_")
    # Remove any remaining unsafe characters
    result = re.sub(r'[<>:"/\\|?*]', "_", result)
    # Truncate if too long (Windows MAX_PATH consideration)
    if len(result) > 200:
        result = result[:200]
    return result


def _sanitize_filename(filename: str) -> str:
    """Remove or replace characters that are invalid in filenames.

    Preserves the original extension (.html, .pdf, etc.).

    Args:
        filename: Proposed filename.

    Returns:
        Sanitized filename safe for Windows and Unix.
    """
    # Replace invalid filename characters
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", filename)
    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip(". ")
    # Ensure not empty
    if not sanitized:
        sanitized = "page.html"
    # Ensure it ends with a known extension
    lower = sanitized.lower()
    if not lower.endswith((".html", ".pdf")):
        sanitized += ".html"
    return sanitized


def _sanitize_path(path: str) -> str:
    """Sanitize a relative directory path for the filesystem.

    Args:
        path: Relative path string with / separators.

    Returns:
        Sanitized path safe for os.path.join.
    """
    parts = path.split("/")
    sanitized_parts = []
    for part in parts:
        # Remove invalid directory name characters
        clean = re.sub(r'[<>:"/\\|?*]', "_", part)
        clean = clean.strip(". ")
        if clean:
            sanitized_parts.append(clean)
    return os.path.join(*sanitized_parts) if sanitized_parts else ""
