# Document Type Detection Prompt

Analyze this {page_text} of a document and determine the document type.

IMPORTANT: Return ONLY a valid JSON object with this exact structure (no markdown, no code blocks, no explanations):
{
    "docType": "MAIN_POLICY" | "ENDORSEMENT" | "RENEWAL" | "CANCELLATION"
}

Document type definitions:
- MAIN_POLICY: Main insurance policy document (ana poliçe) - Original policy document
- ENDORSEMENT: Endorsement/amendment document (zeyilname) - Document that modifies or adds to an existing policy
- RENEWAL: Policy renewal document (yenileme) - Document for renewing an existing policy
- CANCELLATION: Policy cancellation document (iptal) - Document for canceling a policy

Look for these keywords in Turkish or English:
- "ZEYİLNAME", "ZEYİL", "ENDORSEMENT", "AMENDMENT" → ENDORSEMENT
- "YENİLEME", "RENEWAL", "RENEW" → RENEWAL
- "İPTAL", "CANCELLATION", "CANCEL" → CANCELLATION
- "POLİÇE", "POLICY" (without zeyilname/renewal/cancellation) → MAIN_POLICY

If the document title or header contains "ZEYİLNAME" or "ENDORSEMENT", it is ENDORSEMENT.
If uncertain or cannot determine, default to "MAIN_POLICY".

CRITICAL: Return ONLY the JSON object, no markdown code blocks (```), no explanations, no other text. Just the JSON.
