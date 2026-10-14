"""Level 1: image -> validated bill JSON."""
from __future__ import annotations

import logging

from . import prompts
from .llm import LLMClient
from .schema import check_consistency, normalize_level1, provider_from_bill_id

log = logging.getLogger(__name__)


def extract_level1(client: LLMClient, image: bytes, bill_id: str) -> dict:
    hint = provider_from_bill_id(bill_id)
    raw = client.complete_json(prompts.L1_SYSTEM, prompts.L1_USER, [image], max_tokens=3000)
    result = normalize_level1(raw, hint)

    issues = check_consistency(result)
    if issues:  # one self-correction pass: re-read the image with the specific discrepancy
        log.info("%s: consistency issues %s; re-reading", bill_id, issues)
        retry_prompt = prompts.L1_USER + "\n\nYour previous answer was:\n" + str(raw) + "\n\n" + \
            prompts.L1_RETRY.format(issues="\n".join("- " + i for i in issues))
        try:
            second = normalize_level1(
                client.complete_json(prompts.L1_SYSTEM, retry_prompt, [image], max_tokens=3000), hint)
            if len(check_consistency(second)) < len(issues):
                result = second
        except Exception as exc:  # noqa: BLE001 - keep first answer on any retry failure
            log.warning("%s: retry failed (%s); keeping first extraction", bill_id, exc)
    return result
