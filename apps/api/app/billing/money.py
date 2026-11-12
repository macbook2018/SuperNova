"""Currency minor-unit precision helpers.

Amounts in billing tables are stored in ISO currency minor units. Keep currency
exponents centralized so JPY/KRW and three-decimal currencies are not treated
as if every currency had two decimal places.
"""

_CURRENCY_MINOR_UNIT_EXPONENTS = {
    "BIF": 0,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "ISK": 0,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "UYI": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
    "CLF": 4,
    "UYW": 4,
}


def currency_minor_unit_exponent(currency: str) -> int:
    """Return the ISO 4217 minor-unit exponent, defaulting to two decimals."""
    return _CURRENCY_MINOR_UNIT_EXPONENTS.get(currency.upper(), 2)
