"""HTML filtering by div attributes for link extraction scoping."""

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag


def filter_html(
    soup: BeautifulSoup,
    limitation_type: Optional[str] = None,
    limitation_text: Optional[str] = None,
) -> list:
    """Filter HTML to only matching div elements, or return full document.

    Two filtering modes:

    1. **limitation_text only**: Search ALL attributes of every <div> for the
       text. If any attribute value (class, id, aria-label, style, data-*, etc.)
       contains the text, the entire div (start to end tag) is a match.

    2. **limitation_type + limitation_text**: Only match divs where the specific
       named attribute contains the text.

    If no limitation_text is set, the full document is returned unfiltered.

    Args:
        soup: Parsed BeautifulSoup document.
        limitation_type: Optional attribute name to filter on (e.g. 'class', 'id', 'aria-label').
        limitation_text: Text to search for in div attributes.

    Returns:
        List of matching Tag elements, or [soup] if no filtering is applied.
    """
    if not limitation_text:
        return [soup]

    matching_divs = []

    if limitation_type:
        # Mode 2: Match specific attribute
        matching_divs = _filter_by_attribute(soup, limitation_type, limitation_text)
    else:
        # Mode 1: Match ANY attribute value across all divs
        matching_divs = _filter_by_any_attribute(soup, limitation_text)

    if not matching_divs:
        print(f"  [FILTER] No matching divs found for text '{limitation_text}'"
              f"{f' in attribute {limitation_type}' if limitation_type else ''}")
        print("  [FILTER] Falling back to full document for link extraction")
        return [soup]

    print(f"  [FILTER] Found {len(matching_divs)} matching div(s)")
    return matching_divs


def _filter_by_attribute(
    soup: BeautifulSoup,
    attr_name: str,
    attr_text: str,
) -> list[Tag]:
    """Find divs where a specific attribute contains the given text.

    For multi-valued attributes like 'class', checks if any class value
    contains the text. For single-valued attributes, checks substring match.

    Args:
        soup: Parsed document.
        attr_name: Attribute name (e.g. 'class', 'id', 'aria-label').
        attr_text: Text to match within the attribute value.

    Returns:
        List of matching div Tags.
    """
    # Regex pattern for substring matching
    # re.compile with IGNORECASE for flexible matching
    pattern = re.compile(re.escape(attr_text), re.IGNORECASE)

    matches = []
    for div in soup.find_all("div"):
        attr_value = div.get(attr_name)
        if attr_value is None:
            continue

        # 'class' attribute returns a list in BeautifulSoup
        if isinstance(attr_value, list):
            for val in attr_value:
                if pattern.search(str(val)):
                    matches.append(div)
                    break
        else:
            if pattern.search(str(attr_value)):
                matches.append(div)

    return matches


def _filter_by_any_attribute(
    soup: BeautifulSoup,
    text: str,
) -> list[Tag]:
    """Find divs where ANY attribute value contains the given text.

    Checks every attribute of every div element. For multi-valued
    attributes (like class), checks each value individually.

    Args:
        soup: Parsed document.
        text: Text to search for in attribute values.

    Returns:
        List of matching div Tags.
    """
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    matches = []

    for div in soup.find_all("div"):
        if not div.attrs:
            continue

        matched = False
        for attr_name, attr_value in div.attrs.items():
            if matched:
                break

            if isinstance(attr_value, list):
                for val in attr_value:
                    if pattern.search(str(val)):
                        matched = True
                        break
            else:
                if pattern.search(str(attr_value)):
                    matched = True

        if matched:
            matches.append(div)

    return matches


def extract_links(elements: list, content_types: str = "html") -> list[str]:
    """Extract href values from anchor tags within the given elements.

    Filters links based on content_types:
    - "html": Only non-PDF links (pages).
    - "pdf":  Only links ending in .pdf.
    - "all":  Both HTML page links and PDF links.

    Args:
        elements: List of BeautifulSoup Tag or BeautifulSoup objects.
        content_types: What link types to extract: "html", "pdf", or "all".

    Returns:
        List of href strings (may contain relative or absolute URLs).
    """
    hrefs = []
    seen = set()

    for element in elements:
        for anchor in element.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href in seen:
                continue

            is_pdf = href.lower().endswith(".pdf")

            if content_types == "pdf" and not is_pdf:
                continue
            if content_types == "html" and is_pdf:
                continue
            # "all" accepts both

            seen.add(href)
            hrefs.append(href)

    return hrefs
