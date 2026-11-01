"""Google Places client (app/ingest/places.py). No network: httpx's MockTransport answers.
Guards the Places terms (DEC-11, R9): no reviews/ratings requested, nothing but place_id kept."""

import httpx

from app.ingest.places import PlacesClient, place_to_live_fields


def _client(handler):
    return PlacesClient("KEY", httpx.Client(transport=httpx.MockTransport(handler)))


def test_get_place_sends_key_and_a_field_mask_without_reviews_or_rating():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), headers=request.headers)
        return httpx.Response(200, json={"id": "p1"})

    assert _client(handler).get_place("p1") == {"id": "p1"}
    assert seen["url"] == "https://places.googleapis.com/v1/places/p1"
    assert seen["headers"]["x-goog-api-key"] == "KEY"
    mask = seen["headers"]["x-goog-fieldmask"]
    assert "reviews" not in mask and "rating" not in mask and "displayName" in mask


def test_get_place_returns_none_on_error_status_or_network_failure():
    assert _client(lambda r: httpx.Response(404)).get_place("p") is None

    def boom(request):
        raise httpx.ConnectError("down")

    assert _client(boom).get_place("p") is None


def test_find_place_id_posts_the_query_and_takes_the_first_hit():
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(200, json={"places": [{"id": "first"}, {"id": "second"}]})

    assert _client(handler).find_place_id("Sauna Brooklyn") == "first"
    assert b"Sauna Brooklyn" in seen["body"]


def test_find_place_id_is_none_without_results_error_or_network():
    assert _client(lambda r: httpx.Response(200, json={})).find_place_id("x") is None
    assert _client(lambda r: httpx.Response(200, json={"places": []})).find_place_id("x") is None
    assert _client(lambda r: httpx.Response(500)).find_place_id("x") is None

    def boom(request):
        raise httpx.ConnectError("down")

    assert _client(boom).find_place_id("x") is None


def test_place_to_live_fields_maps_names_and_tolerates_missing_parts():
    full = {"displayName": {"text": "Sauna"}, "formattedAddress": "1 Main St", "internationalPhoneNumber": "+1 555",
            "regularOpeningHours": {"weekdayDescriptions": []}, "websiteUri": "https://s.example"}
    assert place_to_live_fields(full) == {"name": "Sauna", "address": "1 Main St", "phone": "+1 555",
                                          "opening_hours": {"weekdayDescriptions": []}, "website": "https://s.example"}
    assert place_to_live_fields({}) == {"name": None, "address": None, "phone": None,
                                        "opening_hours": None, "website": None}
