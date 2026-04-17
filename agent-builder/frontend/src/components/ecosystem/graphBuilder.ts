import dagre from 'dagre';
import type { Node, Edge } from '@xyflow/react';
import type { EcosystemData } from '../../services/evolvingApi';

const NODE_W = 220;
const NODE_H = 100;

export type EcoNodeType =
  | 'agentNode'
  | 'ontologyNode'
  | 'connectionNode'
  | 'subagentNode'
  | 'batchNode'
  | 'resourceNode';

function edge(source: string, target: string, label = ''): Edge {
  return {
    id: `${source}->${target}`,
    source,
    target,
    label,
    animated: true,
    style: { stroke: '#a0aec0', strokeWidth: 2 },
  };
}

export function buildGraph(eco: EcosystemData): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Center: Agent
  nodes.push({
    id: 'agent',
    type: 'agentNode',
    position: { x: 0, y: 0 },
    data: {
      label: eco.agent.name,
      purpose: eco.agent.purpose,
      domain: eco.agent.domain,
      mode: eco.agent.mode,
      model: eco.agent.model,
    },
  });

  // Resources -> Agent
  nodes.push({
    id: 'resources',
    type: 'resourceNode',
    position: { x: 0, y: 0 },
    data: {
      label: 'Resources',
      sampleFiles: eco.resources.sample_files,
      sourceUrls: eco.resources.source_urls,
    },
  });
  edges.push(edge('resources', 'agent', 'input'));

  // Agent -> Ontology
  nodes.push({
    id: 'ontology',
    type: 'ontologyNode',
    position: { x: 0, y: 0 },
    data: {
      label: 'Ontology',
      entityCount: eco.ontology.entity_count,
      relCount: eco.ontology.relationship_count,
      ruleCount: eco.ontology.rule_count,
      constraintCount: eco.ontology.constraint_count,
      pendingDiscoveries: eco.discoveries.pending,
      domain: eco.ontology.domain,
    },
  });
  edges.push(edge('agent', 'ontology', 'defines'));

  // Agent -> MCP
  nodes.push({
    id: 'mcp',
    type: 'connectionNode',
    position: { x: 0, y: 0 },
    data: {
      label: 'MCP Context Forge',
      connectionType: 'mcp',
      connected: eco.mcp.connected,
      detail: eco.mcp.connected ? `${eco.mcp.tool_count} tools` : '',
    },
  });
  edges.push(edge('agent', 'mcp', 'tools'));

  // Agent -> Celery Pipeline
  nodes.push({
    id: 'celery',
    type: 'connectionNode',
    position: { x: 0, y: 0 },
    data: {
      label: 'Celery Pipeline',
      connectionType: 'celery',
      connected: eco.celery.available,
      detail: eco.celery.available ? 'OCR → Extract' : '',
    },
  });
  edges.push(edge('agent', 'celery', 'tasks'));

  // Celery -> Neo4j
  nodes.push({
    id: 'neo4j',
    type: 'connectionNode',
    position: { x: 0, y: 0 },
    data: {
      label: 'Neo4j',
      connectionType: 'neo4j',
      connected: eco.neo4j.configured,
      detail: eco.neo4j.configured ? eco.neo4j.uri : '',
    },
  });
  edges.push(edge('celery', 'neo4j', 'write'));

  // Agent -> Subagents
  eco.subagents.forEach((sa, i) => {
    const id = `subagent-${i}`;
    nodes.push({
      id,
      type: 'subagentNode',
      position: { x: 0, y: 0 },
      data: {
        label: sa.name,
        description: sa.description,
        toolCount: sa.tool_count,
      },
    });
    edges.push(edge('agent', id, 'delegate'));
  });

  // Agent -> Batch
  nodes.push({
    id: 'batch',
    type: 'batchNode',
    position: { x: 0, y: 0 },
    data: {
      label: 'Batch Processing',
      activeCount: eco.batch.active_count,
      latestStatus: eco.batch.latest?.status || '',
      total: eco.batch.latest?.total || 0,
      processed: eco.batch.latest?.processed || 0,
      percentComplete: eco.batch.latest?.percent_complete || 0,
    },
  });
  edges.push(edge('agent', 'batch', 'orchestrate'));

  // Discovery -> Ontology (feedback loop)
  if (eco.discoveries.pending > 0) {
    edges.push({
      id: 'discovery->ontology',
      source: 'celery',
      target: 'ontology',
      label: `${eco.discoveries.pending} pending`,
      animated: true,
      style: { stroke: '#ed8936', strokeWidth: 2, strokeDasharray: '5 5' },
    });
  }

  applyLayout(nodes, edges);
  return { nodes, edges };
}

function applyLayout(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 100 });

  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  nodes.forEach((n) => {
    const pos = g.node(n.id);
    n.position = { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 };
  });
}
