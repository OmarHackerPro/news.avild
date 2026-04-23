from fastapi import APIRouter, HTTPException, Query
import httpx

router = APIRouter()

GT_LANG = {"zh": "zh-CN"}
SUPPORTED = {"az", "ru", "es", "fr", "de", "ja", "zh", "ar", "tr"}


@router.get("/translate", tags=["translate"], summary="Translate text via Google Translate")
async def translate(q: str = Query(..., max_length=500), lang: str = Query(..., max_length=10)):
    if lang not in SUPPORTED:
        raise HTTPException(status_code=400, detail="Unsupported language")

    tl = GT_LANG.get(lang, lang)
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=en&tl={tl}&dt=t&q={httpx.URL('', params={'q': q}).params}"
    )
    # Build URL cleanly
    params = {"client": "gtx", "sl": "en", "tl": tl, "dt": "t", "q": q}
    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            resp = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            raise HTTPException(status_code=502, detail="Translation service unavailable")

    if not isinstance(data, list) or not isinstance(data[0], list):
        raise HTTPException(status_code=502, detail="Unexpected translation response")

    translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
    if not translated:
        raise HTTPException(status_code=502, detail="Empty translation result")

    return {"translated": translated}
