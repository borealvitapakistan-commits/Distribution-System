"""Money amounts in words, the way Pakistani documents write them:
2320500 -> "Rupees Twenty-Three Lakh Twenty Thousand Five Hundred Only"."""

from decimal import ROUND_HALF_UP, Decimal

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_hundred(n):
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")


def _below_thousand(n):
    hundreds, rest = divmod(n, 100)
    words = [f"{ONES[hundreds]} Hundred"] if hundreds else []
    if rest:
        words.append(_below_hundred(rest))
    return " ".join(words)


def number_in_words(n):
    """Whole number in words using crore / lakh / thousand."""
    if n == 0:
        return "Zero"
    words = []
    crore, n = divmod(n, 10_000_000)
    if crore:
        words.append(f"{number_in_words(crore)} Crore")
    lakh, n = divmod(n, 100_000)
    if lakh:
        words.append(f"{_below_hundred(lakh)} Lakh")
    thousand, n = divmod(n, 1000)
    if thousand:
        words.append(f"{_below_hundred(thousand)} Thousand")
    if n:
        words.append(_below_thousand(n))
    return " ".join(words)


def amount_in_words(amount, currency="Rupees", cents="Paisa"):
    amount = Decimal(amount or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole = int(amount)
    paisa = int((amount - whole) * 100)
    text = f"{currency} {number_in_words(whole)}"
    if paisa:
        text += f" and {_below_hundred(paisa)} {cents}"
    return text + " Only"
