"""
Ontology Model
==============

KG Prompt Generator yapisina dayanan yapilandirilmis ontoloji modeli.
Agent'in domain bilgisini (entity class'lari, relationship predicate'leri,
inference rule'lari, constraint'leri) tanimlar.

Ref: https://gist.github.com/smrati/3b6ed5d493d9d82418da7e76df8e3125
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Property:
    """Entity class'inin bir property'si."""
    name: str
    type: str = "string"          # string | number | date | boolean
    constraint: str = "optional"  # required | optional | unique
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EntityClass:
    """Ontolojideki bir entity sinifi (KG Prompt Generator 'Class Hierarchy')."""
    name: str
    description: str = ""
    parent: str = ""
    properties: list[Property] = field(default_factory=list)

    @property
    def required_properties(self) -> list[Property]:
        return [p for p in self.properties if p.constraint == "required"]

    @property
    def optional_properties(self) -> list[Property]:
        return [p for p in self.properties if p.constraint != "required"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parent": self.parent,
            "properties": [p.to_dict() for p in self.properties],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityClass:
        props = [Property(**p) for p in data.get("properties", [])]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            parent=data.get("parent", ""),
            properties=props,
        )


@dataclass
class RelationshipPredicate:
    """Ontolojideki bir iliski tipi (KG Prompt Generator 'Relationship Predicates')."""
    name: str
    source: str
    target: str
    edge_properties: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationshipPredicate:
        return cls(**data)


@dataclass
class InferenceRule:
    """Cikarim kurali (KG Prompt Generator 'Inference Rules')."""
    condition: str                # "X HAS_POLICY Y"
    inference: str                # "X IS_CUSTOMER_OF Y.insurer"
    rule_type: str = "implied"    # implied | transitive | inverse

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferenceRule:
        return cls(**data)


@dataclass
class AgentOntology:
    """
    Agent'in tum domain bilgisini tutan ontoloji.

    KG Prompt Generator'in form yapisiyla birebir eslenir:
    1. Domain & Objectives  -> domain, goal
    2. Class Hierarchy      -> entity_classes
    3. Relationship Schema  -> relationship_predicates
    4. Inference Rules      -> inference_rules
    5. Global Constraints   -> constraints
    """
    domain: str = ""
    goal: str = ""
    entity_classes: list[EntityClass] = field(default_factory=list)
    relationship_predicates: list[RelationshipPredicate] = field(default_factory=list)
    inference_rules: list[InferenceRule] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    # --- Entity class helpers ---

    def get_entity(self, name: str) -> EntityClass | None:
        for e in self.entity_classes:
            if e.name == name:
                return e
        return None

    def upsert_entity(self, entity: EntityClass) -> None:
        for i, e in enumerate(self.entity_classes):
            if e.name == entity.name:
                self.entity_classes[i] = entity
                return
        self.entity_classes.append(entity)

    def remove_entity(self, name: str) -> bool:
        before = len(self.entity_classes)
        self.entity_classes = [e for e in self.entity_classes if e.name != name]
        return len(self.entity_classes) < before

    # --- Relationship helpers ---

    def get_relationship(self, name: str) -> RelationshipPredicate | None:
        for r in self.relationship_predicates:
            if r.name == name:
                return r
        return None

    def upsert_relationship(self, rel: RelationshipPredicate) -> None:
        for i, r in enumerate(self.relationship_predicates):
            if r.name == rel.name:
                self.relationship_predicates[i] = rel
                return
        self.relationship_predicates.append(rel)

    def remove_relationship(self, name: str) -> bool:
        before = len(self.relationship_predicates)
        self.relationship_predicates = [r for r in self.relationship_predicates if r.name != name]
        return len(self.relationship_predicates) < before

    # --- Serialization ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "goal": self.goal,
            "entity_classes": [e.to_dict() for e in self.entity_classes],
            "relationship_predicates": [r.to_dict() for r in self.relationship_predicates],
            "inference_rules": [r.to_dict() for r in self.inference_rules],
            "constraints": self.constraints,
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentOntology:
        return cls(
            domain=data.get("domain", ""),
            goal=data.get("goal", ""),
            entity_classes=[EntityClass.from_dict(e) for e in data.get("entity_classes", [])],
            relationship_predicates=[RelationshipPredicate.from_dict(r) for r in data.get("relationship_predicates", [])],
            inference_rules=[InferenceRule.from_dict(r) for r in data.get("inference_rules", [])],
            constraints=data.get("constraints", []),
        )

    @classmethod
    def from_json(cls, text: str) -> AgentOntology:
        return cls.from_dict(json.loads(text))

    @property
    def is_empty(self) -> bool:
        return not self.entity_classes and not self.relationship_predicates
