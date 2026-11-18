"""Reuse AstrBot's ordered model fallbacks within one caller-owned time budget."""
from __future__ import annotations

import asyncio
import time

from astrbot.api import logger

# 一次尝试至少要有这么多秒才值得发出去。实测（2026-11-18）：判题 prompt 只有
# 1486 字符，可是最快的备用模型 deepseek-flash 也要 11.6 秒——它把 1770 个
# 推理 token 都烧在思考上，最后只吐 121 字符的 JSON。15 秒窗口一抖动就废，
# 所以「给它一次机会」的下限是 20 秒。
ATTEMPT_FLOOR_SECONDS = 20.0

# AstrBot 默认对 5xx 重试 5 次（0.2→3.2 秒退避）。上游一抽风，十几次重试
# 全耗在同一个坏模型上，降级链后面的模型连请求都发不出去——现场就是这么
# 连续失败的：sol 45 秒窗口里全在 503 重试。压到 2 次，坏就快点报错，把
# 时间让给下一个模型；真正的重试由降级链自己完成。
REQUEST_MAX_RETRIES = 2


def attempt_budgets(remaining: float, candidates_left: int) -> list[float]:
    """这一次尝试该给它多少秒。

    均分是底线，但排在前面（通常是主模型）的那个该多拿一点：先把后面每个
    候选的 ``ATTEMPT_FLOOR_SECONDS`` 扣出来，剩下的整块给当前这个。所以
    90 秒 4 个候选是 30 / 20 / 20 / 20，而不是 45 / 15 / 15 / 15——后者让
    兜底模型只剩 15 秒，比实测最慢的健康模型还要紧。总预算本来就很小
    （不够每个候选 20 秒）时退回均分，切得再细也没意义。
    """
    if candidates_left <= 1:
        return [max(remaining, 0.0)]
    floor = remaining - ATTEMPT_FLOOR_SECONDS * (candidates_left - 1)
    return [max(remaining / candidates_left, floor, 0.0)]


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

    # 上游 5xx 不要靠 AstrBot 内部重试消化：那会把整个窗口烧死在一个坏模型上。
    kwargs.setdefault('request_max_retries', REQUEST_MAX_RETRIES)

    initial_error = ValueError('No eligible model returned valid completion text')
    last_error = initial_error
    for index, provider_id in enumerate(candidates):
        if index:
            logger.info(f'[KouHuOJ] Selecting fallback model: {provider_id}')
        candidates_left = len(candidates) - index
        budget = attempt_budgets(deadline - time.monotonic(), candidates_left)[0]
        if budget <= 0:
            if index + 1 < len(candidates):
                continue
            if last_error is initial_error:
                raise asyncio.TimeoutError('Model fallback total timeout exceeded')
            # 时间已经耗光，但这是最后一个候选且前面真的失败过：宁可多等这
            # 20 秒，也要真发一次请求，而不是发个 0 秒的必超时请求（那等于白兜底）。
            budget = ATTEMPT_FLOOR_SECONDS
        started = time.monotonic()
        logger.info(f'[KouHuOJ] Model attempt: {provider_id} budget={budget:.2f}s')
        try:
            response = await asyncio.wait_for(
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
