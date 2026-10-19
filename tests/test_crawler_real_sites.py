"""Crawler unit tests on three more real sites (plan/01-crawler.md; issue #46), saved as
fixtures on 2026-10-19 so tests never touch the network. Each site is a different shape:

- Indoor Pickleball X Brooklyn: WordPress + Elementor, no <main>, several <article>s,
  sitemap.xml redirects to a Yoast sitemap *index*.
- B&H Barber Shop: WordPress (Yoast), also a sitemap index, long service pages.
- Gnocco: Squarespace, a real flat sitemap, a robots.txt with Disallow rules.
"""

from pathlib import Path

import pytest

from app.ingest.chunk import chunk_markdown
from app.ingest.clean import clean_page, extract_html_lang, extract_og_site_name, extract_title
from app.ingest.discover import RobotsChecker, discover_links, discover_pages, has_noindex, is_blog_path, parse_sitemap

FIXTURES = Path(__file__).parent / "fixtures"

IPX_URL = "https://indoorpickleballx.com/bk/"
BH_URL = "https://bhbarbershop.com/"
GNOCCO_URL = "https://www.gnocco.com/"


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- robots.txt + sitemap ------------------------------------------------------------


@pytest.mark.parametrize("name", ["ipx", "bhbarbershop", "gnocco"])
def test_robots_without_crawl_delay_gives_none(name: str):
    assert RobotsChecker(read(f"{name}_robots.txt")).crawl_delay() is None


def test_gnocco_robots_blocks_search_and_config_but_not_content():
    checker = RobotsChecker(read("gnocco_robots.txt"))
    assert not checker.can_fetch("https://www.gnocco.com/search")
    assert not checker.can_fetch("https://www.gnocco.com/config")
    assert checker.can_fetch("https://www.gnocco.com/menu")


@pytest.mark.parametrize("name", ["ipx", "bhbarbershop"])
def test_open_robots_allow_everything(name: str):
    assert RobotsChecker(read(f"{name}_robots.txt")).can_fetch("https://example.com/anything")


@pytest.mark.parametrize("name,start", [("ipx", IPX_URL), ("bhbarbershop", BH_URL)])
def test_wordpress_sitemap_index_is_not_a_page_list(name: str, start: str):
    """Yoast's /sitemap.xml is a sitemap *index* (links to post-sitemap.xml etc.). It has
    no <url> entries, so discovery falls back to the start page + link BFS -- and must not
    queue the child .xml sitemaps as pages."""
    xml = read(f"{name}_sitemap_index.xml")
    assert "<sitemapindex" in xml
    assert parse_sitemap(xml) == []
    assert discover_pages(start, xml, read(f"{name}_robots.txt")) == [start]


def test_gnocco_sitemap_is_discovered_with_homepage_first():
    xml, robots = read("gnocco_sitemap.xml"), read("gnocco_robots.txt")
    urls = parse_sitemap(xml)
    assert len(urls) == 21
    pages = discover_pages(GNOCCO_URL, xml, robots)
    assert pages[0] == GNOCCO_URL
    assert "https://www.gnocco.com/menu" in pages
    assert len(pages) == len(set(pages))
    assert all(u.startswith("https://www.gnocco.com") for u in pages)


# --- homepage: metadata, links ---------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,title_part,og_name",
    [
        ("ipx_brooklyn.html", "Indoor Pickleball X", "Indoor Pickleball X"),
        ("bhbarbershop_home.html", "B & H Barber Shop", "B & H Barber Shop East Village NYC | Barber East Village 10009"),
        ("gnocco_home.html", "Gnocco", "Gnocco"),
    ],
)
def test_homepage_metadata(fixture: str, title_part: str, og_name: str):
    html = read(fixture)
    assert title_part in extract_title(html)
    assert extract_og_site_name(html) == og_name
    assert extract_html_lang(html) == "en-US"
    assert not has_noindex(html)


@pytest.mark.parametrize(
    "fixture,start,expected_path",
    [
        ("ipx_brooklyn.html", IPX_URL, "/membership-brooklyn"),
        ("bhbarbershop_home.html", BH_URL, "/services"),
        ("gnocco_home.html", GNOCCO_URL, "/menu"),
    ],
)
def test_homepage_links_stay_on_site_and_are_normalized(fixture: str, start: str, expected_path: str):
    links = discover_links(read(fixture), start)
    host = start.split("/")[2].removeprefix("www.")
    assert len(links) >= 10
    assert all(host in link for link in links)
    assert all(not link.endswith("/") or link.count("/") == 3 for link in links)  # no trailing slash except a bare root
    assert any(link.endswith(expected_path) for link in links)


# --- homepage: cleaning + chunking -------------------------------------------------------


def test_barbershop_homepage_keeps_services_hours_and_drops_nav():
    page = clean_page(read("bhbarbershop_home.html"))
    assert len(page.main_markdown) > 3000
    assert "Walk-ins" in page.main_markdown
    assert "Services" in page.chrome_markdown  # nav goes to chrome, not main
    assert "mens-haircut-in-east-village-nyc" in page.chrome_markdown
    chunks = chunk_markdown(page.main_markdown)
    assert len(chunks) > 5
    assert all(c.text.strip() for c in chunks)


def test_gnocco_homepage_main_has_the_pitch_and_chrome_has_the_nav():
    page = clean_page(read("gnocco_home.html"))
    assert "Gnocco Fritto" in page.main_markdown
    assert "Modena" in page.main_markdown
    assert "[Location](/locations)" in page.chrome_markdown
    assert chunk_markdown(page.main_markdown)


def test_ipx_homepage_keeps_pricing_section():
    page = clean_page(read("ipx_brooklyn.html"))
    assert "$40/session" in page.main_markdown
    assert "indoorpickleballx.com/membership-brooklyn" in page.chrome_markdown


@pytest.mark.xfail(
    strict=True,
    reason="K-bug: clean_page takes the first <article> when a page has no <main>; Elementor "
    "pages have several, so ~95% of the homepage text (hero, amenities, 13 courts, 24/7) is lost",
)
def test_ipx_homepage_main_content_is_not_cut_to_the_first_article():
    page = clean_page(read("ipx_brooklyn.html"))
    assert "Cushioned Courts" in page.main_markdown
    assert len(page.main_markdown) > 2000


def test_is_blog_path_does_not_flag_service_pages_on_real_sites():
    assert not is_blog_path("https://bhbarbershop.com/services")
    assert not is_blog_path("https://www.gnocco.com/menu")
