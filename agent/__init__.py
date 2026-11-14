"""DubaiNest agent: a LangGraph StateGraph (router → one specialist → writer), spec §5.

Runs in-process for local dev and evals (AGENT_MODE=local), and inside the
AgentCore Runtime container from build step 6.
"""
