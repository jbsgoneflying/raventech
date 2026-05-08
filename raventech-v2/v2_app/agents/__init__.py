"""Layer 3 - Agentic Reasoning (Anthropic-native).

Phase 3 deliverables:

- ``researcher``         pulls evidence (chains, news, calendar, fundamentals)
- ``quant``              critiques numbers, requests reruns
- ``devils_advocate``    actively tries to break the trade
- ``risk_officer``       hard caps, constitution enforcement
- ``synthesizer``        produces the final desk note + dissent flag
- ``memory``             persistent vector store of past trades + episodes
- ``mcp_tools``          v2 engine endpoints exposed as MCP tools

Default model: Claude Sonnet 4.5+ (V2_ANTHROPIC_MODEL_DEFAULT).
Hard cases auto-escalate to Opus / extended thinking.
"""
