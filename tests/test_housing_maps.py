from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import app
import config
import housing_maps
import housing_service


def test_map_endpoint_rejects_tampering_and_returns_provider_png(monkeypatch):
    api_key = "fixture-rest-key"
    monkeypatch.setattr(config, "KAKAO_MAP_REST_API_KEY", api_key)
    calls = []
    png = b"\x89PNG\r\n\x1a\nfixture"

    def get(url, params, headers, **kwargs):
        assert headers == {"Authorization": "KakaoAK " + api_key}
        calls.append((url, params))
        if url.endswith("address.json"):
            body = {"documents": [{"address_name": "서울 마포구 예시로 1", "x": "126.95", "y": "37.55"}]}
            return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)
        return SimpleNamespace(headers={"Content-Type": "image/png"}, content=png, raise_for_status=lambda: None)

    monkeypatch.setattr(housing_maps.requests, "get", get)
    signed_url = housing_maps.image_url("address", "서울 마포구 예시로 1 (예시단지)")
    query = {key: items[0] for key, items in parse_qs(urlsplit(signed_url).query).items()}
    assert api_key not in signed_url
    with TestClient(app.app) as client:
        assert client.get(housing_maps.MAP_PATH, params={**query, "place": "임의 주소"}).status_code == 404
        assert not calls
        response = client.get(signed_url)
    assert response.status_code == 200 and response.content == png
    assert response.headers["Content-Type"] == "image/png"
    assert calls[0][1]["query"] == "서울 마포구 예시로 1"
    assert calls[1][1]["markers"] == "location:126.950000,37.550000|option:false"


def test_public_map_locations_follow_only_lh_addresses_and_rent_districts(monkeypatch):
    monkeypatch.setattr(config, "KAKAO_MAP_REST_API_KEY", "fixture-rest-key")
    address = "서울 마포구 예시로 1"
    notices = [{"addresses": [address]}, {"addresses": [address]}]
    districts = [{"district": "중구", "count": 4}, {"district": "마포구", "count": 9}]
    locations = housing_service._public_map_locations(notices, {"districts": districts})
    assert [item["place"] for item in locations["addresses"]] == [address]
    assert [item["place"] for item in locations["districts"]] == ["마포구", "중구"]
