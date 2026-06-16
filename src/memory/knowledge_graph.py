"""知识图谱"""

from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


class Entity(BaseModel):
    """实体"""
    id: str
    name: str
    entity_type: str  # file, function, concept, pattern, task, etc.
    properties: Dict[str, Any] = Field(default_factory=dict)


class Relation(BaseModel):
    """关系"""
    source_id: str
    target_id: str
    relation_type: str  # uses, depends_on, implements, modifies, etc.
    properties: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraph:
    """知识图谱"""
    
    def __init__(self):
        self.entities: Dict[str, Entity] = {}
        self.relations: List[Relation] = []
        self.adjacency: Dict[str, List[Tuple[str, str]]] = {}  # entity_id -> [(relation_type, target_id)]
    
    def add_entity(self, entity: Entity) -> None:
        """添加实体"""
        self.entities[entity.id] = entity
        if entity.id not in self.adjacency:
            self.adjacency[entity.id] = []
    
    def add_relation(self, relation: Relation) -> None:
        """添加关系"""
        self.relations.append(relation)
        
        # 更新邻接表
        if relation.source_id not in self.adjacency:
            self.adjacency[relation.source_id] = []
        self.adjacency[relation.source_id].append(
            (relation.relation_type, relation.target_id)
        )
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """获取实体"""
        return self.entities.get(entity_id)
    
    def get_related(
        self, 
        entity_id: str, 
        relation_type: Optional[str] = None
    ) -> List[Tuple[str, Entity]]:
        """获取相关实体"""
        if entity_id not in self.adjacency:
            return []
        
        results = []
        for rel_type, target_id in self.adjacency[entity_id]:
            if relation_type and rel_type != relation_type:
                continue
            if target_id in self.entities:
                results.append((rel_type, self.entities[target_id]))
        
        return results
    
    def find_path(
        self, 
        start_id: str, 
        end_id: str, 
        max_depth: int = 3
    ) -> List[List[Tuple[str, str]]]:
        """查找两个实体之间的路径"""
        paths = []
        self._dfs(start_id, end_id, max_depth, [], paths, set())
        return paths
    
    def _dfs(
        self, 
        current_id: str, 
        target_id: str, 
        depth: int,
        current_path: List[Tuple[str, str]],
        all_paths: List[List[Tuple[str, str]]],
        visited: Set[str]
    ) -> None:
        """深度优先搜索"""
        if depth == 0:
            return
        
        if current_id == target_id:
            all_paths.append(current_path.copy())
            return
        
        visited.add(current_id)
        
        for rel_type, next_id in self.adjacency.get(current_id, []):
            if next_id not in visited:
                current_path.append((rel_type, next_id))
                self._dfs(
                    next_id, target_id, depth - 1, 
                    current_path, all_paths, visited
                )
                current_path.pop()
        
        visited.remove(current_id)
    
    def get_entities_by_type(self, entity_type: str) -> List[Entity]:
        """按类型获取实体"""
        return [
            entity for entity in self.entities.values()
            if entity.entity_type == entity_type
        ]
    
    def remove_entity(self, entity_id: str) -> None:
        """删除实体"""
        if entity_id in self.entities:
            del self.entities[entity_id]
        
        if entity_id in self.adjacency:
            del self.adjacency[entity_id]
        
        # 移除相关的关系
        self.relations = [
            r for r in self.relations
            if r.source_id != entity_id and r.target_id != entity_id
        ]
        
        # 更新邻接表
        for key in self.adjacency:
            self.adjacency[key] = [
                (rel_type, target_id)
                for rel_type, target_id in self.adjacency[key]
                if target_id != entity_id
            ]
    
    def clear(self) -> None:
        """清除所有数据"""
        self.entities.clear()
        self.relations.clear()
        self.adjacency.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "entities": len(self.entities),
            "relations": len(self.relations),
            "entity_types": list(set(
                e.entity_type for e in self.entities.values()
            ))
        }
