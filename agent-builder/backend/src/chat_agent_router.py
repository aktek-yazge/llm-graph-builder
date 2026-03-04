"""
Chat Agent Router
=================

CRUD endpoints for Chat Agents (Virtual Servers on MCP Gateway).
Also exposes gateway-level tool/prompt/resource listings.
"""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .chat_agent_repository import ChatAgentRepository
from .event_store.postgres_client import get_postgres_client
from .gateway import get_gateway_client, MCPGatewayClient
from .models import (
    ChatAgentCreate,
    ChatAgentDeployRequest,
    ChatAgentUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/chat-agents", tags=["Chat Agents"])


async def _get_repo() -> ChatAgentRepository:
    pg = await get_postgres_client()
    return ChatAgentRepository(pg)


def _to_summary(row: dict) -> dict:
    tools = row.get("associated_tools") or []
    prompts = row.get("associated_prompts") or []
    resources = row.get("associated_resources") or []
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row.get("description", ""),
        "status": row["status"],
        "tenant_id": row["tenant_id"],
        "workspace_id": row.get("workspace_id"),
        "gateway_server_id": row.get("gateway_server_id"),
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "prompt_count": len(prompts) if isinstance(prompts, list) else 0,
        "resource_count": len(resources) if isinstance(resources, list) else 0,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _to_detail(row: dict, gateway: Optional[MCPGatewayClient] = None) -> dict:
    gw_id = row.get("gateway_server_id")
    mcp_ep = gateway.get_server_mcp_endpoint(gw_id) if (gateway and gw_id) else None
    sse_ep = gateway.get_server_sse_endpoint(gw_id) if (gateway and gw_id) else None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row.get("description", ""),
        "status": row["status"],
        "tenant_id": row["tenant_id"],
        "workspace_id": row.get("workspace_id"),
        "gateway_server_id": gw_id,
        "system_prompt": row.get("system_prompt"),
        "associated_tools": row.get("associated_tools") or [],
        "associated_prompts": row.get("associated_prompts") or [],
        "associated_resources": row.get("associated_resources") or [],
        "kb_resource_id": str(row["kb_resource_id"]) if row.get("kb_resource_id") else None,
        "tags": row.get("tags") or [],
        "config": row.get("config") or {},
        "mcp_endpoint": mcp_ep,
        "sse_endpoint": sse_ep,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


# ===========================================================================
# GATEWAY ITEMS — static paths, MUST be before /{agent_id} routes
# ===========================================================================

@router.get("/gateway/tools", summary="List all tools on Gateway")
async def list_gateway_tools():
    gw = await get_gateway_client()
    tools = await gw.list_tools()
    return {"tools": tools, "count": len(tools)}


@router.get("/gateway/prompts", summary="List all prompts on Gateway")
async def list_gateway_prompts(include_inactive: bool = Query(default=False)):
    gw = await get_gateway_client()
    prompts = await gw.list_prompts(include_inactive=include_inactive)
    return {"prompts": prompts, "count": len(prompts)}


@router.get("/gateway/resources", summary="List all resources on Gateway")
async def list_gateway_resources(include_inactive: bool = Query(default=False)):
    gw = await get_gateway_client()
    resources = await gw.list_resources(include_inactive=include_inactive)
    return {"resources": resources, "count": len(resources)}


@router.get("/gateway/servers", summary="List all virtual servers on Gateway")
async def list_gateway_servers():
    gw = await get_gateway_client()
    servers = await gw.list_virtual_servers()
    return {"servers": servers, "count": len(servers)}


# ===========================================================================
# CRUD
# ===========================================================================

@router.post("", summary="Create a new Chat Agent")
async def create_chat_agent(
    body: ChatAgentCreate,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    row = await repo.create(
        name=body.name,
        description=body.description,
        tenant_id=body.tenant_id,
        workspace_id=body.workspace_id,
        system_prompt=body.system_prompt,
        associated_tools=body.associated_tool_ids,
        associated_prompts=body.associated_prompt_ids,
        associated_resources=body.associated_resource_ids,
        kb_resource_id=body.kb_resource_id,
        tags=body.tags,
        config=body.config,
    )
    return {"id": str(row["id"]), "name": row["name"], "message": "Chat Agent created"}


@router.get("", summary="List Chat Agents")
async def list_chat_agents(
    tenant_id: str = Query(default="default"),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    repo: ChatAgentRepository = Depends(_get_repo),
):
    agents = await repo.list_by_tenant(tenant_id, limit=limit, offset=offset, status=status)
    return {"agents": [_to_summary(a) for a in agents], "count": len(agents)}


@router.get("/{agent_id}", summary="Get Chat Agent detail")
async def get_chat_agent(
    agent_id: str,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    row = await repo.get(agent_id)
    if not row:
        raise HTTPException(status_code=404, detail="Chat Agent not found")

    try:
        gw = await get_gateway_client()
    except Exception:
        gw = None
    return _to_detail(row, gw)


@router.put("/{agent_id}", summary="Update Chat Agent")
async def update_chat_agent(
    agent_id: str,
    body: ChatAgentUpdate,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    existing = await repo.get(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Chat Agent not found")

    updates = body.model_dump(exclude_unset=True)
    rename_map = {
        "associated_tool_ids": "associated_tools",
        "associated_prompt_ids": "associated_prompts",
        "associated_resource_ids": "associated_resources",
    }
    for old_key, new_key in rename_map.items():
        if old_key in updates:
            updates[new_key] = updates.pop(old_key)

    row = await repo.update(agent_id, **updates)
    if not row:
        raise HTTPException(status_code=404, detail="Update failed")

    gw_id = row.get("gateway_server_id")
    if gw_id:
        try:
            gw = await get_gateway_client()
            server_update = {}
            if "name" in updates:
                server_update["name"] = updates["name"]
            if "description" in updates:
                server_update["description"] = updates["description"]
            if "associated_tools" in updates:
                server_update["associated_tools"] = updates["associated_tools"]
            if "associated_prompts" in updates:
                server_update["associated_prompts"] = updates["associated_prompts"]
            if "associated_resources" in updates:
                server_update["associated_resources"] = updates["associated_resources"]
            if server_update:
                await gw.update_virtual_server(gw_id, **server_update)
                logger.info("Synced Chat Agent %s to Gateway server %s", agent_id, gw_id)
        except Exception as e:
            logger.warning("Failed to sync agent %s to Gateway: %s", agent_id, e)

    return {"id": str(row["id"]), "message": "Chat Agent updated"}


@router.delete("/{agent_id}", summary="Delete Chat Agent")
async def delete_chat_agent(
    agent_id: str,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    existing = await repo.get(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Chat Agent not found")

    gw_id = existing.get("gateway_server_id")
    if gw_id:
        try:
            gw = await get_gateway_client()
            await gw.delete_virtual_server(gw_id)
            logger.info("Deleted Gateway virtual server %s", gw_id)
        except Exception as e:
            logger.warning("Failed to delete Gateway server %s: %s", gw_id, e)

    await repo.delete(agent_id)
    return {"message": "Chat Agent deleted", "agent_id": agent_id}


# ===========================================================================
# DEPLOY / UNDEPLOY
# ===========================================================================

@router.post("/{agent_id}/deploy", summary="Deploy Chat Agent to Gateway")
async def deploy_chat_agent(
    agent_id: str,
    body: ChatAgentDeployRequest = ChatAgentDeployRequest(),
    repo: ChatAgentRepository = Depends(_get_repo),
):
    """
    Creates or activates a Virtual Server on the Gateway for this Chat Agent.
    Associates selected tools, prompts, and resources.
    """
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")

    gw = await get_gateway_client()
    gw_id = agent.get("gateway_server_id")

    if gw_id:
        if body.activate:
            await gw.set_server_state(gw_id, active=True)
            await repo.update(agent_id, status="active")
        return {
            "agent_id": agent_id,
            "gateway_server_id": gw_id,
            "mcp_endpoint": gw.get_server_mcp_endpoint(gw_id),
            "message": "Chat Agent activated on Gateway",
        }

    await repo.update(agent_id, status="deploying")
    try:
        result = await gw.create_virtual_server(
            name=agent["name"],
            description=agent.get("description", ""),
            associated_tool_ids=agent.get("associated_tools") or [],
            associated_prompt_ids=agent.get("associated_prompts") or [],
            associated_resource_ids=agent.get("associated_resources") or [],
            tags=agent.get("tags") or [],
        )
        new_gw_id = result.get("id", result.get("server", {}).get("id", ""))
        await repo.set_gateway_server_id(agent_id, new_gw_id)

        return {
            "agent_id": agent_id,
            "gateway_server_id": new_gw_id,
            "mcp_endpoint": gw.get_server_mcp_endpoint(new_gw_id),
            "sse_endpoint": gw.get_server_sse_endpoint(new_gw_id),
            "message": "Chat Agent deployed to Gateway",
        }
    except Exception as e:
        await repo.update(agent_id, status="error")
        logger.error("Deploy failed for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=502, detail=f"Gateway deploy failed: {e}")


@router.post("/{agent_id}/undeploy", summary="Deactivate Chat Agent on Gateway")
async def undeploy_chat_agent(
    agent_id: str,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")

    gw_id = agent.get("gateway_server_id")
    if not gw_id:
        raise HTTPException(status_code=400, detail="Agent is not deployed")

    try:
        gw = await get_gateway_client()
        await gw.set_server_state(gw_id, active=False)
        await repo.update(agent_id, status="inactive")
        return {"agent_id": agent_id, "message": "Chat Agent deactivated"}
    except Exception as e:
        logger.error("Undeploy failed for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=502, detail=f"Gateway undeploy failed: {e}")


# ===========================================================================
# SERVER-SPECIFIC — Items assigned to a deployed agent's virtual server
# ===========================================================================

@router.get("/{agent_id}/server-tools", summary="Tools assigned to this agent's server")
async def get_agent_server_tools(
    agent_id: str,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")
    gw_id = agent.get("gateway_server_id")
    if not gw_id:
        return {"tools": [], "message": "Agent not deployed yet"}
    gw = await get_gateway_client()
    tools = await gw.get_server_tools(gw_id)
    return {"tools": tools, "count": len(tools)}


@router.get("/{agent_id}/server-prompts", summary="Prompts assigned to this agent's server")
async def get_agent_server_prompts(
    agent_id: str,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")
    gw_id = agent.get("gateway_server_id")
    if not gw_id:
        return {"prompts": [], "message": "Agent not deployed yet"}
    gw = await get_gateway_client()
    prompts = await gw.get_server_prompts(gw_id)
    return {"prompts": prompts, "count": len(prompts)}


@router.get("/{agent_id}/server-resources", summary="Resources assigned to this agent's server")
async def get_agent_server_resources(
    agent_id: str,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")
    gw_id = agent.get("gateway_server_id")
    if not gw_id:
        return {"resources": [], "message": "Agent not deployed yet"}
    gw = await get_gateway_client()
    resources = await gw.get_server_resources(gw_id)
    return {"resources": resources, "count": len(resources)}


@router.get("/{agent_id}/mcp-config", summary="MCP client config for this agent's server")
async def get_agent_mcp_config(
    agent_id: str,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    """Returns the HTTP config block for connecting to this agent's Virtual Server."""
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")
    gw_id = agent.get("gateway_server_id")
    if not gw_id:
        raise HTTPException(status_code=400, detail="Agent not deployed")
    gw = await get_gateway_client()
    return gw.get_server_http_config(gw_id)


# ===========================================================================
# LLM CHAT — proxy to Gateway's /llmchat/* endpoints
# ===========================================================================

class ChatConnectRequest(BaseModel):
    model: str = Field(default="gpt-4o")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    streaming: bool = True
    engine: str = Field(
        default="gateway",
        description="Chat engine: 'gateway' (LLM Chat proxy) or 'v2' (ReactAgentV2)",
    )


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    streaming: bool = True
    engine: str = Field(default="gateway")


@router.post("/{agent_id}/chat/connect", summary="Start LLM Chat session")
async def chat_connect(
    agent_id: str,
    body: ChatConnectRequest,
    repo: ChatAgentRepository = Depends(_get_repo),
):
    """
    Connect to this agent's Virtual Server.

    engine='gateway': Gateway LLM Chat proxy (default)
    engine='v2': ReactAgentV2 - local ReAct agent with Gateway-sourced config
    """
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")
    gw_id = agent.get("gateway_server_id")
    if not gw_id:
        raise HTTPException(status_code=400, detail="Agent not deployed to Gateway")

    user_id = f"agent-{agent_id}-{uuid.uuid4().hex[:8]}"
    session_id = f"v2-{agent_id}-{uuid.uuid4().hex[:8]}"

    if body.engine == "v2":
        return {
            "engine": "v2",
            "session_id": session_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "gateway_server_id": gw_id,
            "model": body.model,
            "message": "ReactAgentV2 session ready. Send messages to /{agent_id}/chat",
        }

    gw = await get_gateway_client()
    try:
        result = await gw.llmchat_connect(
            server_id=gw_id,
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            user_id=user_id,
            streaming=body.streaming,
        )
        return {**result, "engine": "gateway", "user_id": user_id, "agent_id": agent_id}
    except Exception as e:
        logger.error("Chat connect failed for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=502, detail=f"Gateway connect failed: {e}")


@router.post("/{agent_id}/chat", summary="Send chat message")
async def chat_message(
    agent_id: str,
    body: ChatMessageRequest,
    user_id: str = Query(..., description="Session user_id from /chat/connect"),
    session_id: Optional[str] = Query(default=None, description="V2 session_id"),
    repo: ChatAgentRepository = Depends(_get_repo),
):
    """
    Send a message to the LLM Chat session.

    engine='gateway': Proxy to Gateway LLM Chat (default)
    engine='v2': ReactAgentV2 local streaming
    """
    agent = await repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Chat Agent not found")

    if body.engine == "v2":
        return await _handle_v2_chat(agent, body, session_id or f"v2-{agent_id}", user_id)

    gw = await get_gateway_client()

    if body.streaming:
        async def sse_generator():
            try:
                resp = await gw.llmchat_chat_stream_raw(user_id, body.message)
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        yield f"{line}\n\n"
                    elif line.startswith("event:"):
                        yield f"{line}\n"
                await resp.aclose()
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        result = await gw.llmchat_chat(user_id, body.message, streaming=False)
        return result
    except Exception as e:
        logger.error("Chat failed for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=502, detail=f"Gateway chat failed: {e}")


async def _handle_v2_chat(agent: dict, body: ChatMessageRequest, session_id: str, user_id: str):
    """ReactAgentV2 ile SSE streaming chat."""
    from .agent.react_agent_v2 import stream_react_agent_v2_response

    gw_id = agent.get("gateway_server_id")
    if not gw_id:
        raise HTTPException(status_code=400, detail="Agent not deployed to Gateway")

    model = agent.get("config", {}).get("model", "gpt-4o") if agent.get("config") else "gpt-4o"

    async def v2_sse_generator():
        try:
            async for chunk in stream_react_agent_v2_response(
                question=body.message,
                server_id=gw_id,
                model=model,
                session_id=session_id,
                question_id=f"q-{uuid.uuid4().hex[:8]}",
                graph=None,
                user_id=user_id,
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            logger.error("ReactAgentV2 streaming error: %s", e, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        v2_sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{agent_id}/chat/disconnect", summary="End LLM Chat session")
async def chat_disconnect(
    agent_id: str,
    user_id: str = Query(..., description="Session user_id"),
):
    gw = await get_gateway_client()
    try:
        return await gw.llmchat_disconnect(user_id)
    except Exception as e:
        logger.error("Chat disconnect failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Disconnect failed: {e}")


@router.get("/{agent_id}/chat/status", summary="Check chat session status")
async def chat_status(
    agent_id: str,
    user_id: str = Query(..., description="Session user_id"),
):
    gw = await get_gateway_client()
    try:
        return await gw.llmchat_status(user_id)
    except Exception as e:
        return {"connected": False, "error": str(e)}
