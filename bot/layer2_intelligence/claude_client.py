from __future__ import annotations

import json

import anthropic
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.config import Settings
from bot.layer2_intelligence.prompts import DIVERGENCE_SYSTEM_PROMPT
from bot.layer2_intelligence.tools import DIVERGENCE_ANALYSIS_TOOL
from bot.models import DivergenceSignal


class ClaudeClient:
    """Claude API integration using tool_use for guaranteed structured output."""

    def __init__(self, settings: Settings) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.claude_model
        self._max_tokens = settings.claude_max_tokens

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type((
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        )),
        before_sleep=lambda retry_state: logger.warning(
            f"Claude API request failed, retrying ({retry_state.attempt_number}/3)..."
        ),
    )
    async def analyze_divergence(
        self, payload: dict, symbol: str, timeframe: str
    ) -> DivergenceSignal:
        """Send market data to Claude for divergence analysis.

        Uses tool_use with forced tool_choice to guarantee structured output.
        No JSON parsing needed — Claude must call the tool with valid schema.
        """
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[{
                "type": "text",
                "text": DIVERGENCE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=[DIVERGENCE_ANALYSIS_TOOL],
            tool_choice={"type": "tool", "name": "report_divergence_analysis"},
            messages=[{
                "role": "user",
                "content": (
                    f"Analyse this {symbol} {timeframe} data for divergences. "
                    f"Candle status: {payload.get('candle_status', 'closed')}.\n\n"
                    f"```json\n{json.dumps(payload)}\n```"
                ),
            }],
        )

        # Extract tool_use block — guaranteed by tool_choice
        for block in response.content:
            if block.type == "tool_use":
                result = block.input
                signal = DivergenceSignal(
                    symbol=symbol,
                    timeframe=timeframe,
                    **result,
                )
                logger.info(
                    f"Claude analysis: {symbol}/{timeframe} — "
                    f"detected={signal.divergence_detected}, "
                    f"confidence={signal.confidence:.2f}, "
                    f"type={signal.divergence_type}"
                )
                return signal

        # Should never reach here with tool_choice forced
        logger.error("No tool_use block in Claude response — this should not happen")
        return DivergenceSignal(
            divergence_detected=False,
            confidence=0.0,
            reasoning="Error: no tool_use block in response",
            symbol=symbol,
            timeframe=timeframe,
        )
