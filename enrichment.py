"""Best-effort extraction of a contact email (and, if confidently derivable, a
first/last name) from a business's own website.

Google's Places API does not return personal contact details, so this visits
the business's public homepage and a couple of likely contact pages and looks
for a published email address. Nothing is fabricated: if no email is found,
or a name can't be confidently split out of it, those fields are left blank.
"""
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TIMEOUT = 8
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
CANDIDATE_PATHS = ["/contact", "/contact-us", "/contactus", "/about", "/about-us"]
JUNK_DOMAINS = {
    "example.com", "yourdomain.com", "domain.com", "sentry.io",
    "wixpress.com", "godaddy.com", "schema.org", "w3.org",
}
ROLE_LOCAL_PARTS = {
    "info", "contact", "sales", "support", "hello", "admin", "office",
    "enquiry", "inquiry", "inquiries", "enquiries", "team", "mail", "help",
    "marketing", "hr", "careers", "press", "media", "billing", "noreply",
    "no-reply",
}


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _fetch(url: str):
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, allow_redirects=True
        )
        if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
            return resp.text
    except requests.RequestException:
        pass
    return None


def _clean_email(email: str) -> str:
    email = email.strip().strip(".,;:")
    return email


def _is_valid_email(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    if domain in JUNK_DOMAINS:
        return False
    if re.search(r"\.(png|jpg|jpeg|gif|svg|webp)$", email, re.I):
        return False
    return True


def _emails_from_mailto(soup: BeautifulSoup) -> list:
    emails = []
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        addr = a["href"].split(":", 1)[1].split("?")[0]
        addr = _clean_email(addr)
        if addr and _is_valid_email(addr):
            emails.append(addr)
    return emails


def _emails_from_text(soup: BeautifulSoup) -> list:
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ")
    found = [_clean_email(m) for m in EMAIL_RE.findall(text)]
    return [e for e in found if _is_valid_email(e)]


def _discover_contact_links(soup: BeautifulSoup, base_url: str) -> list:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").lower()
        if "contact" in href.lower() or "contact" in text:
            links.append(urljoin(base_url, href))
    # de-dupe, keep order, cap at 2
    seen = []
    for link in links:
        if link not in seen and urlparse(link).netloc == urlparse(base_url).netloc:
            seen.append(link)
    return seen[:2]


def guess_name_from_email(email: str):
    """Only splits into first/last when the local part clearly encodes a two-part
    human name (e.g. john.smith@ or jane-doe@). Role inboxes and single tokens
    (info@, contact@, rahul@) are left blank rather than guessed."""
    local = email.split("@")[0].lower()
    local = re.sub(r"\d+", "", local)
    if local in ROLE_LOCAL_PARTS:
        return "", ""
    for sep in (".", "_", "-"):
        if sep in local:
            parts = [p for p in local.split(sep) if p]
            if len(parts) >= 2 and all(p.isalpha() for p in parts[:2]):
                return parts[0].capitalize(), parts[1].capitalize()
    return "", ""


def find_contact_info(website_url: str) -> dict:
    """Returns {"email": str, "first_name": str, "last_name": str}. Fields are
    "" when nothing could be confidently found."""
    result = {"email": "", "first_name": "", "last_name": ""}
    base_url = _normalize_url(website_url)
    if not base_url:
        return result

    pages_to_try = [base_url]
    home_html = _fetch(base_url)
    home_soup = BeautifulSoup(home_html, "html.parser") if home_html else None

    if home_soup:
        pages_to_try += _discover_contact_links(home_soup, base_url)
    for path in CANDIDATE_PATHS:
        pages_to_try.append(urljoin(base_url, path))

    seen_pages = set()
    for page_url in pages_to_try:
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)

        html = home_html if page_url == base_url else _fetch(page_url)
        if not html:
            continue
        soup = home_soup if page_url == base_url and home_soup else BeautifulSoup(html, "html.parser")

        emails = _emails_from_mailto(soup) or _emails_from_text(soup)
        if emails:
            email = emails[0]
            first, last = guess_name_from_email(email)
            result["email"] = email
            result["first_name"] = first
            result["last_name"] = last
            return result

    return result
