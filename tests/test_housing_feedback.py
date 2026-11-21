from types import SimpleNamespace

import housing_service as housing


def test_hug_public_fields_and_error_fallback(monkeypatch):
    rows = [{"CUST_NM": "예시 임대인", "ADDR": "서울 마포구 예시로", "STDD_DT": "20261101"}]
    monkeypatch.setattr(housing.config, "HUG_API_KEY", "fixture-key")
    monkeypatch.setattr(housing.requests, "get",
                        lambda *args, **kwargs: SimpleNamespace(json=lambda: rows, raise_for_status=lambda: None))
    result = housing.answer({"mode": "hug", "landlord_query": "마포구"})
    assert result["records"][0]["region"] == "서울 마포구"
    assert result["records"][0]["basis_date"] == "2026-11-01"
    rows[:] = [{"ERROR_CODE": "03", "ERROR_MSG": "NO_DATA"}]
    result = housing.answer({"mode": "hug", "landlord_query": "마포구"})
    assert "error" in result and result["sources"][0]["url"] == housing.HUG_LOOKUP_URL
    assert result["provider_code"] == "03"


def test_lh_filters_and_published_property_address(monkeypatch):
    notice = {"PAN_NM": "서울 강남구 공공임대", "PAN_ID": "123", "SPL_INF_TP_CD": "060",
              "CCR_CNNT_SYS_DS_CD": "02", "UPP_AIS_TP_CD": "06", "DTL_URL": " https://apply.lh.or.kr/"}

    def response(url, params, **kwargs):
        if "DtlInfo" in url:
            payload = [{"dsSbd": [{"LCT_ARA_ADR": "서울 강남구 예시로", "LCT_ARA_DTL_ADR": "10",
                                   "CTRT_PLC_ADR": "서울 마포구 예시 접수처"}]}]
        else:
            assert params["serviceKey"] == "test+key="
            assert len(params["PAN_ST_DT"]) == len(params["PAN_ED_DT"]) == 8
            payload = [{"dsSch": []}, {"resHeader": [{"SS_CODE": "Y"}], "dsList": [notice]}]
        return SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)

    monkeypatch.setattr(housing.requests, "get", response)
    rows, error = housing._lh_notices("test%2Bkey%3D", "강남구", "detail-key")
    assert not error
    assert rows[0]["url"] == "https://apply.lh.or.kr/"
    assert rows[0]["addresses"] == ["서울 강남구 예시로 10"]


def test_recommendation_retains_budget_when_providers_unavailable(monkeypatch):
    for name in ("UPSTAGE_API_KEY", "LAW_OC", "PUBLIC_DATA_API_KEY", "LH_API_KEY", "MOLIT_API_KEY"):
        monkeypatch.setattr(housing.config, name, "")
    result = housing.answer({"mode": "recommend", "question": "서울 주거복지 추천",
                             "profile": "newlywed", "district": "강남구", "budget_deposit": "50,000,000"})
    assert result["profile_summary"]["budget_deposit"] == 50_000_000
    assert "신혼부부" in " ".join(program["title"] for program in result["programs"])
    assert result["public_data"]["lh_status"] == result["public_data"]["rent_status"] == "unavailable"
    assert "rent_records" not in result["public_data"]
