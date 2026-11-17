"""Reuse AstrBot's ordered model fallbacks within one caller-owned time budget."""
from __future__ import annotations

import asyncio
import time

from astrbot.api import logger

FALLBACK_RESERVE_SECONDS = 15.0   # 备用模型拆成两次调用时，每次至少留这么多
FALLBACK_PREFERRED_SECONDS = 15.0  # 一次调用能拿到这么多，就不必拆成两次
MIN_ATTEMPT_SECONDS = 8.0        # 少于这个数的预算不切碎，直接留给最后一个候选
MIN_FALLBACK_SECONDS = 8.0       # 前面耗光预算时，最后兜底模型至少拿到这么多


def attempt_budgets(remaining: float, candidates_left: int) -> list[float]:
    """本轮要依次尝试的预算列表。

    主模型（``candidates_left`` 为总数时是第一个候选）先拿 ``share``；若还剩
    足够多，就**再问同一个模型一次**，而不是立刻换备用。理由是一次要给
    评委吐约 300 token 的 JSON，首字十几秒是常态：实测同一条消息 25 秒
    成功、45 秒超时、重发一次 10 秒成功（2026-11-17 09:52）。凭一次窗口
    就判它超时，等于把偶发慢当成服务不可用。

    备用候选各保留 ``FALLBACK_RESERVE_SECONDS``；预算太小就不切碎——
    只剩两三秒时请求还没发出去就超时，反而浪费掉最后一次机会。
    """
    if candidates_left <= 1:
        return [remaining]
    share = max(0.0, remaining - FALLBACK_RESERVE_SECONDS * (candidates_left - 1))
    if share > 0 and (share >= MIN_ATTEMPT_SECONDS or remaining < MIN_ATTEMPT_SECONDS):
        # 第一次给它 share 秒；剩得下第二次的整份 share 才安排重试，
        # 否则把剩余整块留给备用，别切成两边都不够用的碎片。
        if share >= FALLBACK_PREFERRED_SECONDS and remaining - 2 * share >= MIN_ATTEMPT_SECONDS:
            return [share, share]
        return [share]
    return [remaining]


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

    initial_error = ValueError('No eligible model returned valid completion text')
    last_error = initial_error
    for index, provider_id in enumerate(candidates):
        if index:
            logger.info(f'[KouHuOJ] Selecting fallback model: {provider_id}')
        candidates_left = len(candidates) - index
        attempts = attempt_budgets(deadline - time.monotonic(), candidates_left)
        if not attempts:
            # 计划里一次都排不上（预算已经耗光）。还有下一个候选就跳过它，
            # 由后面的候选兜底；最后一个也没戏时才收场。
            if index + 1 < len(candidates):
                continue
            break
        for round_index, share in enumerate(attempts):
            timeout_now = max(share, 0.0)
            if timeout_now < MIN_FALLBACK_SECONDS and index > 0 and index + 1 == len(candidates):
                # 前面几次把总预算耗光了，但还没有任何一次真正拿到答案：
                # 最后这个兜底模型照样给一个能跑完的小窗口，宁可多等 8 秒，
                # 也别发个 0 秒的请求让它必然超时（那等于白兜底）。
                timeout_now = MIN_FALLBACK_SECONDS
            started = time.monotonic()
            suffix = f' (第 {round_index + 1} 次)' if round_index else ''
            logger.info(f'[KouHuOJ] Model attempt: {provider_id} budget={timeout_now:.2f}s{suffix}')
            try:
                response = await asyncio.wait_for(
                    context.llm_generate(chat_provider_id=provider_id, **kwargs),
                    timeout=timeout_now,
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
                    f'type={type(exc).__name__} elapsed={time.monotonic() - started:.2f}s{suffix}'
                )
                # 已经超过总预算就别再给同一个模型加时了
                if deadline - time.monotonic() <= 0:
                    break
    raise last_error
