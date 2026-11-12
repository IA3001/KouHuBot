"""Reuse AstrBot's ordered model fallbacks within one caller-owned time budget."""
from __future__ import annotations

import asyncio
import time

from astrbot.api import logger

FALLBACK_RESERVE_SECONDS = 15.0   # 每个备用模型至少要有的时间
MAX_RESERVE_FRACTION = 0.5        # 备用模型总共最多占掉一半预算


def _attempt_budget(remaining: float, candidates_left: int) -> float:
    """给当前候选的时间：主模型拿大头，只为后面每个备用留少量兜底时间。

    平均分配会害死主模型：90 秒配 3 个模型时每人只有 30 秒，
    实测复杂题判题要 30–60 秒，主模型本可答出却先被判超时。
    """
    if candidates_left <= 1:
        return remaining
    reserve = min(FALLBACK_RESERVE_SECONDS * (candidates_left - 1),
                  remaining * MAX_RESERVE_FRACTION)
    return remaining - reserve


async def generate_with_fallback(
    context, *, event=None, chat_provider_id, timeout, validate=None, **kwargs
):
    """Generate with AstrBot's session fallback list and a shared deadline.

    Args:
        context: AstrBot context providing configuration and ``llm_generate``.
        event: Optional event selecting the effective session configuration.
        chat_provider_id: Primary model ID.
        timeout: Total seconds available across all model calls.
        validate: Optional synchronous response validator; false triggers fallback.
        **kwargs: Arguments forwarded unchanged to ``context.llm_generate``.

    Returns:
        The first response with nonempty completion text accepted by the validator.

    Raises:
        Exception: Last call or validation error after exhausting eligible models.
        asyncio.CancelledError: The caller cancelled this request.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    fallback_ids = []
    try:
        config = (context.get_config(umo=event.unified_msg_origin)
                  if event is not None else context.get_config())
        configured = config.get('agent_runner', {}).get('config', {}).get(
            'model', {}).get('fallback_provider_ids', [])
        if isinstance(configured, list) and all(
            isinstance(item, str) and item.strip() for item in configured
        ):
            fallback_ids = configured
    except Exception as exc:
        logger.warning(f'[KouHuOJ] Fallback configuration unavailable: {type(exc).__name__}')

    candidates = list(dict.fromkeys([chat_provider_id, *fallback_ids]))
    if kwargs.get('image_urls'):
        eligible = []
        for provider_id in candidates:
            try:
                provider = context.get_provider_by_id(provider_id)
                provider_config = getattr(provider, 'provider_config', None)
                modalities = (provider_config.get('modalities')
                              if isinstance(provider_config, dict) else None)
                if isinstance(modalities, (list, tuple, set)) and 'image' not in modalities:
                    logger.info(f'[KouHuOJ] Skipping image-incompatible model: {provider_id}')
                    continue
            except Exception:
                pass  # Unknown capability remains eligible for an actual call.
            eligible.append(provider_id)
        candidates = eligible

    last_error = ValueError('No eligible model returned valid completion text')
    for index, provider_id in enumerate(candidates):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError('Model fallback total timeout exceeded')
        share = _attempt_budget(remaining, len(candidates) - index)
        if index:
            logger.info(f'[KouHuOJ] Selecting fallback model: {provider_id}')
        started = time.monotonic()
        logger.info(f'[KouHuOJ] Model attempt: {provider_id} budget={share:.2f}s')
        try:
            response = await asyncio.wait_for(
                context.llm_generate(chat_provider_id=provider_id, **kwargs), timeout=share
            )
            completion = getattr(response, 'completion_text', None)
            if not isinstance(completion, str) or not completion.strip():
                raise ValueError('Model returned empty completion text')
            if validate is not None and not validate(response):
                raise ValueError('Model response failed validation')
            logger.info(
                f'[KouHuOJ] Model attempt succeeded: {provider_id} '
                f'elapsed={time.monotonic() - started:.2f}s'
            )
            return response
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                f'[KouHuOJ] Model attempt failed: {provider_id} '
                f'type={type(exc).__name__} elapsed={time.monotonic() - started:.2f}s'
            )
    raise last_error
