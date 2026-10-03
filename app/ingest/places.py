"""Google Places API (New) client — live lookup only.

Places terms forbid storing most Places content permanently; only `place_id` may be
kept indefinitely (DEC-11, R9). Everything else here is for display, never written to
our tables, and reviews/ratings are never requested at all.
"""

import httpx

BASE_URL = "https://places.googleapis.com/v1"

# Deliberately no "reviews" or "rating" — R9.
_FIELD_MASK = "id,displayName,formattedAddress,internationalPhoneNumber,regularOpeningHours,priceLevel,websiteUri"


class PlacesClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client if client is not None else httpx.Client(timeout=10.0)

    def get_place(self, place_id: str) -> dict | None:
        """Live place details. None on any non-200 response or request failure."""
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": _FIELD_MASK}
        try:
            response = self.client.get(f"{BASE_URL}/places/{place_id}", headers=headers)
        except httpx.HTTPError:
            return None
        return response.json() if response.status_code == 200 else None

    def find_place_id(self, text_query: str) -> str | None:
        """Resolve a free-text search (e.g. business name + city) to a place_id."""
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": "places.id"}
        try:
            response = self.client.post(f"{BASE_URL}/places:searchText", json={"textQuery": text_query}, headers=headers)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        places = response.json().get("places")
        return places[0].get("id") if places else None

    def close(self) -> None:
        self.client.close()


def place_to_live_fields(place: dict) -> dict:
    """Map a get_place() response to our field names, for display only — never stored."""
    return {
        "name": (place.get("displayName") or {}).get("text"),
        "address": place.get("formattedAddress"),
        "phone": place.get("internationalPhoneNumber"),
        "opening_hours": place.get("regularOpeningHours"),
        "website": place.get("websiteUri"),
    }
