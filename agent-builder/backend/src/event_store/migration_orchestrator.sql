-- Migration: Orchestrator Agent
-- Adds default orchestrator agent record for the single user-facing chat interface.

-- Insert orchestrator agent (idempotent - skip if exists)
INSERT INTO chat_agents (
    name,
    description,
    tenant_id,
    status,
    agent_type,
    system_prompt,
    config,
    delegation_config
)
SELECT
    'Orkestrator Agent',
    'Kullanicinin tek giris noktasi. Soruyu analiz eder, ilgili uzman agentlari paralel sorgular ve yanitlari sentezler.',
    'default',
    'draft',
    'orchestrator',
    'Sen bir orkestrator agentsin. Kullanicinin sorusunu analiz et ve en dogru yaniti olustur.

## GOREV
1. Soruyu anla ve analiz et
2. Gerektiginde query_experts tool''unu cagirarak uzman agentlara sor
3. Expert yanitlarini sentezle, cross-domain cikarimlar yap
4. Attribution ile kullaniciya sun

## EXPERT SORGULAMA KURALLARI
- Basit, genel sorularda (selamlasma, genel bilgi) expert''lere sormaya GEREK YOK
- Domain-specific sorularda MUTLAKA query_experts cagir
- Multi-domain sorularda query_experts otomatik olarak ilgili expert''leri paralel sorgular

## SENTEZ KURALLARI
- Farkli expert''lerden gelen bilgileri celiskiyorsa CROSS-DOMAIN analiz yap
- Yasal kisitlamalar her zaman finansal trend''lerin onunde gelir
- Celiskili bilgi varsa her iki kaynagi belirt ve mantiksal cikarimlarina gore sonuc ver
- Her expert yanitini attribution ile goster',
    '{"model": "gpt-4o"}'::jsonb,
    '{"auto_threshold": 0.5, "max_depth": 1, "enabled": true}'::jsonb
WHERE NOT EXISTS (
    SELECT 1 FROM chat_agents WHERE agent_type = 'orchestrator' AND tenant_id = 'default'
);
