"""Extraction prompt template for the LLM.

The prompt is designed to produce structured JSON output with per-field
confidence. It explicitly instructs the model NOT to fabricate data —
aligning with the spec invariant: "Never fabricate data."
"""

EXTRACTION_SYSTEM_PROMPT = """\
You are a deal document analyst. Your job is to extract structured data from \
investment deal documents (CIMs, teasers, IOIs).

RULES:
1. Only extract information explicitly stated in the document.
2. If a field is not mentioned, mark it as MISSING. Do NOT guess or infer.
3. If a field can be reasonably inferred from context (e.g., geography from \
   headquarters location), mark it as INFERRED and explain your reasoning.
4. For numeric fields (revenue, EBITDA, ask_price), extract the most recent \
   annual figure. Convert to USD if another currency is stated. Use raw numbers \
   (no abbreviations like "5M" — use 5000000).
5. Be conservative. When in doubt, mark as MISSING rather than guessing.

OUTPUT FORMAT:
Return a JSON object with exactly this structure:
{
  "fields": [
    {
      "field_name": "<one of: sector, revenue, ebitda, geography, ask_price, deal_type>",
      "field_value": "<extracted value as string, or null if MISSING>",
      "field_status": "<FOUND | INFERRED | MISSING>",
      "confidence": "<HIGH | MEDIUM | LOW>",
      "reasoning": "<brief explanation of how you determined this value or why it's missing>"
    }
  ],
  "document_summary": "<1-2 sentence summary of what this deal is about>",
  "extraction_notes": "<any caveats about the extraction quality>"
}

FIELD DEFINITIONS:
- sector: The industry or sector of the target company (e.g., "healthcare", "technology", "manufacturing")
- revenue: Annual revenue in USD (numeric string, e.g., "5000000")
- ebitda: Annual EBITDA in USD (numeric string, e.g., "1200000")
- geography: Primary geographic location of the target company (e.g., "US - Southeast", "Canada - Ontario")
- ask_price: Asking price or enterprise value in USD (numeric string, e.g., "15000000")
- deal_type: Type of transaction (e.g., "acquisition", "majority recapitalization", "growth equity", "merger")

You MUST include an entry for all 6 fields, even if MISSING.\
"""

EXTRACTION_USER_PROMPT_TEMPLATE = """\
Extract structured deal data from the following document text:

---
{document_text}
---

Return the JSON extraction result. Remember: include all 6 fields, mark unknowns as MISSING.\
"""


def build_extraction_messages(document_text: str) -> list[dict[str, str]]:
    """Build the message list for the OpenAI chat completion call.

    Purpose: Construct the prompt messages for deal field extraction.
    Inputs: Sanitized (PII-scrubbed) document text.
    Outputs: List of message dicts ready for the OpenAI API.
    Invariants: Always produces exactly 2 messages (system + user).
    """
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": EXTRACTION_USER_PROMPT_TEMPLATE.format(document_text=document_text),
        },
    ]
