"""Reuse AstrBot's ordered model fallbacks within one caller-owned time budget."""
from __future__ import annotations

import asyncio
import time

from astrbot.api import logger

# 真实三级提示实测：sol 47.55s，DeepSeek 48.63s。短判题 JSON 的耗时
# 不能代表所有任务；禁止再按候选数量把正常请求切成 15～20 秒。
ATTEMPT_MAX_SECONDS = 60.0

# 两次总尝试（不是两次重试）；由降级链接管持续的上游故障。
REQUEST_MAX_RETRIES = 2


def attempt_budgets(remaining: float, candidates_left: int) -> list[float]:
    """Give each actual attempt a usable window, bounded by the shared deadline.

    Adding more configured backups must not shorten otherwise healthy calls.
    A 120s request permits two full 60s attempts; quick errors leave their unused
    time available for subsequent candidates. Not every candidate is guaranteed
    a call when earlier providers exhaust the caller's total budget.
    """
    return [min(max(remaining, 0.0), ATTEMPT_MAX_SECONDS)]


# Opt-in long judges: still reserve a full normal slot for fallback in 240s.
LONG_JUDGE_TOTAL_SECONDS = 240.0
LONG_JUDGE_ATTEMPT_SECONDS = 180.0


async def _long_judge_response(context, provider_id, kwargs):
    """Consume the provider stream privately; only its completed response may score."""
    provider = context.get_provider_by_id(provider_id)
    stream_chat = getattr(provider, 'text_chat_stream', None)
    if stream_chat is not None:
        stream = stream_chat(**kwargs)
        try:
            async for response in stream:
                if not response.is_chunk:
                    return response
            raise ValueError('Judge stream ended without a final response')
        except NotImplementedError:
            pass  # Providers with no streaming implementation can still judge.
        finally:
            await stream.aclose()
    return await context.llm_generate(chat_provider_id=provider_id, **kwargs)


async def generate_with_fallback(
    context, *, event=None, chat_provider_id, timeout, validate=None, long_judge=False, **kwargs
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
        runner = config.get('agent', config.get('agent_runner', {}))
        configured = runner.get('config', {}).get(
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

    # 上游 5xx 不要靠 AstrBot 内部重试消化：那会把整个窗口烧死在一个坏模型上。
    kwargs.setdefault('request_max_retries', REQUEST_MAX_RETRIES)

    initial_error = ValueError('No eligible model returned valid completion text')
    last_error = initial_error
    for index, provider_id in enumerate(candidates):
        if index:
            logger.info(f'[KouHuOJ] Selecting fallback model: {provider_id}')
        candidates_left = len(candidates) - index
        remaining = deadline - time.monotonic()
        budget = (min(remaining, LONG_JUDGE_ATTEMPT_SECONDS) if long_judge else
                  attempt_budgets(remaining, candidates_left)[0])
        if budget <= 0:
            raise asyncio.TimeoutError('Model fallback total timeout exceeded') from last_error
        started = time.monotonic()
        mode = ' long_judge' if long_judge else ''
        logger.info(f'[KouHuOJ] Model attempt: {provider_id} budget={budget:.2f}s{mode}')
        try:
            response = await asyncio.wait_for(
                _long_judge_response(context, provider_id, kwargs) if long_judge else
                context.llm_generate(chat_provider_id=provider_id, **kwargs),
                timeout=budget,
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
