"""Browser tests for web/mic-test.html (Playwright + headless Chromium).

Hermetic: the page, its REST API and the voice WebSocket are all faked inside the
browser (page.route / page.route_web_socket), the microphone is Chromium's fake device.
No server, Google or LLM. Run alone with `pytest -m ui`, skip with `-m "not ui"`.
Drafted by local qwen3.6-35b-a3b from a spec; routing/waiting bugs fixed on review.
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, WebSocketRoute, expect

pytestmark = pytest.mark.ui

# --- Fixtures ---

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "args": [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--autoplay-policy=no-user-gesture-required"
    ]}

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "permissions": ["microphone"], "viewport": {"width": 390, "height": 844}}

# --- Constants ---

BIZ_A = {
    "id": "aaaaaaaa-0000-0000-0000-000000000001",
    "name": "Bathhouse Williamsburg",
    "website": "https://www.abathhouse.com/williamsburg",
    "conversation_count": 4
}
BIZ_B = {
    "id": "aaaaaaaa-0000-0000-0000-000000000002",
    "name": "Corner Bakery",
    "website": None,
    "conversation_count": 1
}
BIZ_C = {
    "id": "aaaaaaaa-0000-0000-0000-000000000003",
    "name": "<img src=x onerror=window.__xss=1>",
    "website": "https://evil.example/",
    "conversation_count": 0
}
BUSINESSES = [BIZ_A, BIZ_B, BIZ_C]

NOW = datetime.now()
YESTERDAY = NOW - timedelta(days=1)  # draft used .replace(day=day-1): breaks on the 1st..5th
OLDER = NOW - timedelta(days=5)

CONVOS_A = [
    {"id": "cccccccc-0000-0000-0000-000000000001", "title": "Do you have a sauna", "preview": "User asked about sauna availability.", "started_at": NOW.isoformat(), "duration_s": 95, "message_count": 3},
    {"id": "cccccccc-0000-0000-0000-000000000002", "title": "Where are you located", "preview": "Location inquiry.", "started_at": YESTERDAY.isoformat(), "duration_s": 45, "message_count": 2},
    {"id": "cccccccc-0000-0000-0000-000000000003", "title": "Are you open right now", "preview": "Hours question.", "started_at": OLDER.isoformat(), "duration_s": 30, "message_count": 2},
    {"id": "cccccccc-0000-0000-0000-000000000004", "title": "Day pass price", "preview": "Pricing info.", "started_at": OLDER.isoformat(), "duration_s": 60, "message_count": 2},
]
CONVOS_B = [
    {"id": "cccccccc-0000-0000-0000-000000000005", "title": "Menu question", "preview": "Asked about menu.", "started_at": OLDER.isoformat(), "duration_s": 40, "message_count": 2},
]
CONVOS_C = []

DETAIL_A_1 = {
    "id": "cccccccc-0000-0000-0000-000000000001",
    "title": "Do you have a sauna",
    "started_at": NOW.isoformat(),
    "duration_s": 95,
    "message_count": 3,
    "messages": [
        {"role": "assistant", "content": "Hi, you've reached Bathhouse Williamsburg.", "at_s": 0},
        {"role": "user", "content": "Do you have a sauna?", "at_s": 10},
        {"role": "assistant", "content": "Yes, two saunas.", "at_s": 14}
    ]
}

CONVO_CACHE = {
    BIZ_A["id"]: CONVOS_A,
    BIZ_B["id"]: CONVOS_B,
    BIZ_C["id"]: CONVOS_C,
}

# --- Helpers ---

REQUESTED_URLS: list[str] = []

class FakeVoiceServer:
    def __init__(self, page: Page):
        self.page = page
        self.ws: WebSocketRoute | None = None
        self.url: str = ""
        self.received_json: list[dict] = []
        self.received_audio: list[bytes] = []
        page.route_web_socket(re.compile(r".*/voice/browser.*"), self._on_ws)

    def _on_ws(self, ws: WebSocketRoute):
        self.ws = ws
        self.url = ws.url
        ws.on_message(lambda msg: self._on_msg(msg))

    def _on_msg(self, msg):
        if isinstance(msg, str):
            self.received_json.append(json.loads(msg))
        else:
            self.received_audio.append(msg)

    def send_event(self, data: dict):
        if self.ws:
            self.ws.send(json.dumps(data))

    def send_audio(self, seconds: float):
        if self.ws:
            pcm = b"\x00\x00" * int(16000 * seconds)
            self.ws.send(pcm)

    def close(self, code: int = 1000, reason: str = ""):
        if self.ws:
            self.ws.close(code=code, reason=reason)

    def wait_for(self, predicate, timeout: float = 5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            self.page.wait_for_timeout(50)
        pytest.fail("Timeout waiting for predicate")

LIST_PATH = re.compile(r"/businesses/([^/]+)/conversations")
DETAIL_PATH = re.compile(r"/businesses/([^/]+)/conversations/([^/]+)")


def handle_route(route):
    path = urlparse(route.request.url).path
    REQUESTED_URLS.append(route.request.url)
    
    if path == "/web/mic-test.html":
        html_path = Path(__file__).parents[1] / "web" / "mic-test.html"
        route.fulfill(status=200, content_type="text/html", body=html_path.read_text())
    elif path == "/web/dev-reload.js":
        route.fulfill(status=200, content_type="application/javascript", body="")
    elif path == "/businesses":
        route.fulfill(status=200, content_type="application/json", body=json.dumps(BUSINESSES))
    elif m := LIST_PATH.fullmatch(path):
        convos = CONVO_CACHE.get(m.group(1), [])
        route.fulfill(status=200, content_type="application/json", body=json.dumps(convos))
    elif m := DETAIL_PATH.fullmatch(path):
        biz_id, conv_id = m.groups()
        if biz_id == BIZ_A["id"] and conv_id == "cccccccc-0000-0000-0000-000000000001":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(DETAIL_A_1))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": conv_id, "title": "Conversation", "messages": [], "started_at": NOW.isoformat(), "duration_s": 0, "message_count": 0}))
    elif "fonts.googleapis.com" in route.request.url or "fonts.gstatic.com" in route.request.url:
        route.abort()
    else:
        route.fulfill(status=404)

ORIGIN = "http://localhost:8999"  # never actually listened on


def open_page(page: Page) -> "FakeVoiceServer":
    """Route everything, install the fake voice server, then load the page. The fake
    must exist BEFORE navigation: Playwright hooks WebSocket only in documents loaded
    after route_web_socket (the draft created it later -> real connection refused)."""
    global REQUESTED_URLS
    REQUESTED_URLS = []
    page.route("**/*", handle_route)
    server = FakeVoiceServer(page)
    # localhost, not a made-up host: getUserMedia and AudioWorklet only exist in a secure
    # context (https or localhost). Every request is still answered by handle_route.
    page.goto(f"{ORIGIN}/web/mic-test.html")
    page.wait_for_load_state("networkidle")
    return server

def card(page: Page, name: str):
    """A business card's button (cards carry aria-pressed; the call button also contains
    the business name, so a plain role+name query is ambiguous once one is selected)."""
    return page.locator("button[aria-pressed]", has_text=name)


CALL_ID = "dddddddd-0000-0000-0000-000000000001"


def start_call(page: Page, server: FakeVoiceServer, biz_name: str):
    """Click the call button, wait for the (faked) socket, then send what the real server
    sends first: the conversation id. (A WebSocket never changes the page URL -- the draft
    waited for that and timed out.)"""
    page.get_by_role("button", name="Call about " + biz_name).click()
    server.wait_for(lambda: server.ws is not None)
    server.send_event({"type": "conversation_id", "id": CALL_ID})
    expect(page.get_by_text("Live", exact=True)).to_be_visible(timeout=5000)

def talk_turn(page: Page, server: FakeVoiceServer, question: str, sentences: list[str]):
    page.keyboard.down("Space")
    server.wait_for(lambda: any(m.get("type") == "ptt_start" for m in server.received_json))
    server.wait_for(lambda: len(server.received_audio) > 0)
    page.keyboard.up("Space")
    server.wait_for(lambda: any(m.get("type") == "ptt_end" for m in server.received_json))
    
    server.send_event({"type": "transcript", "text": question})
    for s in sentences:
        server.send_event({"type": "reply", "text": s})
    server.send_audio(0.2)
    server.send_event({"type": "latency", "stt_ms": 80, "llm_first_sentence_ms": 900, "tts_first_audio_ms": 1400, "total_ms": 1600})
    expect(page.get_by_text(sentences[-1])).to_be_visible(timeout=5000)

# --- Tests ---

def test_business_list_shows_cards_counts_and_hosts(page: Page):
    server = open_page(page)
    expect(card(page, "Bathhouse Williamsburg")).to_be_visible()
    expect(card(page, "Corner Bakery")).to_be_visible()
    expect(card(page, "<img src=x onerror=window.__xss=1>")).to_be_visible()
    
    expect(page.get_by_text("BW")).to_be_visible()
    expect(page.get_by_text("CB")).to_be_visible()
    expect(page.get_by_text("abathhouse.com")).to_be_visible()
    expect(page.get_by_text("No website")).to_be_visible()
    
    expect(page.get_by_text("4 chats")).to_be_visible()
    expect(page.get_by_text("1 chat")).to_be_visible()
    expect(page.get_by_text("No chats yet")).to_be_visible()
    
    call_btn = page.get_by_role("button", name="Select a business to call")
    expect(call_btn).to_be_visible()
    expect(call_btn).to_be_disabled()

def test_search_filters_by_name_and_host_and_keeps_typing(page: Page):
    server = open_page(page)
    search = page.get_by_label("Search businesses")
    
    search.fill("bakery")
    expect(card(page, "Corner Bakery")).to_be_visible()
    expect(card(page, "Bathhouse Williamsburg")).not_to_be_visible()
    
    search.fill("abathhouse")
    expect(card(page, "Bathhouse Williamsburg")).to_be_visible()
    expect(card(page, "Corner Bakery")).not_to_be_visible()
    
    search.fill("zzz")
    expect(page.get_by_text("No businesses match your search.")).to_be_visible()
    
    search.fill("")
    card(page, "Bathhouse Williamsburg").click()  # selected: Space outside the box would start a call
    search.focus()
    page.keyboard.press("Space")
    expect(search).to_have_value(" ")
    page.wait_for_timeout(300)
    assert server.ws is None, "a space typed in the search box started a call"

def test_expand_card_shows_recent_and_see_all(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    
    call_btn = page.get_by_role("button", name="Call about Bathhouse Williamsburg")
    expect(call_btn).to_be_visible()
    expect(call_btn).not_to_be_disabled()
    
    expect(page.get_by_text("Do you have a sauna", exact=True)).to_be_visible()
    expect(page.get_by_text("Where are you located")).to_be_visible()
    expect(page.get_by_text("Are you open right now")).to_be_visible()
    expect(page.get_by_text("Day pass price")).not_to_be_visible()
    
    page.get_by_text("See all 4 conversations").click()
    expect(page.get_by_text("Do you have a sauna", exact=True)).to_be_visible()
    expect(page.get_by_text("Where are you located")).to_be_visible()
    expect(page.get_by_text("Are you open right now")).to_be_visible()
    expect(page.get_by_text("Day pass price")).to_be_visible()
    expect(page.get_by_text("1:35")).to_be_visible()
    
    page.get_by_role("button", name="Back to businesses").click()
    expect(card(page, "Bathhouse Williamsburg")).to_be_visible()
    
    card(page, "Corner Bakery").click()
    expect(page.get_by_text("Menu question")).to_be_visible()
    
    card(page, "<img src=x onerror=window.__xss=1>").click()
    expect(page.get_by_text("No conversations yet. Start a call below and it will show up here.")).to_be_visible()

def test_transcript_and_continue_in_new_call(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    page.get_by_role("button", name=re.compile("Do you have a sauna")).click()
    
    expect(page.get_by_role("heading", name="Do you have a sauna")).to_be_visible()
    expect(page.get_by_text("Hi, you've reached Bathhouse Williamsburg.")).to_be_visible()
    expect(page.get_by_text("Do you have a sauna?")).to_be_visible()
    expect(page.get_by_text("Yes, two saunas.")).to_be_visible()
    expect(page.get_by_text("You · 0:10")).to_be_visible()
    expect(page.get_by_text("Call ended · 1:35")).to_be_visible()
    
    page.get_by_role("button", name="Continue in a new call").click()
    server.wait_for(lambda: server.ws is not None)
    
    assert "continue_from=cccccccc-0000-0000-0000-000000000001" in server.url
    assert "mode=ptt" in server.url
    assert "business_id=" + BIZ_A["id"] in server.url

def test_call_flow_push_to_talk(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    server.send_event({"type": "reply", "text": "Hello!"})
    server.send_audio(0.3)
    expect(page.get_by_text("Hello!")).to_be_visible(timeout=5000)
    
    page.keyboard.down("Space")
    server.wait_for(lambda: any(m.get("type") == "ptt_start" for m in server.received_json))
    expect(page.get_by_text("Listening to you…")).to_be_visible(timeout=5000)
    
    server.wait_for(lambda: len(server.received_audio) > 0)
    page.keyboard.up("Space")
    server.wait_for(lambda: any(m.get("type") == "ptt_end" for m in server.received_json))
    expect(page.get_by_text("Thinking…")).to_be_visible(timeout=5000)
    
    server.send_event({"type": "transcript", "text": "What time do you close?"})
    server.send_event({"type": "reply", "text": "We close at 9 pm."})
    server.send_event({"type": "reply", "text": "Anything else?"})
    server.send_audio(0.2)
    server.send_event({"type": "latency", "stt_ms": 80, "llm_first_sentence_ms": 900, "tts_first_audio_ms": 1400, "total_ms": 1600})
    
    expect(page.get_by_text("What time do you close?")).to_be_visible(timeout=5000)
    expect(page.get_by_text("We close at 9 pm.")).to_be_visible(timeout=5000)
    expect(page.get_by_text("Anything else?")).to_be_visible(timeout=5000)
    expect(page.get_by_text("1.4 s")).to_be_visible(timeout=5000)

def test_push_to_talk_with_mouse_and_no_speech(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    
    ptt_btn = page.get_by_role("button", name="Push to talk — hold to speak")
    ptt_btn.hover()
    page.mouse.down()
    server.wait_for(lambda: any(m.get("type") == "ptt_start" for m in server.received_json))
    page.mouse.up()
    server.wait_for(lambda: any(m.get("type") == "ptt_end" for m in server.received_json))
    
    server.send_event({"type": "no_speech"})
    expect(page.get_by_text("Didn't catch that — hold and try again")).to_be_visible(timeout=5000)

def test_barge_in_clear_marks_interrupted(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    
    server.send_event({"type": "reply", "text": "Starting response"})
    server.send_audio(0.1)
    server.send_event({"type": "clear"})
    
    expect(page.get_by_text("interrupted")).to_be_visible(timeout=5000)

def test_end_call_shows_summary_and_reads_conversation(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    talk_turn(page, server, "Question?", ["Answer."])
    
    page.get_by_role("button", name="End call").click()
    expect(page.get_by_text("Call ended")).to_be_visible()
    expect(page.get_by_text("Saved to Bathhouse Williamsburg’s conversations")).to_be_visible()
    expect(page.get_by_text("Messages")).to_be_visible()
    
    page.get_by_role("button", name="Read this conversation").click()
    server.wait_for(lambda: any(f"/conversations/{CALL_ID}" in u for u in REQUESTED_URLS))

def test_end_call_no_speech(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    
    page.get_by_role("button", name="End call").click()
    expect(page.get_by_text("Nothing was said")).to_be_visible()
    expect(page.get_by_role("button", name="Nothing was said")).to_be_disabled()

def test_escape_ends_call_and_backspace_ignored_outside_call(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    
    page.keyboard.press("Escape")
    expect(page.get_by_text("Call ended")).to_be_visible()
    
    page.get_by_role("button", name="Back to conversations").click()
    page.get_by_role("button", name="Back to businesses").click()
    page.keyboard.press("Backspace")
    expect(card(page, "Bathhouse Williamsburg")).to_be_visible()

def test_unknown_business_close_4404_returns_to_list(page: Page):
    server = open_page(page)
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    
    server.close(4404)
    expect(page.get_by_text("Unknown business")).to_be_visible()
    expect(card(page, "Bathhouse Williamsburg")).to_be_visible()

def test_server_text_is_not_html(page: Page):
    server = open_page(page)
    card(page, "<img src=x onerror=window.__xss=1>").click()
    # the name shows as literal text in the card and in the call button
    expect(page.get_by_text("<img src=x onerror=window.__xss=1>", exact=True)).to_be_visible()
    expect(page.get_by_text("Call about <img src=x onerror=window.__xss=1>")).to_be_visible()
    assert page.evaluate("window.__xss") is None
    
    card(page, "Bathhouse Williamsburg").click()
    start_call(page, server, "Bathhouse Williamsburg")
    server.send_event({"type": "error", "message": "<b>boom</b>"})
    expect(page.get_by_text("<b>boom</b>")).to_be_visible(timeout=5000)
