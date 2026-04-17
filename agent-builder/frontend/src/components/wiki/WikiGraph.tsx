import { useMemo, useEffect } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState,
  type Node, type Edge, type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import type { WikiGraphResponse, WikiCategory } from '../../services/evolvingApi';
import { CATEGORY_COLORS, CATEGORY_LABELS, pageName } from './wikiUtils';

interface Props {
  graph: WikiGraphResponse;
  onSelectPage: (path: string) => void;
}

function sizeForBacklinks(n: number): { w: number; h: number; fontSize: number } {
  const base = 80;
  const extra = Math.min(120, n * 12);
  return {
    w: base + extra,
    h: 40 + Math.min(60, n * 6),
    fontSize: 11 + Math.min(4, n * 0.4),
  };
}

function layoutGraph(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 80 });

  for (const n of nodes) {
    const raw = (n.data as { w: number; h: number }) || { w: 120, h: 50 };
    g.setNode(n.id, { width: raw.w, height: raw.h });
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    const raw = (n.data as { w: number; h: number }) || { w: 120, h: 50 };
    return {
      ...n,
      position: { x: pos.x - raw.w / 2, y: pos.y - raw.h / 2 },
    };
  });
}

export default function WikiGraph({ graph, onSelectPage }: Props) {
  const { initialNodes, initialEdges } = useMemo(() => {
    const nodes: Node[] = graph.nodes.map((gn) => {
      const cat = gn.category in CATEGORY_LABELS ? gn.category : ('general' as WikiCategory);
      const { w, h, fontSize } = sizeForBacklinks(gn.backlinks);
      return {
        id: gn.id,
        position: { x: 0, y: 0 },
        data: {
          w,
          h,
          fontSize,
          label: pageName(gn.path),
          path: gn.path,
          category: cat,
          summary: gn.summary,
          backlinks: gn.backlinks,
          outbound: gn.outbound,
        },
        style: {
          width: w,
          height: h,
          background: '#fff',
          border: `2px solid ${CATEGORY_COLORS[cat as keyof typeof CATEGORY_COLORS]}`,
          borderRadius: 8,
          padding: 6,
          fontSize,
          fontWeight: 500,
          color: '#1f2937',
          textAlign: 'center' as const,
          cursor: 'pointer',
        },
      };
    });

    const edges: Edge[] = graph.edges.map((ge, idx) => ({
      id: `${ge.source}->${ge.target}-${idx}`,
      source: ge.source,
      target: ge.target,
      animated: false,
      style: { stroke: '#cbd5e1', strokeWidth: 1.5 },
    }));

    return { initialNodes: layoutGraph(nodes, edges), initialEdges: edges };
  }, [graph]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  const handleNodeClick: NodeMouseHandler = (_ev, node) => {
    const path = (node.data as { path?: string }).path;
    if (path) onSelectPage(path);
  };

  return (
    <div style={{ height: '100%', width: '100%' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
        <MiniMap
          nodeStrokeWidth={3}
          pannable
          zoomable
          style={{ height: 100, width: 150 }}
          nodeColor={(n) => {
            const cat = (n.data as { category?: WikiCategory }).category;
            return cat ? CATEGORY_COLORS[cat] : '#cbd5e1';
          }}
        />
      </ReactFlow>
    </div>
  );
}
