import { useMemo } from 'react';
import { Box, Text } from '@chakra-ui/react';
import type { OntologyData } from '../services/evolvingApi';
import { hashColor } from '../services/evolvingApi';

interface Props {
  ontology: OntologyData['ontology'];
}

interface GraphNode {
  id: string;
  label: string;
  color: string;
}

interface GraphEdge {
  from: string;
  to: string;
  label: string;
}

export default function OntologyGraph({ ontology }: Props) {
  const { nodes, edges } = useMemo(() => {
    const ns: GraphNode[] = ontology.entity_classes.map((e) => ({
      id: e.name,
      label: e.name,
      color: hashColor(e.name),
    }));

    const nodeNames = new Set(ns.map((n) => n.id));

    const es: GraphEdge[] = ontology.relationship_predicates
      .filter((r) => nodeNames.has(r.source) && nodeNames.has(r.target))
      .map((r) => ({
        from: r.source,
        to: r.target,
        label: r.name,
      }));

    return { nodes: ns, edges: es };
  }, [ontology]);

  if (nodes.length === 0) {
    return (
      <Box display="flex" alignItems="center" justifyContent="center" h="32" color="gray.400">
        <Text fontSize="sm">Entity tanimlayinca graf burada gorunecek</Text>
      </Box>
    );
  }

  const width = 320;
  const height = Math.max(180, nodes.length * 50);
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(cx, cy) - 40;

  const positioned = nodes.map((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    return { ...n, x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
  });

  const posMap = new Map(positioned.map((p) => [p.id, p]));

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: 'block' }}>
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <path d="M0,0 L8,3 L0,6 Z" fill="#9CA3AF" />
        </marker>
      </defs>

      {edges.map((e, i) => {
        const from = posMap.get(e.from);
        const to = posMap.get(e.to);
        if (!from || !to) return null;
        const mx = (from.x + to.x) / 2;
        const my = (from.y + to.y) / 2 - 10;
        return (
          <g key={`edge-${i}`}>
            <line
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke="#D1D5DB"
              strokeWidth="1.5"
              markerEnd="url(#arrow)"
            />
            <text x={mx} y={my} textAnchor="middle" fill="#9CA3AF" fontSize="8">
              {e.label}
            </text>
          </g>
        );
      })}

      {positioned.map((n) => (
        <g key={n.id}>
          <circle cx={n.x} cy={n.y} r="20" fill={n.color} opacity="0.85" />
          <text
            x={n.x}
            y={n.y + 1}
            textAnchor="middle"
            dominantBaseline="central"
            fill="white"
            fontSize="9"
            fontWeight="600"
          >
            {n.label.length > 8 ? n.label.slice(0, 7) + '..' : n.label}
          </text>
        </g>
      ))}
    </svg>
  );
}
