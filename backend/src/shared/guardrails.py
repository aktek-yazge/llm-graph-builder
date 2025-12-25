# -*- coding: utf-8 -*-
"""
Guardrails Module - LLM Output Validation & Protection

LAYER 5 Features:
- Cypher injection prevention (agent output sanitization)
- PII/sensitive data masking
- Basic hallucination detection (fact-checking with graph)
- Input/output validation

Bu modül lightweight bir guardrails implementasyonu sağlar.
NeMo Guardrails veya Guardrails-AI alternatif olarak kullanılabilir.

Kullanım:
    from src.shared.guardrails import (
        validate_cypher_query,
        mask_pii,
        check_hallucination,
        GuardrailsConfig,
    )
    
    # Cypher injection kontrolü
    is_safe, sanitized = validate_cypher_query(cypher_query)
    
    # PII maskeleme
    masked_text = mask_pii(response_text)
    
    # Hallucination kontrolü
    is_valid, confidence = check_hallucination(response, graph_facts)

Environment Variables:
    GUARDRAILS_ENABLED: Enable/disable guardrails (default: true)
    GUARDRAILS_PII_MASKING: Enable PII masking (default: true)
    GUARDRAILS_CYPHER_VALIDATION: Enable Cypher validation (default: true)
    GUARDRAILS_HALLUCINATION_CHECK: Enable hallucination check (default: false)
"""

import os
import re
import logging
from typing import Tuple, List, Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Environment configuration
GUARDRAILS_ENABLED = os.getenv("GUARDRAILS_ENABLED", "true").lower() in ("true", "1", "yes")
GUARDRAILS_PII_MASKING = os.getenv("GUARDRAILS_PII_MASKING", "true").lower() in ("true", "1", "yes")
GUARDRAILS_CYPHER_VALIDATION = os.getenv("GUARDRAILS_CYPHER_VALIDATION", "true").lower() in ("true", "1", "yes")
GUARDRAILS_HALLUCINATION_CHECK = os.getenv("GUARDRAILS_HALLUCINATION_CHECK", "false").lower() in ("true", "1", "yes")

# Guardrails metrics
_guardrails_metrics = {
    "total_checks": 0,
    "cypher_blocked": 0,
    "pii_masked": 0,
    "hallucinations_detected": 0,
}


def get_guardrails_metrics() -> dict:
    """Get guardrails metrics"""
    return _guardrails_metrics.copy()


@dataclass
class GuardrailsConfig:
    """Guardrails configuration"""
    enabled: bool = True
    pii_masking: bool = True
    cypher_validation: bool = True
    hallucination_check: bool = False
    
    # PII patterns to mask
    pii_patterns: List[str] = field(default_factory=lambda: [
        # TC Kimlik No (11 haneli)
        r'\b[1-9]\d{10}\b',
        # Telefon numaraları (Türkiye)
        r'\b0?[5]\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b',
        r'\b\+90[\s.-]?\d{3}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b',
        # Email adresleri
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        # Kredi kartı numaraları (16 haneli)
        r'\b(?:\d{4}[\s.-]?){4}\b',
        # IBAN (TR ile başlayan)
        r'\bTR\d{2}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{2}\b',
    ])
    
    # Dangerous Cypher patterns (injection prevention)
    dangerous_cypher_patterns: List[str] = field(default_factory=lambda: [
        # Data modification
        r'\bCREATE\b',
        r'\bMERGE\b',
        r'\bSET\b',
        r'\bDELETE\b',
        r'\bDETACH\s+DELETE\b',
        r'\bREMOVE\b',
        r'\bDROP\b',
        # Schema modification
        r'\bCONSTRAINT\b',
        r'\bINDEX\b',
        # System commands
        r'\bCALL\s+dbms\.',
        r'\bCALL\s+db\.index',
        r'\bCALL\s+apoc\.periodic',
        # Subqueries that could modify
        r'\bFOREACH\b',
        # Comment injection
        r'//.*$',
        r'/\*.*?\*/',
    ])
    
    # Allowed Cypher patterns (whitelist)
    allowed_cypher_patterns: List[str] = field(default_factory=lambda: [
        r'\bMATCH\b',
        r'\bWHERE\b',
        r'\bRETURN\b',
        r'\bWITH\b',
        r'\bORDER\s+BY\b',
        r'\bLIMIT\b',
        r'\bSKIP\b',
        r'\bOPTIONAL\s+MATCH\b',
        r'\bUNION\b',
        r'\bCOLLECT\b',
        r'\bUNWIND\b',
        r'\bCASE\b',
        r'\bWHEN\b',
        r'\bTHEN\b',
        r'\bELSE\b',
        r'\bEND\b',
        # Read-only APOC
        r'\bCALL\s+gds\.similarity',
        r'\bCALL\s+apoc\.text\.',
        r'\bapoc\.text\.clean\b',
    ])


# Global config
_config = GuardrailsConfig(
    enabled=GUARDRAILS_ENABLED,
    pii_masking=GUARDRAILS_PII_MASKING,
    cypher_validation=GUARDRAILS_CYPHER_VALIDATION,
    hallucination_check=GUARDRAILS_HALLUCINATION_CHECK,
)


def get_config() -> GuardrailsConfig:
    """Get current guardrails config"""
    return _config


# ============================================================================
# CYPHER INJECTION PREVENTION
# ============================================================================

def validate_cypher_query(
    cypher: str,
    allow_write: bool = False,
    config: GuardrailsConfig = None,
) -> Tuple[bool, str, List[str]]:
    """
    Validate Cypher query for injection attacks.
    
    Args:
        cypher: Cypher query to validate
        allow_write: Allow write operations (default: False)
        config: Guardrails config
    
    Returns:
        (is_safe, sanitized_query, violations)
    """
    config = config or _config
    
    if not config.cypher_validation:
        return True, cypher, []
    
    _guardrails_metrics["total_checks"] += 1
    
    violations = []
    sanitized = cypher
    
    # Check for dangerous patterns
    if not allow_write:
        for pattern in config.dangerous_cypher_patterns:
            if re.search(pattern, cypher, re.IGNORECASE | re.MULTILINE):
                violations.append(f"Dangerous pattern: {pattern}")
    
    # Remove comments
    sanitized = re.sub(r'//.*$', '', sanitized, flags=re.MULTILINE)
    sanitized = re.sub(r'/\*.*?\*/', '', sanitized, flags=re.DOTALL)
    
    # Check for string injection attempts
    # Unbalanced quotes
    single_quotes = sanitized.count("'") - sanitized.count("\\'")
    double_quotes = sanitized.count('"') - sanitized.count('\\"')
    
    if single_quotes % 2 != 0:
        violations.append("Unbalanced single quotes")
    if double_quotes % 2 != 0:
        violations.append("Unbalanced double quotes")
    
    # Check for common injection patterns
    injection_patterns = [
        r"'\s*OR\s*'",  # ' OR '
        r"'\s*AND\s*'",  # ' AND '
        r";\s*--",  # ; --
        r"'\s*;\s*",  # '; 
        r"\bOR\s+1\s*=\s*1\b",  # OR 1=1
        r"\bAND\s+1\s*=\s*1\b",  # AND 1=1
    ]
    
    for pattern in injection_patterns:
        if re.search(pattern, cypher, re.IGNORECASE):
            violations.append(f"Injection pattern: {pattern}")
    
    is_safe = len(violations) == 0
    
    if not is_safe:
        _guardrails_metrics["cypher_blocked"] += 1
        logger.warning(f"⚠️ Cypher validation failed: {violations}")
    
    return is_safe, sanitized, violations


def sanitize_cypher_input(value: str) -> str:
    """
    Sanitize a value for safe inclusion in Cypher query.
    
    Args:
        value: Value to sanitize
    
    Returns:
        Sanitized value
    """
    # Escape single quotes
    sanitized = value.replace("'", "\\'")
    
    # Remove or escape dangerous characters
    sanitized = sanitized.replace("\\", "\\\\")
    sanitized = sanitized.replace("\n", " ")
    sanitized = sanitized.replace("\r", " ")
    sanitized = sanitized.replace("\t", " ")
    
    return sanitized


# ============================================================================
# PII MASKING
# ============================================================================

def mask_pii(
    text: str,
    config: GuardrailsConfig = None,
    replacement: str = "[MASKED]",
) -> Tuple[str, int]:
    """
    Mask PII (Personally Identifiable Information) in text.
    
    Args:
        text: Text to mask
        config: Guardrails config
        replacement: Replacement text for masked content
    
    Returns:
        (masked_text, mask_count)
    """
    config = config or _config
    
    if not config.pii_masking:
        return text, 0
    
    _guardrails_metrics["total_checks"] += 1
    
    masked = text
    mask_count = 0
    
    for pattern in config.pii_patterns:
        matches = re.findall(pattern, masked, re.IGNORECASE)
        if matches:
            mask_count += len(matches)
            masked = re.sub(pattern, replacement, masked, flags=re.IGNORECASE)
    
    if mask_count > 0:
        _guardrails_metrics["pii_masked"] += mask_count
        logger.info(f"🔒 PII masked: {mask_count} items")
    
    return masked, mask_count


def detect_pii(text: str, config: GuardrailsConfig = None) -> List[Dict[str, Any]]:
    """
    Detect PII in text without masking.
    
    Args:
        text: Text to check
        config: Guardrails config
    
    Returns:
        List of detected PII with type and position
    """
    config = config or _config
    
    detections = []
    
    pii_types = {
        r'\b[1-9]\d{10}\b': "TC_KIMLIK",
        r'\b0?[5]\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b': "PHONE_TR",
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b': "EMAIL",
        r'\b(?:\d{4}[\s.-]?){4}\b': "CREDIT_CARD",
        r'\bTR\d{2}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{2}\b': "IBAN",
    }
    
    for pattern, pii_type in pii_types.items():
        for match in re.finditer(pattern, text, re.IGNORECASE):
            detections.append({
                "type": pii_type,
                "start": match.start(),
                "end": match.end(),
                "value_preview": match.group()[:4] + "***",
            })
    
    return detections


# ============================================================================
# HALLUCINATION DETECTION
# ============================================================================

def check_hallucination(
    response: str,
    graph_facts: List[Dict[str, Any]],
    threshold: float = 0.7,
) -> Tuple[bool, float, List[str]]:
    """
    Check if response contains hallucinated information.
    
    Compares response against known graph facts to detect potential hallucinations.
    
    Args:
        response: LLM response text
        graph_facts: List of known facts from graph
        threshold: Confidence threshold (0-1)
    
    Returns:
        (is_valid, confidence, issues)
    """
    if not GUARDRAILS_HALLUCINATION_CHECK:
        return True, 1.0, []
    
    _guardrails_metrics["total_checks"] += 1
    
    issues = []
    
    # Extract claims from response
    # Basic: look for numbers, dates, names
    
    # Number extraction
    numbers_in_response = set(re.findall(r'\b\d+(?:,\d{3})*(?:\.\d+)?\b', response))
    
    # Check if numbers are supported by facts
    supported_numbers = set()
    for fact in graph_facts:
        fact_str = str(fact)
        for num in re.findall(r'\b\d+(?:,\d{3})*(?:\.\d+)?\b', fact_str):
            supported_numbers.add(num)
    
    unsupported_numbers = numbers_in_response - supported_numbers
    
    if unsupported_numbers and len(numbers_in_response) > 0:
        unsupported_ratio = len(unsupported_numbers) / len(numbers_in_response)
        if unsupported_ratio > 0.5:
            issues.append(f"Unsupported numbers: {list(unsupported_numbers)[:5]}")
    
    # Entity name checking
    # Extract potential entity names (capitalized words)
    entity_pattern = r'\b[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)*\b'
    entities_in_response = set(re.findall(entity_pattern, response))
    
    # Check if entities exist in facts
    supported_entities = set()
    for fact in graph_facts:
        for entity in re.findall(entity_pattern, str(fact)):
            supported_entities.add(entity.lower())
    
    unsupported_entities = []
    for entity in entities_in_response:
        if entity.lower() not in supported_entities and len(entity) > 3:
            # Check partial match
            partial_match = any(entity.lower() in s or s in entity.lower() for s in supported_entities)
            if not partial_match:
                unsupported_entities.append(entity)
    
    if unsupported_entities and len(entities_in_response) > 0:
        unsupported_ratio = len(unsupported_entities) / len(entities_in_response)
        if unsupported_ratio > 0.3:
            issues.append(f"Potentially unsupported entities: {unsupported_entities[:5]}")
    
    # Calculate confidence
    if issues:
        confidence = max(0.3, 1.0 - (len(issues) * 0.2))
        _guardrails_metrics["hallucinations_detected"] += 1
    else:
        confidence = 1.0
    
    is_valid = confidence >= threshold
    
    if not is_valid:
        logger.warning(f"⚠️ Potential hallucination detected: {issues}")
    
    return is_valid, confidence, issues


# ============================================================================
# INPUT VALIDATION
# ============================================================================

def validate_user_input(
    text: str,
    max_length: int = 10000,
    min_length: int = 1,
) -> Tuple[bool, str, List[str]]:
    """
    Validate user input.
    
    Args:
        text: User input text
        max_length: Maximum allowed length
        min_length: Minimum required length
    
    Returns:
        (is_valid, sanitized_text, errors)
    """
    errors = []
    
    if not text or len(text.strip()) < min_length:
        errors.append(f"Input too short (min {min_length} characters)")
        return False, "", errors
    
    if len(text) > max_length:
        errors.append(f"Input too long (max {max_length} characters)")
        text = text[:max_length]
    
    # Remove control characters
    sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    # Normalize whitespace
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    
    return len(errors) == 0, sanitized, errors


def validate_output(
    response: str,
    mask_pii_enabled: bool = True,
    check_cypher: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """
    Validate and sanitize LLM output.
    
    Args:
        response: LLM response text
        mask_pii_enabled: Whether to mask PII
        check_cypher: Whether to check embedded Cypher
    
    Returns:
        (sanitized_response, validation_info)
    """
    validation_info = {
        "original_length": len(response),
        "pii_masked": 0,
        "cypher_sanitized": False,
        "issues": [],
    }
    
    sanitized = response
    
    # Mask PII
    if mask_pii_enabled:
        sanitized, mask_count = mask_pii(sanitized)
        validation_info["pii_masked"] = mask_count
    
    # Check for embedded Cypher queries
    if check_cypher:
        cypher_pattern = r'```cypher\s*(.*?)```'
        cypher_matches = re.findall(cypher_pattern, sanitized, re.DOTALL | re.IGNORECASE)
        
        for cypher in cypher_matches:
            is_safe, _, violations = validate_cypher_query(cypher)
            if not is_safe:
                validation_info["issues"].extend(violations)
                validation_info["cypher_sanitized"] = True
                # Remove dangerous Cypher blocks
                sanitized = re.sub(
                    r'```cypher\s*' + re.escape(cypher) + r'```',
                    '```cypher\n[Query removed for security]\n```',
                    sanitized
                )
    
    validation_info["final_length"] = len(sanitized)
    
    return sanitized, validation_info

