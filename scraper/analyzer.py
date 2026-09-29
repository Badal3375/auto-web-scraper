"""Intelligent website analyzer for extracting Company, HR, Email, and Contact information."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .config import RequestConfig
from .fetcher import FetchError, Fetcher

log = logging.getLogger(__name__)

# Regular expressions for data extraction
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b")
PHONE_REGEX = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")

INVALID_EMAIL_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".pdf", ".zip"
}

DUMMY_EMAIL_DOMAINS = {
    "example.com", "domain.com", "yourdomain.com", "email.com",
    "sentry.io", "w3.org", "schema.org", "github.com", "placeholder.com"
}

HR_KEYWORDS = [
    "hr", "human resources", "talent", "talent acquisition", "recruiter",
    "recruitment", "people", "people operations", "people & culture",
    "head of people", "chief people officer", "vp of people", "careers", "hiring"
]

LEADERSHIP_KEYWORDS = [
    "founder", "co-founder", "ceo", "chief executive officer", "cto",
    "coo", "cfo", "director", "managing director", "president", "partner"
]

SUBPAGE_KEYWORDS = [
    "about", "about-us", "company", "who-we-are",
    "contact", "contact-us", "get-in-touch",
    "careers", "career", "jobs", "join-us", "work-with-us", "hiring",
    "team", "our-team", "people", "leadership", "management"
]


@dataclass
class HRContact:
    name: str
    role: str
    email: str | None = None
    phone: str | None = None
    source_url: str | None = None


@dataclass
class EmailRecord:
    email: str
    category: str  # HR & Recruitment | General & Support | Sales & Business | Direct / Other
    source_url: str


@dataclass
class AnalysisResult:
    url: str
    domain: str
    company: dict[str, Any] = field(default_factory=dict)
    hr_contacts: list[dict[str, Any]] = field(default_factory=list)
    emails: list[dict[str, Any]] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    social_links: dict[str, str] = field(default_factory=dict)
    careers_info: dict[str, Any] = field(default_factory=dict)
    scanned_urls: list[str] = field(default_factory=list)
    model_used: str = "Built-in Intelligent Extractor"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_company_df(self) -> pd.DataFrame:
        """Table of Company Details."""
        props = [
            ("Company Name", self.company.get("name") or "Not found"),
            ("Domain", self.domain),
            ("Website URL", self.url),
            ("Tagline / Description", self.company.get("description") or "Not found"),
            ("Industry", self.company.get("industry") or "Not found"),
            ("Headquarters / Location", self.company.get("location") or "Not found"),
            ("Careers Page", self.careers_info.get("careers_url") or "Not found"),
            ("Phone Numbers", ", ".join(self.phones) if self.phones else "Not found"),
            ("Social Profiles", ", ".join(f"{k}: {v}" for k, v in self.social_links.items()) if self.social_links else "Not found"),
        ]
        return pd.DataFrame(props, columns=["Property", "Value"])

    def to_hr_df(self) -> pd.DataFrame:
        """Table of HR & Key People Contacts."""
        cols = ["Name", "Role / Title", "Email", "Phone", "Source Page"]
        if not self.hr_contacts:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(self.hr_contacts)
        rename_map = {"name": "Name", "role": "Role / Title", "email": "Email", "phone": "Phone", "source_url": "Source Page"}
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df[[c for c in cols if c in df.columns]]

    def to_emails_df(self) -> pd.DataFrame:
        """Table of Emails Found."""
        cols = ["Email Address", "Category", "Source Page"]
        if not self.emails:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(self.emails)
        rename_map = {"email": "Email Address", "category": "Category", "source_url": "Source Page"}
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df[[c for c in cols if c in df.columns]]

    def to_socials_df(self) -> pd.DataFrame:
        """Table of Social Profiles."""
        if not self.social_links:
            return pd.DataFrame(columns=["Platform", "URL"])
        return pd.DataFrame([{"Platform": k.capitalize(), "URL": v} for k, v in self.social_links.items()])

    def to_cli_text(self) -> str:
        """Nicely formatted ASCII / Unicode output for terminal."""
        lines = []
        sep = "=" * 70
        sub_sep = "-" * 70

        lines.append(sep)
        lines.append(f" [WEBSITE ANALYSIS REPORT]: {self.url}")
        lines.append(f" Engine: {self.model_used} | Pages Scanned: {len(self.scanned_urls)}")
        lines.append(sep)

        # 1. Company Overview
        lines.append("\n[COMPANY OVERVIEW]")
        lines.append(sub_sep)
        lines.append(f"  Name:         {self.company.get('name') or 'N/A'}")
        lines.append(f"  Domain:       {self.domain}")
        lines.append(f"  Tagline/Desc: {self.company.get('description') or 'N/A'}")
        lines.append(f"  Industry:     {self.company.get('industry') or 'N/A'}")
        lines.append(f"  Location:     {self.company.get('location') or 'N/A'}")
        lines.append(f"  Careers URL:  {self.careers_info.get('careers_url') or 'N/A'}")
        if self.phones:
            lines.append(f"  Phones:       {', '.join(self.phones)}")

        # 2. HR & Key Contacts
        lines.append("\n[HR & KEY PEOPLE CONTACTS]")
        lines.append(sub_sep)
        if self.hr_contacts:
            lines.append(f"  {'Name':<24} | {'Role / Title':<26} | {'Email / Contact':<20}")
            lines.append("  " + "-" * 74)
            for c in self.hr_contacts:
                name = (c.get("name") or "Unknown")[:24]
                role = (c.get("role") or "N/A")[:26]
                contact = (c.get("email") or c.get("phone") or "N/A")[:20]
                lines.append(f"  {name:<24} | {role:<26} | {contact:<20}")
        else:
            lines.append("  No specific individual HR personnel identified by name.")
            hr_emails = [e['email'] for e in self.emails if "HR" in e.get("category", "")]
            if hr_emails:
                lines.append(f"  Dedicated HR/Recruitment Inboxes: {', '.join(hr_emails)}")

        # 3. Emails Table
        lines.append("\n[EMAILS FOUND]")
        lines.append(sub_sep)
        if self.emails:
            lines.append(f"  {'Email Address':<34} | {'Category':<22} | {'Found On'}")
            lines.append("  " + "-" * 74)
            for e in self.emails:
                email = e.get("email", "")[:34]
                cat = e.get("category", "")[:22]
                src = e.get("source_url", "")
                src_short = src.split("//")[-1][:20] if src else "N/A"
                lines.append(f"  {email:<34} | {cat:<22} | {src_short}")
        else:
            lines.append("  No direct email addresses discovered on the scanned pages.")

        # 4. Social Links
        if self.social_links:
            lines.append("\n[SOCIAL & WEB PROFILES]")
            lines.append(sub_sep)
            for platform, link in self.social_links.items():
                lines.append(f"  - {platform.capitalize():<12}: {link}")

        # 5. Open Positions / Careers
        openings = self.careers_info.get("openings_found", [])
        if openings:
            lines.append("\n[DETECTED JOB ROLES / OPENINGS]")
            lines.append(sub_sep)
            for op in openings[:8]:
                lines.append(f"  * {op}")
            if len(openings) > 8:
                lines.append(f"  ... and {len(openings) - 8} more")

        lines.append("\n" + sep)
        return "\n".join(lines)


class WebsiteAnalyzer:
    """Extracts company profile, HR contacts, emails, and phone numbers from a website."""

    def __init__(self, fetcher: Fetcher | None = None, request_config: RequestConfig | None = None):
        self.request_config = request_config or RequestConfig(delay_seconds=0.5, jitter=0.2, timeout=15)
        self._owned_fetcher = fetcher is None
        self.fetcher = fetcher or Fetcher(self.request_config)

    def close(self) -> None:
        if self._owned_fetcher and self.fetcher:
            self.fetcher.close()

    def __enter__(self) -> "WebsiteAnalyzer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def analyze(
        self,
        url: str,
        crawl_subpages: bool = True,
        max_subpages: int = 4,
        use_ai: bool = False,
        ai_provider: str = "gemini",
        api_key: str | None = None,
    ) -> AnalysisResult:
        """Analyze a website and return extracted information."""
        normalized_url = self._normalize_url(url)
        parsed = urlparse(normalized_url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        result = AnalysisResult(url=normalized_url, domain=domain)

        try:
            # 1. Crawl main page and relevant subpages (about, contact, team, careers)
            pages = self._crawl_site(normalized_url, max_subpages if crawl_subpages else 0)
            result.scanned_urls = list(pages.keys())

            if not pages:
                result.error = "Could not fetch any content from the given URL."
                return result

            # 2. Extract company information
            result.company = self._extract_company_info(pages, normalized_url, domain)

            # 3. Extract emails & classify them
            result.emails = self._extract_emails(pages, domain)

            # 4. Extract phones
            result.phones = self._extract_phones(pages)

            # 5. Extract social profiles
            result.social_links = self._extract_socials(pages)

            # 6. Extract careers information & job postings
            result.careers_info = self._extract_careers_info(pages, normalized_url)
            if result.careers_info.get("careers_url") and not result.company.get("careers_url"):
                result.company["careers_url"] = result.careers_info["careers_url"]

            # 7. Extract HR & Key Contacts
            result.hr_contacts = self._extract_hr_contacts(pages, result.emails, normalized_url)

            # 8. Optional AI Model Enhancement
            if use_ai:
                ai_data = self._analyze_with_ai(pages, result.company.get("name", domain), domain, ai_provider, api_key)
                if ai_data:
                    result.model_used = f"AI Augmented ({ai_provider})"
                    self._merge_ai_results(result, ai_data)

        except FetchError as exc:
            log.error("Fetch error analyzing %s: %s", url, exc)
            result.error = str(exc)
        except Exception as exc:
            log.exception("Unexpected error analyzing %s: %s", url, exc)
            result.error = f"{type(exc).__name__}: {exc}"

        return result

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _crawl_site(self, base_url: str, max_subpages: int) -> dict[str, str]:
        """Fetch base URL and discover relevant subpages (contact, about, careers, team)."""
        pages: dict[str, str] = {}
        parsed_base = urlparse(base_url)
        base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

        try:
            main_html = self.fetcher.get(base_url)
            pages[base_url] = main_html
        except Exception as exc:
            log.warning("Failed to fetch base URL %s: %s", base_url, exc)
            return pages

        if max_subpages <= 0:
            return pages

        # Find internal subpage links matching keywords
        soup = BeautifulSoup(main_html, "html.parser")
        candidate_urls: list[str] = []
        seen = {base_url, base_url.rstrip("/")}

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            full_url = urljoin(base_url, href)
            p = urlparse(full_url)
            # Only same origin
            if f"{p.scheme}://{p.netloc}" != base_origin:
                continue
            # Strip query & fragment for clean path matching
            clean_url = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
            if clean_url in seen:
                continue

            link_text = (a.get_text() or "").lower()
            href_lower = href.lower()

            for kw in SUBPAGE_KEYWORDS:
                if kw in href_lower or re.search(rf"\b{kw}\b", link_text):
                    seen.add(clean_url)
                    candidate_urls.append(full_url)
                    break

            if len(candidate_urls) >= max_subpages * 2:
                break

        # Fetch top candidate subpages
        for sub_url in candidate_urls[:max_subpages]:
            try:
                pages[sub_url] = self.fetcher.get(sub_url)
            except Exception as exc:
                log.warning("Could not fetch subpage %s: %s", sub_url, exc)

        return pages

    def _extract_company_info(self, pages: dict[str, str], base_url: str, domain: str) -> dict[str, Any]:
        """Extract company name, description, industry, location from JSON-LD, meta tags, and footer."""
        info: dict[str, Any] = {
            "name": None,
            "description": None,
            "industry": None,
            "location": None,
            "domain": domain,
            "url": base_url,
        }

        # Check all pages, starting with base_url
        for url, html in pages.items():
            soup = BeautifulSoup(html, "html.parser")

            # 1. JSON-LD Schema
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        stype = str(item.get("@type", "")).lower()
                        if any(t in stype for t in ("organization", "corporation", "localbusiness", "company")):
                            if not info["name"] and item.get("name"):
                                info["name"] = item["name"]
                            if not info["description"] and item.get("description"):
                                info["description"] = item["description"]
                            if not info["location"]:
                                addr = item.get("address")
                                if isinstance(addr, dict):
                                    parts = [addr.get("streetAddress"), addr.get("addressLocality"),
                                             addr.get("addressRegion"), addr.get("addressCountry")]
                                    info["location"] = ", ".join(p for p in parts if p)
                                elif isinstance(addr, str):
                                    info["location"] = addr
                except Exception:
                    continue

            # 2. OpenGraph and Meta tags
            if not info["name"]:
                og_site = soup.find("meta", property="og:site_name")
                if og_site and og_site.get("content"):
                    info["name"] = og_site["content"].strip()

            if not info["description"]:
                desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
                if desc and desc.get("content"):
                    info["description"] = desc["content"].strip()

            if not info["industry"]:
                keywords = soup.find("meta", attrs={"name": "keywords"})
                if keywords and keywords.get("content"):
                    # First 2-3 keywords often indicate industry
                    kw_list = [k.strip() for k in keywords["content"].split(",") if k.strip()]
                    if kw_list:
                        info["industry"] = ", ".join(kw_list[:3])

            # 3. Address tag
            if not info["location"]:
                addr_tag = soup.find("address")
                if addr_tag:
                    info["location"] = " ".join(addr_tag.get_text(" ").split())

        # Fallback for company name: title tag or copyright
        if not info["name"]:
            main_html = pages.get(base_url, "")
            if main_html:
                soup = BeautifulSoup(main_html, "html.parser")
                title_tag = soup.find("title")
                if title_tag and title_tag.string:
                    raw_title = title_tag.string.strip()
                    # Clean title: e.g. "Acme Corp | Official Website" -> "Acme Corp"
                    for delimiter in ["|", "-", "—", "–", ":"]:
                        if delimiter in raw_title:
                            parts = raw_title.split(delimiter)
                            raw_title = parts[0].strip() if len(parts[0].strip()) > 2 else parts[-1].strip()
                    info["name"] = raw_title

        # Final fallback: infer from domain
        if not info["name"]:
            parts = domain.split(".")
            info["name"] = parts[0].capitalize()

        return info

    def _extract_emails(self, pages: dict[str, str], domain: str) -> list[dict[str, Any]]:
        """Extract and categorize emails from all scanned pages."""
        discovered: dict[str, dict[str, Any]] = {}

        for url, html in pages.items():
            soup = BeautifulSoup(html, "html.parser")

            # 1. Mailto links
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.lower().startswith("mailto:"):
                    clean = href.split("?")[0].replace("mailto:", "").strip().lower()
                    if self._is_valid_email(clean):
                        discovered.setdefault(clean, {
                            "email": clean,
                            "category": self._categorize_email(clean),
                            "source_url": url,
                        })

            # 2. Text regex search
            text = soup.get_text(" ")
            for match in EMAIL_REGEX.findall(text):
                clean = match.strip().lower().rstrip(".,;:)")
                if self._is_valid_email(clean):
                    discovered.setdefault(clean, {
                        "email": clean,
                        "category": self._categorize_email(clean),
                        "source_url": url,
                    })

        # Sort: HR & Recruitment first, then General, then Sales, then Direct
        order = {"HR & Recruitment": 0, "General & Support": 1, "Sales & Business": 2, "Direct / Team": 3}
        return sorted(discovered.values(), key=lambda x: (order.get(x["category"], 9), x["email"]))

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        if not email or "@" not in email:
            return False
        if any(email.endswith(ext) for ext in INVALID_EMAIL_EXTENSIONS):
            return False
        parts = email.split("@")
        if len(parts) != 2:
            return False
        user, dom = parts
        if dom in DUMMY_EMAIL_DOMAINS:
            return False
        if len(user) < 1 or len(dom) < 3:
            return False
        return True

    @staticmethod
    def _categorize_email(email: str) -> str:
        prefix = email.split("@")[0].lower()
        if any(prefix.startswith(k) or prefix.endswith(k) for k in [
            "hr", "career", "careers", "job", "jobs", "talent", "recruiting",
            "recruiter", "people", "hiring", "work"
        ]):
            return "HR & Recruitment"
        if any(prefix.startswith(k) for k in [
            "info", "contact", "hello", "support", "help", "office", "inquiries", "general"
        ]):
            return "General & Support"
        if any(prefix.startswith(k) for k in [
            "sales", "marketing", "business", "press", "media", "partner", "partners"
        ]):
            return "Sales & Business"
        return "Direct / Team"

    def _extract_phones(self, pages: dict[str, str]) -> list[str]:
        """Extract phone numbers from tel: links and text regex."""
        phones: set[str] = set()

        for url, html in pages.items():
            soup = BeautifulSoup(html, "html.parser")

            # 1. tel: links
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.lower().startswith("tel:"):
                    clean = re.sub(r"[^\d+()\s-]", "", href.replace("tel:", "").strip())
                    if len(re.sub(r"\D", "", clean)) >= 7:
                        phones.add(clean)

            # 2. Text regex
            text = soup.get_text(" ")
            for match in PHONE_REGEX.findall(text):
                digits = re.sub(r"\D", "", match)
                if 7 <= len(digits) <= 15:
                    # Filter out obvious years or dates
                    if not (len(digits) == 4 or digits.startswith("202") or digits.startswith("199")):
                        clean = match.strip()
                        phones.add(clean)

        return sorted(phones)[:6]

    def _extract_socials(self, pages: dict[str, str]) -> dict[str, str]:
        """Extract social media links."""
        socials: dict[str, str] = {}
        patterns = {
            "linkedin": re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_-]+", re.I),
            "twitter": re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]+", re.I),
            "github": re.compile(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_-]+", re.I),
            "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.-]+", re.I),
            "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.-]+", re.I),
            "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:c/|channel/|user/|@)[A-Za-z0-9_-]+", re.I),
        }

        for url, html in pages.items():
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                for platform, regex in patterns.items():
                    if platform not in socials and regex.search(href):
                        socials[platform] = href

        return socials

    def _extract_careers_info(self, pages: dict[str, str], base_url: str) -> dict[str, Any]:
        """Detect careers/jobs page and list of open job roles."""
        info: dict[str, Any] = {"careers_url": None, "openings_found": []}

        # Find dedicated careers page
        for url in pages.keys():
            lower = url.lower()
            if any(k in lower for k in ["career", "careers", "jobs", "join-us", "work-with-us", "hiring"]):
                info["careers_url"] = url
                break

        # Search for job opening titles
        openings: list[str] = []
        for url, html in pages.items():
            if info["careers_url"] and url != info["careers_url"]:
                continue
            soup = BeautifulSoup(html, "html.parser")
            # Look for job containers or listings
            job_containers = soup.select(".job, .career, .opening, .position, [class*='job-'], [class*='career-']")
            for c in job_containers[:15]:
                header = c.find(["h2", "h3", "h4", "h5", "a", "strong"])
                if header:
                    title = header.get_text(" ", strip=True)
                    if 3 < len(title) < 80 and not any(w in title.lower() for w in ["search", "filter", "apply", "subscribe"]):
                        if title not in openings:
                            openings.append(title)

        info["openings_found"] = openings[:15]
        return info

    def _extract_hr_contacts(self, pages: dict[str, str], emails: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
        """Identify HR, talent acquisition, recruiter, and leadership personnel."""
        contacts: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for url, html in pages.items():
            soup = BeautifulSoup(html, "html.parser")

            # 1. JSON-LD Person schema
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if isinstance(item, dict) and "person" in str(item.get("@type", "")).lower():
                            name = item.get("name")
                            job_title = item.get("jobTitle") or item.get("roleName") or "Team Member"
                            if name and name not in seen_names:
                                seen_names.add(name)
                                contacts.append({
                                    "name": name,
                                    "role": job_title,
                                    "email": item.get("email"),
                                    "phone": item.get("telephone"),
                                    "source_url": url,
                                })
                except Exception:
                    continue

            # 2. HTML Team / Member Cards
            cards = soup.select(".member, .team-member, .person, .profile, .employee, [class*='team-'], [class*='member-']")
            for card in cards:
                name_tag = card.find(["h2", "h3", "h4", "h5", "strong", ".name"])
                role_tag = card.find([".title", ".role", ".position", "p", "span"])
                if name_tag and role_tag:
                    name = name_tag.get_text(" ", strip=True)
                    role = role_tag.get_text(" ", strip=True)
                    # Filter: role must relate to HR, Talent, Recruitment, or Leadership
                    role_lower = role.lower()
                    if any(k in role_lower for k in HR_KEYWORDS + LEADERSHIP_KEYWORDS):
                        if name and name not in seen_names and len(name.split()) <= 4:
                            seen_names.add(name)
                            card_email = None
                            mailto = card.find("a", href=lambda h: h and h.startswith("mailto:"))
                            if mailto:
                                card_email = mailto["href"].replace("mailto:", "").split("?")[0].strip()
                            contacts.append({
                                "name": name,
                                "role": role,
                                "email": card_email,
                                "phone": None,
                                "source_url": url,
                            })

        # 3. Associate any unassigned HR emails with generic HR contact entry if no specific HR person was found
        hr_emails = [e for e in emails if e["category"] == "HR & Recruitment"]
        has_hr_person = any(any(k in c["role"].lower() for k in HR_KEYWORDS) for c in contacts)
        if not has_hr_person and hr_emails:
            for hr_e in hr_emails:
                contacts.append({
                    "name": "HR Department / Recruiting Team",
                    "role": "Talent Acquisition & HR Team",
                    "email": hr_e["email"],
                    "phone": None,
                    "source_url": hr_e["source_url"],
                })

        return contacts

    def _analyze_with_ai(
        self,
        pages: dict[str, str],
        company_name: str,
        domain: str,
        provider: str,
        api_key: str | None,
    ) -> dict[str, Any] | None:
        """Call AI Model (Gemini, OpenAI, or Ollama) to extract deep HR and company entities."""
        # Check API key from argument or environment
        if not api_key:
            if provider.lower() in ("gemini", "auto"):
                api_key = os.environ.get("GEMINI_API_KEY")
            elif provider.lower() == "openai":
                api_key = os.environ.get("OPENAI_API_KEY")

        if not api_key and provider.lower() not in ("ollama", "local"):
            log.info("No AI API key found for provider %s; skipping AI enhancement.", provider)
            return None

        # Build clean condensed text representation of the website
        text_snippets = []
        for u, html in list(pages.items())[:3]:
            soup = BeautifulSoup(html, "html.parser")
            # Remove scripts, styles
            for s in soup(["script", "style", "svg", "noscript"]):
                s.decompose()
            clean_text = " ".join(soup.get_text(" ").split())[:4000]
            text_snippets.append(f"--- PAGE ({u}) ---\n{clean_text}")

        context_text = "\n\n".join(text_snippets)[:10000]

        prompt = f"""You are an expert web intelligence and corporate analyst.
Extract Company Information, HR / Recruitment Contacts, Emails, and Careers details from the following website text.

Website Domain: {domain}
Company Context: {company_name}

Text:
\"\"\"
{context_text}
\"\"\"

Respond ONLY with a valid JSON object matching this exact schema:
{{
  "company_name": "Name of the company",
  "description": "1-2 sentence company summary",
  "industry": "Industry or business sector",
  "location": "Headquarters address or city/country",
  "hr_contacts": [
    {{
      "name": "Name of HR person or recruiter",
      "role": "HR Manager / Talent Acquisition / Recruiter / etc",
      "email": "email if mentioned or null",
      "phone": "phone if mentioned or null"
    }}
  ],
  "open_positions": ["List", "of", "job", "titles", "or", "roles"],
  "phones": ["phone numbers found"]
}}
"""
        try:
            if provider.lower() in ("gemini", "auto") and api_key:
                return self._call_gemini_api(api_key, prompt)
            elif provider.lower() == "openai" and api_key:
                return self._call_openai_api(api_key, prompt)
            elif provider.lower() in ("ollama", "local"):
                return self._call_ollama_api(prompt)
        except Exception as exc:
            log.warning("AI model extraction failed: %s", exc)

        return None

    @staticmethod
    def _call_gemini_api(api_key: str, prompt: str) -> dict[str, Any] | None:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
        }
        resp = requests.post(url, json=payload, timeout=25)
        if resp.status_code == 200:
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(content)
        # Try fallback to gemini-1.5-flash if 2.5 returns error
        fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        resp_fallback = requests.post(fallback_url, json=payload, timeout=25)
        if resp_fallback.status_code == 200:
            data = resp_fallback.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(content)
        log.warning("Gemini API call failed with status %d: %s", resp.status_code, resp.text)
        return None

    @staticmethod
    def _call_openai_api(api_key: str, prompt: str) -> dict[str, Any] | None:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        log.warning("OpenAI API call failed with status %d: %s", resp.status_code, resp.text)
        return None

    @staticmethod
    def _call_ollama_api(prompt: str) -> dict[str, Any] | None:
        url = "http://localhost:11434/api/generate"
        payload = {
            "model": "llama3.2",
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            return json.loads(resp.json().get("response", "{}"))
        return None

    @staticmethod
    def _merge_ai_results(result: AnalysisResult, ai_data: dict[str, Any]) -> None:
        """Merge AI model insights into the AnalysisResult."""
        if ai_data.get("company_name") and (not result.company.get("name") or result.company["name"] == result.domain):
            result.company["name"] = ai_data["company_name"]
        if ai_data.get("description") and not result.company.get("description"):
            result.company["description"] = ai_data["description"]
        if ai_data.get("industry") and not result.company.get("industry"):
            result.company["industry"] = ai_data["industry"]
        if ai_data.get("location") and not result.company.get("location"):
            result.company["location"] = ai_data["location"]

        # Merge AI-detected HR contacts
        existing_hr_names = {c["name"].lower() for c in result.hr_contacts}
        for ai_c in ai_data.get("hr_contacts", []):
            name = ai_c.get("name")
            if name and name.lower() not in existing_hr_names:
                result.hr_contacts.append({
                    "name": name,
                    "role": ai_c.get("role", "HR / Recruiter"),
                    "email": ai_c.get("email"),
                    "phone": ai_c.get("phone"),
                    "source_url": result.url,
                })
                existing_hr_names.add(name.lower())

        # Merge open positions
        if ai_data.get("open_positions"):
            existing = set(result.careers_info.get("openings_found", []))
            for pos in ai_data["open_positions"]:
                if pos not in existing:
                    result.careers_info.setdefault("openings_found", []).append(pos)
                    existing.add(pos)

        # Merge phones
        if ai_data.get("phones"):
            existing_phones = set(result.phones)
            for p in ai_data["phones"]:
                if p not in existing_phones:
                    result.phones.append(p)
                    existing_phones.add(p)



