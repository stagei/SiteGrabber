# SiteGrabber

Recursive website content downloader with smart URL resolution and HTML filtering.

## Features

- Recursive crawling with BFS traversal and deduplication
- Smart URL resolution with overlap detection
- HTML filtering by div attributes (class, id, aria-label, etc.)
- Rate limiting with configurable delay
- Resume support (skips already-downloaded files)
- Progress reporting to console

## Requirements

- Python 3.10+
- Dependencies: `requests`, `beautifulsoup4`, `lxml`, `playwright` (auto-installed)

## Installation

No manual install needed — `SiteGrabber.ps1` automatically installs pip dependencies from `requirements.txt` on every run. When `-Browser` is used, Playwright Chromium is also auto-installed.

If you prefer to install manually:

```powershell
cd c:\opt\src\SiteGrabber
pip install -r requirements.txt
python -m playwright install chromium   # only needed for -Browser mode
```

## Usage

### Python CLI

```powershell
python -m sitegrabber `
  --input-address "https://www.ibm.com/docs/en/db2/12.1.x" `
  --output-folder "C:\opt\data\SiteGrabber\ibm-db2" `
  --recursive `
  --delay 0.5
```

### With HTML filtering

```powershell
python -m sitegrabber `
  --input-address "https://www.ibm.com/docs/en/db2/12.1.x" `
  --output-folder "C:\opt\data\SiteGrabber\ibm-db2" `
  --limitation-type "class" `
  --limitation-text "ibmdocs-toc-link" `
  --recursive
```

### PowerShell Wrapper

```powershell
.\run.ps1 `
  -InputAddress "https://www.ibm.com/docs/en/db2/12.1.x" `
  -OutputFolder "C:\opt\data\SiteGrabber\ibm-db2" `
  -Recursive `
  -InputLimitationType "class" `
  -InputLimitationText "ibmdocs-toc-link"
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--input-address` | string | *required* | Starting URL to crawl |
| `--output-folder` | string | *required* | Local folder to save downloaded pages |
| `--limitation-type` | string | None | Attribute name to filter on (class, id, aria-label, etc.) |
| `--limitation-text` | string | None | Text to match in div attributes |
| `--recursive` / `--no-recursive` | bool | true | Follow links recursively |
| `--delay` | float | 0.5 | Seconds between requests |
| `--max-pages` | int | 0 | Maximum pages to download (0 = unlimited) |
| `--timeout` | int | 30 | Request timeout in seconds |
| `--resume` | flag | false | Skip already-downloaded files |
| `--verbose` | flag | false | Enable verbose logging |

## HTML Filtering

When `--limitation-text` is set, the crawler filters HTML content before extracting links:

- **Text only**: Searches ALL attributes of all `<div>` elements for matching text
- **Type + Text**: Only matches divs where the specified attribute contains the text

This is useful for sites like IBM docs where navigation links are in specific div containers.

## URL Resolution

The tool handles smart URL resolution including overlap detection:

- Input: `https://www.ibm.com/docs/en/db2/12.1.x`
- Found href: `/docs/en/db2/12.1.x?topic=applications-application-design`
- Result: `https://www.ibm.com/docs/en/db2/12.1.x?topic=applications-application-design`

Relative paths, absolute paths, and fragment-only links are all handled correctly.
