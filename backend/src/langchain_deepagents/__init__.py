# LangChain Agent Module
# Mevcut Orchestrator-Worker Agent
from .dinkal_agent import (
    LangChainAgentIntegration,
    stream_agent_response,
    get_or_create_session_agent,
    clear_session_agent,
    get_session_agent_stats,
    get_mcp_server_config,
    LANGCHAIN_AGENT_AVAILABLE,
    MCP_ADAPTERS_AVAILABLE,
    MCP_HTTP_HOST,
    MCP_HTTP_PORT,
)

# Yeni ReAct Agent (Prompt Caching optimizasyonlu)
from .react_agent import (
    ReactAgent,
    create_react_agent,
    stream_react_agent_response,
    get_or_create_react_session_agent,
    clear_react_session_agent,
    get_react_session_stats,
    LANGCHAIN_AVAILABLE as REACT_LANGCHAIN_AVAILABLE,
    MCP_AVAILABLE as REACT_MCP_AVAILABLE,
)

__all__ = [
    # Mevcut Agent (Orchestrator-Worker)
    "LangChainAgentIntegration",
    "stream_agent_response",
    "get_or_create_session_agent",
    "clear_session_agent",
    "get_session_agent_stats",
    "get_mcp_server_config",
    "LANGCHAIN_AGENT_AVAILABLE",
    "MCP_ADAPTERS_AVAILABLE",
    "MCP_HTTP_HOST",
    "MCP_HTTP_PORT",
    # Yeni ReAct Agent (Prompt Caching)
    "ReactAgent",
    "create_react_agent",
    "stream_react_agent_response",
    "get_or_create_react_session_agent",
    "clear_react_session_agent",
    "get_react_session_stats",
    "REACT_LANGCHAIN_AVAILABLE",
    "REACT_MCP_AVAILABLE",
]
