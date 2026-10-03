"""CLI entry (plan/01-crawler.md): `python -m app.ingest.run --url <site>`."""

import argparse
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select

from app.db import tenant_session
from app.db.models import Business, BusinessProfile
from app.ingest.chunk import chunk_markdown
from app.ingest.clean import content_hash, extract_title, html_to_markdown
from app.ingest.discover import RobotsChecker, discover_links, discover_pages
from app.ingest.fetch import fetch_robots_txt, fetch_sitemap, fetch_text, make_client
from app.ingest.profile import extract_profile_from_text, merge_profile
from app.ingest.store import delete_pages_not_in, replace_chunks, upsert_page

_PROFILE_FIELDS = (
    "name", "address", "phone", "email",
    "booking_policy", "price_range", "opening_hours", "languages", "place_id",
)


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


def crawl_business(
    business_id: uuid.UUID,
    start_url: str,
    max_pages: int = 200,
    max_depth: int = 3,
    delay_seconds: float = 0.5,
    extract_profile: bool = True,
) -> dict:
    """One full crawl: discover → fetch → clean → chunk → store, then profile extraction."""
    client = make_client(delay_seconds)
    try:
        origin = urlparse(start_url)
        base_url = f"{origin.scheme}://{origin.netloc}"
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

        for url in urls_to_crawl:
            try:
                html = fetched.get(url)
                if html is None:
                    html = fetch_text(url, client)
                    time.sleep(client._crawl_delay)

                title = extract_title(html)
                markdown = html_to_markdown(html)
                hash_ = content_hash(markdown)

                with tenant_session(business_id) as session:
                    page, changed = upsert_page(session, business_id, url, title, markdown, hash_)
                    # only re-chunk (and wipe embeddings back to NULL) when content actually
                    # changed -- plan/01-crawler.md step 7: "only re-chunk/re-embed changed
                    # pages". Calling this unconditionally silently de-indexes every page on
                    # every re-crawl, even a no-op one, with no error to notice it by.
                    if changed:
                        replace_chunks(session, business_id, page.id, chunk_markdown(markdown))

                if changed:
                    pages_changed += 1
                all_markdown.append(markdown)
                pages_crawled += 1
            except Exception as exc:
                print(f"skipping {url}: {exc}")

        # keep_urls = everything we *attempted*, not just successes — a page we merely failed
        # to fetch this run hasn't disappeared from the site; only drop pages no longer linked.
        with tenant_session(business_id) as session:
            deleted_pages = delete_pages_not_in(session, business_id, keep_urls=urls_to_crawl)

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
                profile_extracted = True
            except Exception as exc:
                print(f"profile extraction failed: {exc}")

        return {
            "pages_crawled": pages_crawled,
            "pages_changed": pages_changed,
            "deleted_pages": deleted_pages,
            "profile_extracted": profile_extracted,
        }
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl a business website into Postgres")
    parser.add_argument("--url", required=True)
    parser.add_argument("--business-id", type=uuid.UUID, default=None)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--no-profile", action="store_true")
    args = parser.parse_args()

    business_id = args.business_id
    if business_id is None:
        business_id = uuid.uuid4()
        print(f"business_id: {business_id}")

    with tenant_session(business_id) as session:
        if session.execute(select(Business).where(Business.id == business_id)).first() is None:
            session.add(Business(id=business_id, name="(pending)", website=args.url))

    summary = crawl_business(
        business_id, args.url, max_pages=args.max_pages, extract_profile=not args.no_profile
    )
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
