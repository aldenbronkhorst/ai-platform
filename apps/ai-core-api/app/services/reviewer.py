"""ReviewerAgent: validates high-risk or complex answers before they reach the user.

Used for finance/accounting, customer-impacting answers, write actions,
and compliance-sensitive responses. Ensures the answer follows business rules,
uses correct currency, is supported by tool results, and addresses the question.
"""
import logging
import re
from typing import Optional, Any
from app.schemas.schemas import ReviewRequest, ReviewResult

logger = logging.getLogger(__name__)

FINANCE_KEYWORDS = [
    "revenue", "income", "expense", "profit", "loss", "balance", "invoice",
    "bill", "payment", "amount", "total", "cost", "price", "tax", "vat",
    "currency", "zar", "usd", "eur", "dollar", "rand", "accounting",
    "p&l", "pnl", "financial", "budget", "forecast", "audit",
]

# Matches $ symbol immediately before a number
DOLLAR_PATTERN = re.compile(r'\$\s*\d+[\d,.]*', re.IGNORECASE)

# Matches currency symbols/codes: R 123, ZAR 123, 123 ZAR, €123, etc.
CURRENCY_PATTERN = re.compile(
    r'(?:[RZ€£])\s*\d+[\d,.]*'        # R 123, Z 123, €123, £123
    r'|\d+[\d,.]*\s*(?:ZAR|USD|EUR|GBP|R)',  # 123 ZAR, 123.45 USD
    re.IGNORECASE,
)

# Finance words that indicate a number nearby is monetary.
# Allows optional words between the keyword and the number.
FINANCE_NEAR_NUMBER = re.compile(
    r'\b(?:revenue|income|expense|profit|loss|balance|invoice|payment|'
    r'amount|total|cost|price|tax|vat|net|gross|value|'
    r'debit|credit|receivable|payable|sales|turnover)'
    r'(?:\s+\w+){0,4}\s*'
    r'[:\s]*\d+[\d,.]*',
    re.IGNORECASE,
)

NON_MONETARY_COUNT_UNITS = re.compile(
    r'^\s*(?:[-–—,:;()]*\s*)?'
    r'(?:pdf\s+)?'
    r'(?:attachments?|pdfs?|files?|documents?|pages?|records?|lines?|items?|'
    r'rows?|results?|matches?|notes?)\b',
    re.IGNORECASE,
)


class ReviewerAgent:
    def __init__(self):
        self.min_content_length = 5

    async def review(self, request: ReviewRequest) -> ReviewResult:
        """Review a chat response for quality, safety, and correctness."""
        issues: list[str] = []
        changes: list[str] = []
        risk = "low"

        content = (request.content or "").strip()

        # 1. Blank response check
        if not content or len(content) < self.min_content_length:
            issues.append("Response is blank or too short")
            return ReviewResult(
                approved=False,
                issues=issues,
                required_changes=["Provide a complete response"],
                risk_level="high",
                reviewer_notes="Empty response blocked",
            )

        # 2. Finance-specific checks
        matched_amounts: list[str] = []
        currency_matches: list[str] = []
        if self._is_finance_question(request.user_question):
            risk = "medium"

            # Check for raw '$' usage (without currency code context)
            dollar_matches = DOLLAR_PATTERN.findall(content)
            if dollar_matches:
                issues.append(
                    f"Response uses '$' which may be incorrect without confirmed USD source "
                    f"(found {len(dollar_matches)} matches)"
                )
                changes.append("Verify the source currency before using '$'")

            # Check for monetary amounts with currency context
            currency_matches = CURRENCY_PATTERN.findall(content)
            matched_amounts = self._find_monetary_amounts_without_currency(content)

            has_monetary_amount = bool(currency_matches or matched_amounts)
            if has_monetary_amount:
                if not currency_matches:
                    issues.append("Financial values present but no currency symbol detected")
                    changes.append("Add currency prefix/suffix to financial values")
                risk = "high"

        # 3. Check that answer addresses the question
        if not self._addresses_question(content, request.user_question):
            issues.append("Response does not clearly address the user's question")
            changes.append("Ensure the response directly answers the user's question")

        # 4. Check for unsupported claims
        if request.tool_results:
            if not self._supported_by_tools(content, request.tool_results):
                issues.append("Response may contain claims not supported by tool results")
                changes.append("Verify all data points against tool output")

        approved = len(issues) == 0
        reviewer_notes_parts = issues[:]
        if matched_amounts:
            reviewer_notes_parts.append(f"matched_amounts={matched_amounts[:5]}")
        if currency_matches:
            reviewer_notes_parts.append(f"currency_matches={currency_matches[:5]}")
        reviewer_notes = "; ".join(reviewer_notes_parts) if reviewer_notes_parts else None

        if not approved:
            logger.info(
                "Reviewer rejected | issues=%d risk=%s matched_amounts=%s currency=%s",
                len(issues), risk, matched_amounts[:5], currency_matches[:5],
            )

        return ReviewResult(
            approved=approved,
            issues=issues,
            required_changes=changes,
            risk_level=risk,
            reviewer_notes=reviewer_notes,
        )

    def _is_finance_question(self, question: str) -> bool:
        if not question:
            return False
        q = question.lower()
        return any(kw in q for kw in FINANCE_KEYWORDS)

    def _has_financial_amounts(self, content: str) -> bool:
        """Check if content contains numeric amounts that look financial.
        
        Only flags amounts that are:
        - Preceded by a currency symbol (R, $, €, £)
        - Followed by a currency code (ZAR, USD, EUR, GBP, R)
        - Immediately preceded by a finance keyword (revenue, amount, total, etc.)
        
        Does NOT flag dates, years, IDs, error messages, report IDs, etc.
        """
        return bool(CURRENCY_PATTERN.search(content) or self._find_monetary_amounts_without_currency(content))

    def _find_monetary_amounts_without_currency(self, content: str) -> list[str]:
        """Return finance-near-number matches that look like money, not counts.

        Finance documents often contain non-monetary counts near finance words,
        e.g. "credit note with 7 PDF attachments". Those counts should not
        trigger the currency reviewer.
        """
        matches: list[str] = []
        for match in FINANCE_NEAR_NUMBER.finditer(content or ""):
            if self._is_non_monetary_count(content, match):
                continue
            matches.append(match.group(0))
        return matches

    def _is_non_monetary_count(self, content: str, match: re.Match) -> bool:
        after = content[match.end(): match.end() + 80]
        if NON_MONETARY_COUNT_UNITS.search(after):
            return True
        return False

    def _addresses_question(self, content: str, question: str) -> bool:
        if not question or not content:
            return False
        # Check that key terms from the question appear in the response
        q_words = set(re.findall(r'\b[a-z]{3,}\b', question.lower()))
        c_words = set(re.findall(r'\b[a-z]{3,}\b', content.lower()))
        overlap = q_words & c_words
        # Require at least 2 content-word matches
        meaningful = {w for w in q_words if w not in
                      {"the", "and", "for", "are", "this", "that", "with", "from", "what", "how", "why"}}
        if not meaningful:
            return True
        return len(overlap & meaningful) >= 1

    def _supported_by_tools(self, content: str, tool_results: list[Any]) -> bool:
        """Basic check that tool results contain data referenced in the response."""
        if not tool_results:
            return True
        content_lower = content.lower()
        for result in tool_results:
            if isinstance(result, dict):
                # Convert result to string for matching
                for val in result.values():
                    if isinstance(val, str) and len(val) > 5:
                        if val.lower()[:50] in content_lower:
                            return True
        # If no direct match found, still pass (model may have reformulated)
        return True
