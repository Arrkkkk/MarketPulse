"""Exchange suffix reference data.

Yahoo encodes the listing venue in the ticker suffix, and the venue fixes the
quote currency. That lets the overview label 29 symbols correctly for free.

The alternative is what the old code did: fetch `yfinance` `.info` for every
symbol just to read one field, which cost 21 of the original 45-second cold
start. The batch price download returns no currency at all, so without this
map the overview has to either lie (it labelled HKD prices as USD) or stay
silent.

Unknown suffix returns None, and callers render no currency rather than
guessing. A missing label is a small gap; a wrong one is misinformation.
"""

from __future__ import annotations

#: Yahoo ticker suffix -> ISO 4217 currency of the listing.
SUFFIX_CURRENCY: dict[str, str] = {
    # Americas
    ".TO": "CAD", ".V": "CAD", ".NE": "CAD", ".CN": "CAD",
    ".SA": "BRL", ".BA": "ARS", ".MX": "MXN", ".SN": "CLP",
    # Europe
    ".L": "GBP", ".IL": "USD",
    ".DE": "EUR", ".F": "EUR", ".BE": "EUR", ".MU": "EUR", ".SG": "EUR",
    ".HM": "EUR", ".HA": "EUR", ".DU": "EUR", ".BM": "EUR",
    ".PA": "EUR", ".AS": "EUR", ".BR": "EUR", ".LS": "EUR", ".IR": "EUR",
    ".MI": "EUR", ".TI": "EUR", ".MC": "EUR", ".VI": "EUR", ".AT": "EUR",
    ".HE": "EUR", ".TL": "EUR", ".RG": "EUR", ".VS": "EUR",
    ".ST": "SEK", ".OL": "NOK", ".CO": "DKK", ".IC": "ISK",
    ".SW": "CHF", ".PR": "CZK", ".BD": "HUF", ".ME": "RUB",
    # Asia-Pacific
    ".HK": "HKD", ".SS": "CNY", ".SZ": "CNY",
    ".T": "JPY", ".KS": "KRW", ".KQ": "KRW",
    ".TW": "TWD", ".TWO": "TWD",
    ".NS": "INR", ".BO": "INR",
    ".SI": "SGD", ".KL": "MYR", ".JK": "IDR", ".BK": "THB",
    ".AX": "AUD", ".CX": "AUD", ".NZ": "NZD",
    # Middle East / Africa
    ".TA": "ILS", ".SAU": "SAR", ".QA": "QAR", ".IS": "TRY",
    ".JO": "ZAR", ".CA": "EGP",
}

#: Currencies quoted in minor units that Yahoo reports as a lowercase code.
#: London quotes many lines in pence; the ISO code is still GBP.
_MINOR_UNIT_ALIASES = {"GBp": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


def normalise_currency(code: str | None) -> str | None:
    """Map a provider's currency code onto its ISO 4217 form."""
    if not code or not code.strip():
        return None
    return _MINOR_UNIT_ALIASES.get(code, code)


def currency_for_symbol(symbol: str) -> str | None:
    """Infer the quote currency from a ticker's exchange suffix.

    Returns None when the suffix is unrecognised, so a caller can omit the
    label instead of asserting something it does not know.

    A bare symbol with no dot is a US listing (AAPL, MSFT) and is USD. Index
    symbols carry a leading caret and are deliberately not guessed: ^GSPC is
    USD but ^FTSE is GBP, and the suffix carries no signal either way.
    """
    if not symbol:
        return None
    if symbol.startswith("^"):
        return None
    if "." not in symbol:
        return "USD"
    suffix = symbol[symbol.rindex(".") :].upper()
    # .TWO is three characters; check the longer form first.
    return SUFFIX_CURRENCY.get(suffix) or SUFFIX_CURRENCY.get(suffix[:3])
