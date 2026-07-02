import re
from typing import Optional


def normalized_provider_name(provider: Optional[str], product_name: Optional[str], category: Optional[str]) -> str:
    provider_text = re.sub(r"\s+", " ", (provider or "").strip())
    provider_text = provider_text.title() if provider_text else ""
    if provider_text and not re.fullmatch(r"[A-Za-z0-9]{1,4}\*+", provider_text):
        return provider_text

    name_text = (product_name or "").strip()
    if name_text:
        tokens = []
        for token in re.split(r"[\s\-_/]+", name_text):
            if not token:
                continue
            if re.search(r"\d", token):
                break
            upper = token.upper()
            if upper in {"DIAMOND", "DIAMONDS", "UC", "POINT", "POINTS", "VOUCHER", "TOKEN", "COIN", "TOPUP", "TOP", "UP"}:
                break
            tokens.append(token)
            if len(tokens) >= 3:
                break
        if tokens:
            return " ".join(tokens)

    category_text = (category or "").strip()
    if category_text:
        return category_text
    return "Lainnya"


def normalized_category_name(category: Optional[str], provider: Optional[str]) -> str:
    source = f"{(category or '').lower()} {(provider or '').lower()}"
    if any(key in source for key in ["game", "diamond", "uc", "voucher", "mobile legends", "free fire", "pubg", "valorant"]):
        return "Games"
    if any(key in source for key in ["pulsa", "telkomsel", "indosat", "tri", "axis", "xl", "smartfren"]):
        return "Pulsa"
    if any(key in source for key in ["data", "internet", "paket", "masa aktif"]):
        return "Data"
    if any(key in source for key in ["e-money", "e money", "wallet", "ovo", "dana", "gopay", "shopeepay"]):
        return "E-Money"
    fallback = re.sub(r"\s+", " ", (category or "Lainnya").strip())
    return fallback.title() if fallback else "Lainnya"