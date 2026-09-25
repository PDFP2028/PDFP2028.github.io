"""Simple Docsify Markdown Link Checker.

This script crawls a Docsify-based documentation site, extracts all Markdown
and HTML links, and checks their validity. It generates a CSV report of the
results, indicating which links are good or broken.
"""
import csv
import sys
import re
from urllib.parse import urlparse, urljoin
import requests

class DocsifyMarkdownLinkChecker:
    """Check links in a Docsify site's Markdown documentation."""

    def __init__(self, target_url):
        # Normalize the base URL input
        target_url = target_url.strip().strip("'").strip('"')
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'http://' + target_url
        self.base_url = target_url.rstrip('/')

        # Deduplication tracking sets
        self.pages_to_crawl = set()
        self.processed_links = set()

        self.filename = "crawl_report.csv"
        self.fields = [
            "Source Page",
            "Tested Link",
            "Type",
            "Status",
            "HTTP Code / Error",
        ]

        # Parse the host domain to identify internal vs external links
        parsed = urlparse(self.base_url)
        self.allowed_host = parsed.netloc

        # Initialize a fresh CSV spreadsheet
        with open(self.filename, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.fields)
            writer.writeheader()

        print(f"✅ Initialized Link Checker for: {self.base_url}")
        print(f"📊 Results will write line-by-line to: {self.filename}\n")

    def _write_row(self, source, link, link_type, status, error_msg):
        with open(self.filename, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.fields)
            writer.writerow({
                "Source Page": source,
                "Tested Link": link,
                "Type": link_type,
                "Status": status,
                "HTTP Code / Error": str(error_msg)
            })

    def check_links(self):
        """Check all links found in the Docsify documentation site."""
        # 1. Always queue up the main index readme page
        self.pages_to_crawl.add("/README.md")

        # 2. Attempt to fetch and parse the _sidebar.md to find all documentation paths
        sidebar_url = f"{self.base_url}/_sidebar.md"
        print(f"🔍 Inspecting sidebar index file: {sidebar_url}")

        try:
            response = requests.get(
                sidebar_url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if response.status_code == 200:
                # Find all markdown links matching [text](path.md)
                found_sidebar_paths = re.findall(r'\[.*?\]\((.*?)\)', response.text)
                for path in found_sidebar_paths:
                    if not path.startswith(('http://', 'https://')):
                        # Standardize path ending to raw markdown files
                        clean_path = path.split('?')[0].split('#')[0]
                        if (
                            not clean_path.endswith('.md')
                            and not clean_path.endswith('/')
                        ):
                            clean_path += '.md'
                        elif clean_path.endswith('/'):
                            clean_path += 'README.md'

                        if not clean_path.startswith('/'):
                            clean_path = '/' + clean_path
                        self.pages_to_crawl.add(clean_path)
                print(
                    "📋 Discovered "
                    f"{len(self.pages_to_crawl)} unique pages to check "
                    "from the sidebar structure."
                )
            else:
                print(
                    "⚠️ Warning: _sidebar.md returned HTTP "
                    f"{response.status_code}. Defaulting to main page crawl only."
                )
        except requests.RequestException as exc:
            print(
                "⚠️ Could not read _sidebar.md directly "
                f"({exc}). Defaulting to main page crawl."
            )

        # 3. Process every markdown page we discovered
        for page_path in sorted(self.pages_to_crawl):
            # Form the direct path to the raw .md file on your Nginx/GitHub server
            md_file_url = f"{self.base_url}{page_path}"
            # Form the decorative hash URL for display purposes inside the CSV
            display_spa_url = f"{self.base_url}/#{page_path.replace('.md', '')}"

            print(f"\n📄 Scanning Content: {display_spa_url}")

            try:
                res = requests.get(
                    md_file_url,
                    timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if res.status_code >= 400:
                    print(
                        "  ❌ Broken Internal Page: "
                        f"{display_spa_url} (HTTP {res.status_code})"
                    )
                    self._write_row(
                        "Sidebar Navigation",
                        display_spa_url,
                        "Internal",
                        "Bad",
                        f"HTTP {res.status_code}",
                    )
                    continue

                # Extract links from the raw markdown file text
                # Targets both markdown format [text](link) and raw HTML href strings
                md_links = re.findall(r'\[.*?\]\((.*?)\)', res.text)
                html_links = re.findall(r'href=["\'](.*?)["\']', res.text)
                all_extracted_links = set(md_links + html_links)

                for raw_link in all_extracted_links:
                    raw_link = raw_link.strip()
                    if not raw_link or raw_link.startswith(('#', 'mailto:', 'tel:')):
                        continue

                    # Clean brackets or symbols that leak from bad markdown layouts
                    clean_link = raw_link.replace('[', '').replace(']', '')

                    # Resolve relative links against the current page's
                    # direct path context
                    absolute_link = urljoin(md_file_url, clean_link)
                    parsed_link = urlparse(absolute_link)

                    # Skip non-web protocols
                    if parsed_link.scheme not in ['http', 'https']:
                        continue

                    # Prevent testing the exact same source -> target combination twice
                    unique_pair = (display_spa_url, absolute_link)
                    if unique_pair in self.processed_links:
                        continue
                    self.processed_links.add(unique_pair)

                    # Determine if the link points internally to your site or externally
                    is_internal = parsed_link.netloc == self.allowed_host
                    link_type = "Internal" if is_internal else "External"

                    # If it's an internal link matching a Docsify hash pattern,
                    # convert it to a raw file test.
                    test_url = absolute_link
                    if is_internal and '/#/' in absolute_link:
                        route_part = absolute_link.split('/#/')[1].split('?')[0]
                        if not route_part.endswith('.md') and route_part != "":
                            test_url = f"{self.base_url}/{route_part}.md"
                        elif route_part == "":
                            test_url = f"{self.base_url}/README.md"

                    # 4. Perform the live connection health check on the link
                    try:
                        # Use a fast HEAD request first; fallback to GET if a server
                        # blocks HEAD.
                        link_res = requests.head(
                            test_url,
                            timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"},
                            allow_redirects=True,
                        )
                        if link_res.status_code == 405 or link_res.status_code == 400:
                            link_res = requests.get(
                                test_url,
                                timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"},
                                allow_redirects=True,
                            )

                        if link_res.status_code >= 400:
                            print(
                                "  ❌ Bad Link [HTTP "
                                f"{link_res.status_code}]: {absolute_link}"
                            )
                            self._write_row(
                                display_spa_url,
                                absolute_link,
                                link_type,
                                "Bad",
                                link_res.status_code,
                            )
                        else:
                            print(
                                "  ✅ Good Link [HTTP "
                                f"{link_res.status_code}]: {absolute_link}"
                            )
                            self._write_row(
                                display_spa_url,
                                absolute_link,
                                link_type,
                                "Good",
                                link_res.status_code,
                            )

                    except requests.RequestException as exc:
                        print(
                            "  ❌ Bad Link [Connection Error]: "
                            f"{absolute_link} ({exc})"
                        )
                        self._write_row(
                            display_spa_url,
                            absolute_link,
                            link_type,
                            "Bad",
                            f"Connection Failure: {exc}",
                        )

            except requests.RequestException as page_err:
                print(f"❌ Error checking file path {md_file_url}: {page_err}")
                self._write_row(
                    "Crawler Engine",
                    display_spa_url,
                    "Internal",
                    "Bad",
                    "Page Unreachable",
                )

        print(
            "\n💾 Link validation complete! Open '"
            f"{self.filename}' to view the results sheet."
        )

    def run(self):
        """Run the documentation link check."""
        self.check_links()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage error: Missing target website URL or IP.")
        print("Example: python crawler_link_test.py http://localhost:8000")
        sys.exit(1)

    checker = DocsifyMarkdownLinkChecker(sys.argv[1])
    checker.run()
