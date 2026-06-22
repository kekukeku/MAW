"""Karpathy 3-Stage LLM Council logic with mock mode support."""

import re
import logging
from datetime import datetime, timezone
from typing import Any

from council.config import MOCK_MODE
from council.llm_provider import query_model, query_models_parallel, LLMProviderError
from council.storage import create_conversation_skeleton, save_conversation
from project_context import build_prompt_envelope, compact_context_digest, build_context_preview_response

logger = logging.getLogger(__name__)


def _build_mock_council_result(
    prompt: str,
    council_models: list[str],
    chairman_model: str,
    context_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic council data for testing without API calls."""
    stage1 = [
        {
            "model": model,
            "response": (
                f"**Mock Stage 1 Response from {model}**\n\n"
                f"Plan for: {prompt[:200]}\n\n"
                "1. Analyze requirements\n2. Implement core changes\n3. Run tests\n4. Submit for review"
            ),
        }
        for model in council_models
    ]
    stage2 = [
        {
            "model": model,
            "ranking": (
                f"**Mock Stage 2 Ranking by {model}**\n\n"
                + "\n".join(
                    f"{j + 1}. Response {chr(65 + j)} - Strong plan with clear steps"
                    for j in range(len(council_models))
                )
            ),
        }
        for model in council_models
    ]
    aggregate_rankings = [
        {
            "model": m,
            "average_rank": float(i + 1),
            "rankings_count": len(council_models),
        }
        for i, m in enumerate(council_models)
    ]
    stage3 = {
        "model": chairman_model,
        "response": (
            f"## Chairman Synthesis (Mock)\n\n"
            f"**Objective**: Implement the following task:\n\n{prompt}\n\n"
            "**Recommended Approach**:\n"
            "1. Create/modify required files per council consensus\n"
            "2. Add tests covering the new functionality\n"
            "3. Run verification and move task to UNDER_REVIEW\n\n"
            "**Files Likely Affected**: Core application modules and tests\n\n"
            "**Risks**: Minimal in mock mode"
        ),
    }
    metadata = {"aggregate_rankings": aggregate_rankings}
    if context_pack is not None:
        metadata["contextPackVersion"] = context_pack.get("version")
        metadata["contextStatus"] = context_pack.get("summary", {}).get("status", "ready")
        metadata["contextSummary"] = {
            "includedFiles": context_pack.get("summary", {}).get("includedFiles", 0),
            "totalChars": context_pack.get("summary", {}).get("totalChars", 0),
        }
    else:
        metadata["contextPackVersion"] = None
        metadata["contextStatus"] = "unavailable"
        metadata["contextSummary"] = {"includedFiles": 0, "totalChars": 0}
    return {"stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata}


def parse_rankings_from_text(ranking_text: str, num_responses: int) -> dict[int, int]:
    """
    Parse Stage 2 ranking text into model-index -> rank (1-based) mapping.
    Maps anonymous labels (Response A/B/C) back to Stage 1 model indices.
    """
    model_ranks: dict[int, int] = {}
    pattern = re.compile(
        r"(\d+)\s*[.)]\s*(?:Response\s+)?([A-Z])\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(ranking_text):
        rank = int(match.group(1))
        letter = match.group(2).upper()
        model_idx = ord(letter) - ord("A")
        if 0 <= model_idx < num_responses:
            model_ranks[model_idx] = rank

    if not model_ranks:
        numbers = [int(n) for n in re.findall(r"\b(\d+)\b", ranking_text)]
        for idx, rank in enumerate(numbers[:num_responses]):
            model_ranks[idx] = rank

    return model_ranks


def compute_aggregate_rankings(
    stage1: list[dict],
    stage2: list[dict],
) -> list[dict[str, Any]]:
    """Compute average rank per Stage 1 model from Stage 2 anonymous rankings."""
    model_names = [s["model"] for s in stage1]
    num = len(model_names)
    rank_sums: dict[str, list[float]] = {m: [] for m in model_names}

    for stage2_item in stage2:
        ranking_text = stage2_item.get("ranking", "")
        letter_ranks = parse_rankings_from_text(ranking_text, num)
        for model_idx, rank in letter_ranks.items():
            if model_idx < len(model_names):
                rank_sums[model_names[model_idx]].append(float(rank))

    aggregate = []
    for model in model_names:
        scores = rank_sums.get(model, [])
        avg = sum(scores) / len(scores) if scores else float(num)
        aggregate.append({
            "model": model,
            "average_rank": round(avg, 2),
            "rankings_count": len(scores),
        })
    aggregate.sort(key=lambda x: x["average_rank"])
    return aggregate


async def run_council(
    prompt: str,
    context_pack: dict[str, Any] | None = None,
    council_models: list[str] | None = None,
    chairman_model: str | None = None,
    title: str | None = None,
    mock: bool | None = None,
) -> dict[str, Any]:
    """
    Run the full 3-Stage council and persist conversation JSON.
    Returns the saved conversation dict.
    """
    from council.config import DEFAULT_COUNCIL_MODELS, DEFAULT_CHAIRMAN_MODEL

    council_models = council_models or DEFAULT_COUNCIL_MODELS
    chairman_model = chairman_model or DEFAULT_CHAIRMAN_MODEL
    use_mock = MOCK_MODE if mock is None else mock

    conversation = create_conversation_skeleton(title or prompt[:80], prompt)
    conversation["context_pack"] = context_pack
    conversation["context"] = {
        "preview": build_context_preview_response(context_pack) if context_pack else None,
    }

    if use_mock:
        result = _build_mock_council_result(prompt, council_models, chairman_model, context_pack=context_pack)
    else:
        result = await _run_council_live(prompt, council_models, chairman_model, context_pack=context_pack)

    assistant_message = {
        "role": "assistant",
        "content": result["stage3"]["response"],
        "stage1": result["stage1"],
        "stage2": result["stage2"],
        "stage3": result["stage3"],
        "metadata": result["metadata"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    conversation["messages"].append(assistant_message)
    save_conversation(conversation)
    return conversation


async def _run_council_live(
    prompt: str,
    council_models: list[str],
    chairman_model: str,
    context_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute live 3-Stage council against OpenRouter."""
    context_aware_prompt = build_prompt_envelope(prompt, context_pack)
    user_messages = [{"role": "user", "content": context_aware_prompt}]

    # Stage 1: Independent responses
    logger.info("Council Stage 1: querying %d models with context pack", len(council_models))
    stage1_raw = await query_models_parallel(council_models, user_messages)
    stage1 = []
    errors = []
    for item in stage1_raw:
        if item.get("error"):
            errors.append(f"{item['model']}: {item['error']}")
        else:
            stage1.append({"model": item["model"], "response": item["response"]})

    if not stage1:
        raise LLMProviderError(f"All Stage 1 models failed: {'; '.join(errors)}", retryable=False)

    # Stage 2: Anonymous peer rankings
    logger.info("Council Stage 2: peer rankings")
    anonymized = "\n\n".join(
        f"### Response {chr(65 + i)}\n{item['response']}"
        for i, item in enumerate(stage1)
    )
    context_digest = compact_context_digest(context_pack) if context_pack else "No target project context provided."
    ranking_prompt = (
        "You are evaluating anonymous responses to a user request. "
        "Rank ALL responses from best (1) to worst. "
        "Provide your ranking as a numbered list.\n\n"
        f"**Original Context-Aware Request**:\n{context_aware_prompt}\n\n"
        f"**Project Context Digest**:\n{context_digest}\n\n"
        f"**Responses to Rank**:\n{anonymized}"
    )
    ranking_messages = [{"role": "user", "content": ranking_prompt}]
    stage2_raw = await query_models_parallel(council_models, ranking_messages)
    stage2 = []
    for item in stage2_raw:
        if item.get("error"):
            logger.warning("Stage 2 error for %s: %s", item["model"], item["error"])
            stage2.append({"model": item["model"], "ranking": "1. Response A (fallback ranking)"})
        else:
            stage2.append({"model": item["model"], "ranking": item["response"]})

    metadata = {"aggregate_rankings": compute_aggregate_rankings(stage1, stage2)}
    if context_pack is not None:
        metadata["contextPackVersion"] = context_pack.get("version")
        metadata["contextStatus"] = context_pack.get("summary", {}).get("status", "ready")
        metadata["contextSummary"] = {
            "includedFiles": context_pack.get("summary", {}).get("includedFiles", 0),
            "totalChars": context_pack.get("summary", {}).get("totalChars", 0),
        }
    else:
        metadata["contextPackVersion"] = None
        metadata["contextStatus"] = "unavailable"
        metadata["contextSummary"] = {"includedFiles": 0, "totalChars": 0}

    # Stage 3: Chairman synthesis
    logger.info("Council Stage 3: chairman synthesis")
    top_models = [r["model"] for r in metadata["aggregate_rankings"][:3]]
    synthesis_context = (
        f"**Context-Aware User Request**:\n{context_aware_prompt}\n\n"
        f"**Stage 1 Responses**:\n"
        + "\n---\n".join(f"### {s['model']}\n{s['response']}" for s in stage1)
        + f"\n\n**Aggregate Rankings**:\n"
        + "\n".join(
            f"- {r['model']}: avg rank {r['average_rank']}"
            for r in metadata["aggregate_rankings"]
        )
    )
    chairman_messages = [
        {
            "role": "user",
            "content": (
                "You are the chairman of an LLM council. Synthesize the best implementation plan "
                "from the council deliberation below. Be specific about files, steps, and acceptance criteria.\n\n"
                "IMPORTANT: If only the project blueprint (directory tree, README, and dependency files) "
                "is available, plan at the project level and do not invent specific function bodies, "
                "file contents, or exact line numbers that are not present in the provided context.\n\n"
                + synthesis_context
            ),
        }
    ]
    chairman_response = await query_model(chairman_model, chairman_messages, temperature=0.5)
    stage3 = {"model": chairman_model, "response": chairman_response}

    return {"stage1": stage1, "stage2": stage2, "stage3": stage3, "metadata": metadata}