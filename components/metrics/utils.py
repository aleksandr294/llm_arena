# Spreadsheets treat cells starting with these as formulas.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize(value: str) -> str:
    """Prefix a single quote so spreadsheets do not run text as a formula."""
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value
