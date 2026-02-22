"""Claude tool_use schema definitions.

These schemas enforce structured output from Claude. No JSON parsing needed.
The tool_choice parameter forces Claude to call the tool on every request.
"""

DIVERGENCE_ANALYSIS_TOOL = {
    "name": "report_divergence_analysis",
    "description": (
        "Report the results of divergence analysis on the provided market data. "
        "You MUST call this tool exactly once with your complete analysis results. "
        "If no divergence is found, set divergence_detected to false."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "divergence_detected": {
                "type": "boolean",
                "description": "Whether a tradeable divergence was detected in the data",
            },
            "divergence_type": {
                "type": "string",
                "enum": [
                    "bullish_regular",
                    "bearish_regular",
                    "bullish_hidden",
                    "bearish_hidden",
                ],
                "description": (
                    "Type of divergence found. "
                    "Regular = reversal signal, Hidden = continuation signal. "
                    "Only include if divergence_detected is true."
                ),
            },
            "indicator": {
                "type": "string",
                "enum": ["RSI", "MACD", "OBV", "MFI", "Stochastic", "CCI", "Williams_R"],
                "description": "Primary indicator showing the divergence",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": (
                    "Confidence level from 0.0 to 1.0. "
                    "Consider: number of confirming indicators, clarity of swing points, "
                    "volume confirmation, and trend context."
                ),
            },
            "direction": {
                "type": "string",
                "enum": ["long", "short"],
                "description": "Trade direction based on the divergence signal",
            },
            "entry_price": {
                "type": "number",
                "description": "Suggested entry price based on recent price action",
            },
            "stop_loss": {
                "type": "number",
                "description": (
                    "Suggested stop loss price. Must be below entry for long, "
                    "above entry for short. Base on recent swing points and ATR."
                ),
            },
            "take_profit_1": {
                "type": "number",
                "description": "First take profit target (conservative, ~1.5-2R)",
            },
            "take_profit_2": {
                "type": "number",
                "description": "Second take profit target (moderate, ~2.5-3R)",
            },
            "take_profit_3": {
                "type": "number",
                "description": "Third take profit target (aggressive, ~4R+)",
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Brief 1-3 sentence explanation of the analysis. "
                    "Include which indicators confirm, swing point locations, "
                    "and any notable context (trend, volume, key levels)."
                ),
            },
        },
        "required": ["divergence_detected", "confidence", "reasoning"],
    },
}
