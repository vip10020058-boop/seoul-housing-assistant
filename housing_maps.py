"""Render Kakao maps for locations selected from official housing results."""

import hmac
import re
from urllib.parse import urlencode

import requests

import config

KAKAO_API = "https://dapi.kakao.com"
MAP_PATH = "/ui/housing/map"
SEOUL_PREFIXES = ("서울 ", "서울특별시 ")


def _signature(kind, place):
    message = f"housing-map\n{kind}\n{place}".encode()
    return hmac.digest(config.KAKAO_MAP_REST_API_KEY.encode(), message, "sha256").hex()


def image_url(kind, place):
    if not config.KAKAO_MAP_REST_API_KEY or not place or len(place) > 200:
        return ""
    return f"{MAP_PATH}?" + urlencode({"kind": kind, "place": place, "sig": _signature(kind, place)})


def valid_signature(kind, place, sig):
    return (kind in {"address", "district"} and bool(place) and len(place) <= 200
            and bool(config.KAKAO_MAP_REST_API_KEY) and bool(re.fullmatch(r"[a-f0-9]{64}", sig))
            and hmac.compare_digest(_signature(kind, place), sig))


def _get(path, params):
    response = requests.get(KAKAO_API + path, params=params,
                            headers={"Authorization": "KakaoAK " + config.KAKAO_MAP_REST_API_KEY},
                            allow_redirects=False, timeout=(3, 5))
    response.raise_for_status()
    return response


def _coordinates(kind, place):
    if kind == "district":
        office = place + "청"
        data = _get("/v2/local/search/keyword.json", {"query": f"서울 {office}", "size": 5}).json()
        matches = [item for item in data.get("documents", [])
                   if item.get("address_name", "").startswith(SEOUL_PREFIXES)
                   and re.sub(r"^서울(?:특별시|시)?\s*", "", item.get("place_name", "")) == office]
    else:
        address = re.split(r"\s*[(（]", place, maxsplit=1)[0]
        data = _get("/v2/local/search/address.json", {"query": address, "analyze_type": "exact"}).json()
        matches = [item for item in data.get("documents", [])
                   if item.get("address_name", "").startswith(SEOUL_PREFIXES)]
        if len(matches) != 1:
            return None
    if not matches:
        return None
    lon, lat = float(matches[0]["x"]), float(matches[0]["y"])
    if 126.7 <= lon <= 127.3 and 37.3 <= lat <= 37.8:
        return f"{lon:.6f},{lat:.6f}"
    return None


def image_bytes(kind, place):
    coordinates = _coordinates(kind, place)
    if coordinates is None:
        return None
    params = {"center": coordinates, "size": "640x320", "scale": 1, "lv": 7 if kind == "district" else 3}
    if kind == "address":
        params["markers"] = f"location:{coordinates}|option:false"
    response = _get("/v2/maps/staticmap", params)
    if response.headers.get("Content-Type", "").split(";", 1)[0] != "image/png":
        return None
    return response.content if response.content.startswith(b"\x89PNG\r\n\x1a\n") else None
