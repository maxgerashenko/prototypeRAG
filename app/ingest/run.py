"""CLI entry (plan/01-crawler.md, plan/07-knowledge-quality.md §2/§8):
`python -m app.ingest.run --url <site>`.

Pipeline (plan 07 §2): discover (priority order) -> fetch -> clean (main text + site
chrome kept apart) -> store every page -> ORGANIZE (dedupe chrome into one pseudo-page,
detect locations from site content, assign page_type/location/retrievable per page,
idempotent on re-crawl) -> CHUNK only the pages organize says changed, tagged with
their location -> delete pages no longer linked -> profile extraction -> timezone per
location (V22) -> business summary (plan 07 §6, only when its source pages changed).
`--only summary` re-runs just the summary for an already-crawled site. Indexing
(embedding) is a separate step: `python -m app.rag.index`.
"""

import argparse
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select

from app.db import tenant_session
from app.db.models import Business, BusinessProfile, Location, Page
from app.db.session import SessionLocal
from app.ingest.chunk import chunk_markdown
from app.ingest.clean import clean_page, content_hash, extract_og_site_name, extract_title
from app.ingest.discover import RobotsChecker, discover_links, discover_pages, has_noindex
from app.ingest.fetch import fetch_robots_txt, fetch_sitemap, fetch_text, make_client
from app.ingest.identity import domain_of, site_root
from app.ingest.organize import (
    chrome_pseudo_url,
    chunk_prefix,
    cut_testimonials,
    filter_junk_chunks,
    organize_business,
    prefix_chunk_drafts,
)
from app.ingest.profile import (
    PENDING_NAME,
    extract_profile_from_text,
    is_placeholder_name,
    merge_profile,
    name_from_title,
)
from app.ingest.store import delete_pages_not_in, replace_chunks, upsert_page
from app.ingest.summary import summarize_business
from app.ingest.timezone import assign_timezones


_PROFILE_FIELDS = (
    "name", "address", "phone", "email",
    "booking_policy", "price_range", "opening_hours", "languages", "place_id",
)


def _business_by_domain(domain: str) -> Business | None:
    """Cross-tenant lookup by domain: the business_id isn't known yet at this point, so
    there's no id to scope a `tenant_session` to -- same exception as the dev listing in
    app/api/businesses.py. Read-only, single row."""
    with SessionLocal() as session:
        return session.scalar(select(Business).where(Business.domain == domain))


def resolve_business(url: str, business_id: uuid.UUID | None) -> uuid.UUID:
    """Find or create the business identified by `url`'s domain (same website -> same
    business_id, regardless of which page or location of it `url` points at).

    `business_id` still works as an override, but it's an error for it to disagree with
    an existing domain match either way: a different business already owns this domain,
    or this id already belongs to a business with a different domain.
    """
    domain = domain_of(url)
    existing = _business_by_domain(domain) if domain else None
    if existing is not None:
        if business_id is not None and business_id != existing.id:
            raise SystemExit(
                f"--business-id {business_id} conflicts: domain {domain!r} already "
                f"belongs to existing business {existing.id} ({existing.name!r})"
            )
        print(f"existing business: {existing.id} ({existing.name})")
        return existing.id

    bid = business_id or uuid.uuid4()
    with tenant_session(bid) as session:
        by_id = session.get(Business, bid)
        if by_id is None:
            session.add(Business(id=bid, name=PENDING_NAME, website=site_root(url) or url, domain=domain))
        elif by_id.domain and by_id.domain != domain:
            raise SystemExit(
                f"--business-id {bid} conflicts: it already has domain {by_id.domain!r}, "
                f"which differs from {domain!r} for {url!r}"
            )
    print(f"business_id: {bid}")
    return bid


def _expand_by_links(
    start_url: str, client, robots_checker: RobotsChecker | None, max_pages: int, max_depth: int
) -> tuple[list[str], dict[str, str]]:
    """BFS from `start_url` when there's no usable sitemap. One fetch per URL, cached for reuse."""
    seen = {start_url}
    all_urls = [start_url]
    fetched: dict[str, str] = {}
    frontier = [start_url]

    for _depth in range(max_depth):
        if len(all_urls) >= max_pages or not frontier:
            break
        next_frontier = []
        for url in frontier:
            if len(all_urls) >= max_pages:
                break
            if robots_checker and not robots_checker.can_fetch(url):
                continue
            try:
                html = fetch_text(url, client)
                time.sleep(client._crawl_delay)
            except Exception as exc:
                print(f"fetch failed, skipping: {url} ({exc})")
                continue
            fetched[url] = html
            for link in discover_links(html, url):
                # robots check here too, not only before fetching the frontier: every URL in
                # all_urls is fetched later by crawl_business, so a disallowed link added here
                # would be fetched anyway (R10)
                if robots_checker and not robots_checker.can_fetch(link):
                    continue
                if link not in seen and len(all_urls) < max_pages:
                    seen.add(link)
                    next_frontier.append(link)
                    all_urls.append(link)
        frontier = next_frontier

    return all_urls[:max_pages], fetched


def _rechunk_page(session, business_id: uuid.UUID, page: Page, business_name: str) -> None:
    """Chunk one page per its organize-step metadata (A2/A4): never for the site-chrome
    pseudo-page or a page organize marked not retrievable (duplicate/blog/legal/...);
    testimonials cut (and kept in `page.testimonials` for summary.py, plan 07 §6) and
    junk chunks dropped before storing; chunks from a page that is
    itself one location's page are prefixed with "<business> <location> — " and tagged
    with that location_id so both keyword and filtered vector search can use it (A2)."""
    if page.page_type == "site_chrome":
        page.testimonials = []
        replace_chunks(session, business_id, page.id, [])
        return

    # cut before the retrievable check: a non-retrievable page's testimonials still feed
    # the summary (summary.py picks its source pages, e.g. skipping duplicate_of ones)
    cleaned_markdown, page.testimonials = cut_testimonials(page.markdown)
    if not page.retrievable:
        replace_chunks(session, business_id, page.id, [])
        return

    drafts = filter_junk_chunks(chunk_markdown(cleaned_markdown))

    location_name = None
    if page.location_id is not None:
        location = session.get(Location, page.location_id)
        location_name = location.name if location else None
    if location_name:
        drafts = prefix_chunk_drafts(drafts, chunk_prefix(business_name, location_name))

    replace_chunks(session, business_id, page.id, drafts, location_id=page.location_id)


def crawl_business(
    business_id: uuid.UUID,
    start_url: str,
    max_pages: int = 200,
    max_depth: int = 3,
    delay_seconds: float = 0.5,
    extract_profile: bool = True,
) -> dict:
    """One full crawl: discover -> fetch -> clean -> store every page -> organize
    (locations, page type, retrievable) -> chunk the pages organize touched -> delete
    pages no longer linked -> profile extraction (plan 07 §2) -> timezone per location (V22)."""
    client = make_client(delay_seconds)
    try:
        origin = urlparse(start_url)
        base_url = f"{origin.scheme}://{origin.netloc}"
        root = site_root(start_url) or f"{base_url}/"
        robots_txt = fetch_robots_txt(base_url, client)
        sitemap_xml = fetch_sitemap(base_url, client)
        robots_checker = RobotsChecker(robots_txt) if robots_txt else None

        sitemap_urls = discover_pages(start_url, sitemap_xml, robots_txt, max_pages, max_depth)
        if len(sitemap_urls) > 1:
            urls_to_crawl, fetched = sitemap_urls, {}
        else:
            urls_to_crawl, fetched = _expand_by_links(start_url, client, robots_checker, max_pages, max_depth)
        if robots_checker:
            # last guard for both paths -- e.g. a disallowed start_url stays in the BFS list
            urls_to_crawl = [u for u in urls_to_crawl if robots_checker.can_fetch(u)]

        pages_crawled = 0
        pages_changed = 0
        all_markdown: list[str] = []
        brand_name: str | None = None  # first og:site_name seen (A3) -- site-wide, so one hit is enough
        noindex_urls: set[str] = set()  # K1: honoured by organize_business as "not retrievable"

        for url in urls_to_crawl:
            try:
                html = fetched.get(url)
                if html is None:
                    html = fetch_text(url, client)
                    time.sleep(client._crawl_delay)

                if has_noindex(html):
                    noindex_urls.add(url)
                title = extract_title(html)
                cleaned = clean_page(html)
                hash_ = content_hash(cleaned.main_markdown)
                if brand_name is None:
                    brand_name = extract_og_site_name(html)

                with tenant_session(business_id) as session:
                    page, changed = upsert_page(
                        session, business_id, url, title, cleaned.main_markdown, hash_,
                        chrome_markdown=cleaned.chrome_markdown, language=cleaned.language,
                    )

                if changed:
                    pages_changed += 1
                all_markdown.append(cleaned.main_markdown)
                pages_crawled += 1
            except Exception as exc:
                print(f"skipping {url}: {exc}")

        # Brand name (A3): a site-wide signal (og:site_name), always kept in sync with
        # it on every crawl -- unlike BusinessProfile fields, businesses.name has no
        # owner-confirmation flag to protect, and this is specifically what lets a
        # re-crawl fix a name set by the old pipeline (e.g. "Bathhouse Williamsburg",
        # which conflated the brand with its default location) through the normal code
        # path instead of a hand-run SQL UPDATE.
        if brand_name:
            with tenant_session(business_id) as session:
                business = session.get(Business, business_id)
                if business is not None and business.name != brand_name:
                    business.name = brand_name

        # Organize (A1/A4, DEC-37/DEC-40): detect locations from site content (never from
        # start_url's path), assign page_type/location/retrievable per page. Must run
        # after every page this crawl touched is stored, and before chunking, since
        # chunking needs to know each page's resolved location.
        organize_result = organize_business(business_id, start_url, root, frozenset(noindex_urls))

        with tenant_session(business_id) as session:
            business = session.get(Business, business_id)
            business_name = business.name if business and not is_placeholder_name(business.name) else "the business"
            for page_id in organize_result.organized_page_ids:
                page = session.get(Page, page_id)
                if page is not None:
                    _rechunk_page(session, business_id, page, business_name)

        # keep_urls = everything we *attempted*, not just successes — a page we merely failed
        # to fetch this run hasn't disappeared from the site; only drop pages no longer linked.
        # The site-chrome pseudo-page's synthetic url is never in urls_to_crawl (it was
        # never a page to fetch), so it's added explicitly -- without this it was created
        # by organize_business above and then deleted right back out here on every run.
        with tenant_session(business_id) as session:
            deleted_pages = delete_pages_not_in(
                session, business_id, keep_urls=[*urls_to_crawl, chrome_pseudo_url(root)]
            )

        profile_extracted = False
        with tenant_session(business_id) as session:
            existing = session.get(BusinessProfile, business_id)
            confirmed = existing is not None and existing.confirmed_at is not None
            place_id = existing.place_id if existing is not None else None
        if confirmed:
            # DEC-11: once the owner has confirmed the profile it's the source of truth --
            # a re-crawl must not overwrite it (changes go to the owner for review, step 5)
            print("profile confirmed by owner, not re-extracted")
        elif extract_profile and all_markdown:
            try:
                # keep the stored place_id: the website extraction never produces one, so
                # passing None here would wipe it on every re-crawl
                merged = merge_profile(extract_profile_from_text(all_markdown), place_id=place_id)
                with tenant_session(business_id) as session:
                    profile = session.get(BusinessProfile, business_id)
                    if profile is None:
                        profile = BusinessProfile(business_id=business_id)
                        session.add(profile)
                    for field in _PROFILE_FIELDS:
                        setattr(profile, field, merged.get(field))
                    profile.updated_at = datetime.now(UTC)
                    # businesses.name starts as a placeholder (main()); replace it with the
                    # extracted name, else the start page's title -- never overwrite a real name
                    business = session.get(Business, business_id)
                    if business is not None and is_placeholder_name(business.name):
                        title = session.scalar(
                            select(Page.title).where(Page.business_id == business_id, Page.url == start_url)
                        )
                        business.name = merged.get("name") or name_from_title(title) or PENDING_NAME
                profile_extracted = True
            except Exception as exc:
                print(f"profile extraction failed: {exc}")

        # V22: after profile extraction -- a default location without an address of its
        # own falls back to the profile's. Runs even when the profile is owner-confirmed.
        timezones_set = 0
        if extract_profile:
            try:
                timezones_set = assign_timezones(business_id)
            except Exception as exc:
                print(f"timezone lookup failed: {exc}")

        # plan 07 §6: last, after organize stored the testimonials it reads
        summary_status = "skipped"
        if extract_profile:
            try:
                summary_status = summarize_business(business_id)
            except Exception as exc:
                print(f"summary failed: {exc}")
                summary_status = "failed"

        return {
            "pages_crawled": pages_crawled,
            "pages_changed": pages_changed,
            "deleted_pages": deleted_pages,
            "pages_organized": len(organize_result.organized_page_ids),
            "locations_found": organize_result.locations_found,
            "profile_extracted": profile_extracted,
            "timezones_set": timezones_set,
            "summary": summary_status,
        }
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl a business website into Postgres")
    parser.add_argument("--url", required=True)
    parser.add_argument("--business-id", type=uuid.UUID, default=None)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--no-profile", action="store_true")
    parser.add_argument("--only", choices=["summary"], help="re-run one step on the stored pages, no crawl")
    parser.add_argument("--force", action="store_true", help="with --only summary: regenerate even if unchanged")
    args = parser.parse_args()

    business_id = resolve_business(args.url, args.business_id)
    if args.only == "summary":
        print(f"summary: {summarize_business(business_id, force=args.force)}")
        return

    summary = crawl_business(
        business_id, args.url, max_pages=args.max_pages, extract_profile=not args.no_profile
    )
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
