# Spreadsheets treat cells starting with these as formulas.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "|")
# Some spreadsheets ignore leading blanks before the formula character.
_LEADING_BLANKS = " \t\r\n\f\v"


def neutralize(value: str) -> str:
    """Prefix a single quote so spreadsheets do not run text as a formula.

    The check looks past leading ASCII whitespace; ``|`` counts as a prefix
    (DDE-style ``|`` payloads)."""
    if value.startswith(FORMULA_PREFIXES) or value.lstrip(
        _LEADING_BLANKS
    ).startswith(FORMULA_PREFIXES):
        return "'" + value
    return value
