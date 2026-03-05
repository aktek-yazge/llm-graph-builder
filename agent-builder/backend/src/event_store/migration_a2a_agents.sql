-- Migration: A2A Agent Architecture
-- Adds multi-workspace binding, A2A registry, delegation support to chat_agents

ALTER TABLE chat_agents ADD COLUMN IF NOT EXISTS workspace_ids JSONB DEFAULT '[]';
ALTER TABLE chat_agents ADD COLUMN IF NOT EXISTS a2a_agent_id VARCHAR(256);
ALTER TABLE chat_agents ADD COLUMN IF NOT EXISTS agent_type VARCHAR(32) NOT NULL DEFAULT 'expert';
ALTER TABLE chat_agents ADD COLUMN IF NOT EXISTS delegation_config JSONB DEFAULT '{"auto_threshold": 0.8, "max_depth": 3, "enabled": true}';
ALTER TABLE chat_agents ADD COLUMN IF NOT EXISTS connected_agent_ids JSONB DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_chat_agents_a2a ON chat_agents (a2a_agent_id)
    WHERE a2a_agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_agents_type ON chat_agents (agent_type);

-- Backfill: copy existing workspace_id into workspace_ids array
UPDATE chat_agents
SET workspace_ids = jsonb_build_array(workspace_id)
WHERE workspace_id IS NOT NULL
  AND (workspace_ids IS NULL OR workspace_ids = '[]'::jsonb);
