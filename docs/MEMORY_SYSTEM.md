# 记忆系统设计

## 概述

记忆系统使 auto-dev-crew 能够跨会话积累知识，从历史任务中学习，并在新任务中应用经验。这是实现真正智能代理的关键组件。

## 记忆层次结构

```
┌─────────────────────────────────────────────────────────────┐
│                    记忆系统总体架构                          │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   短期记忆    │  │   长期记忆    │  │   专家记忆    │      │
│  │  (工作记忆)  │  │  (持久存储)  │  │  (领域知识)  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│         │                  │                  │              │
│         ▼                  ▼                  ▼              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              记忆检索与整合引擎                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              知识图谱 (关联记忆)                      │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. 短期记忆 (ShortTermMemory)

存储当前会话的临时信息，容量有限，会话结束后清除。

```python
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
from datetime import datetime
from collections import deque

class MemoryItem(BaseModel):
    """记忆项"""
    id: str
    content: str
    memory_type: str  # observation, thought, action, result
    timestamp: datetime
    importance: float  # 0-1
    context: Dict[str, Any] = {}

class ShortTermMemory:
    """短期记忆"""
    
    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.items: deque[MemoryItem] = deque(maxlen=capacity)
        self.index: Dict[str, MemoryItem] = {}
    
    def store(self, item: MemoryItem):
        """存储记忆项"""
        # 如果已存在，更新
        if item.id in self.index:
            self.items.remove(self.index[item.id])
        
        self.items.append(item)
        self.index[item.id] = item
    
    def retrieve(
        self, 
        query: str, 
        top_k: int = 5
    ) -> List[MemoryItem]:
        """检索相关记忆"""
        # 简单的关键词匹配（实际应用中使用向量搜索）
        scored_items = []
        for item in self.items:
            score = self._relevance_score(query, item)
            scored_items.append((score, item))
        
        # 按相关性排序
        scored_items.sort(key=lambda x: x[0], reverse=True)
        
        return [item for _, item in scored_items[:top_k]]
    
    def _relevance_score(self, query: str, item: MemoryItem) -> float:
        """计算相关性分数"""
        query_words = set(query.lower().split())
        content_words = set(item.content.lower().split())
        
        overlap = len(query_words & content_words)
        return overlap / max(len(query_words), 1)
    
    def get_recent(self, n: int = 10) -> List[MemoryItem]:
        """获取最近的 n 条记忆"""
        return list(self.items)[-n:]
    
    def clear(self):
        """清除所有记忆"""
        self.items.clear()
        self.index.clear()
```

### 2. 长期记忆 (LongTermMemory)

持久化存储，使用向量数据库实现语义搜索。

```python
import numpy as np
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import hashlib

class LongTermMemoryItem(BaseModel):
    """长期记忆项"""
    id: str
    content: str
    embedding: List[float]
    metadata: Dict[str, Any]
    created_at: datetime
    accessed_at: datetime
    access_count: int = 0
    importance: float = 0.5

class VectorStore:
    """向量存储（简化实现）"""
    
    def __init__(self):
        self.vectors: Dict[str, np.ndarray] = {}
        self.items: Dict[str, LongTermMemoryItem] = {}
    
    def add(self, item: LongTermMemoryItem):
        """添加向量"""
        self.vectors[item.id] = np.array(item.embedding)
        self.items[item.id] = item
    
    def search(
        self, 
        query_vector: np.ndarray, 
        top_k: int = 5
    ) -> List[tuple]:
        """搜索相似向量"""
        if not self.vectors:
            return []
        
        # 计算余弦相似度
        scores = []
        for id, vector in self.vectors.items():
            similarity = np.dot(query_vector, vector) / (
                np.linalg.norm(query_vector) * np.linalg.norm(vector)
            )
            scores.append((similarity, id))
        
        # 按相似度排序
        scores.sort(reverse=True)
        
        return [(self.items[id], score) for score, id in scores[:top_k]]
    
    def delete(self, item_id: str):
        """删除向量"""
        if item_id in self.vectors:
            del self.vectors[item_id]
            del self.items[item_id]

class EmbeddingGenerator:
    """嵌入向量生成器"""
    
    def __init__(self, model: str = "text-embedding-3-small"):
        self.model = model
    
    async def generate(self, text: str) -> List[float]:
        """生成嵌入向量"""
        # 调用 LLM API 生成嵌入
        # 这里使用简化的实现
        import hashlib
        hash_obj = hashlib.md5(text.encode())
        # 生成伪向量（实际应用中使用真实的嵌入模型）
        return [float(b) / 255.0 for b in hash_obj.digest()][:128]

class LongTermMemory:
    """长期记忆"""
    
    def __init__(self, storage_path: str = "~/.auto-dev-crew/memory"):
        self.storage_path = storage_path
        self.vector_store = VectorStore()
        self.embedding_generator = EmbeddingGenerator()
        self._load_from_disk()
    
    def _load_from_disk(self):
        """从磁盘加载记忆"""
        # 加载持久化的记忆
        pass
    
    def _save_to_disk(self):
        """保存记忆到磁盘"""
        # 保存到文件或数据库
        pass
    
    async def store(self, content: str, metadata: Dict[str, Any] = None):
        """存储记忆"""
        # 生成嵌入向量
        embedding = await self.embedding_generator.generate(content)
        
        # 创建记忆项
        item = LongTermMemoryItem(
            id=self._generate_id(content),
            content=content,
            embedding=embedding,
            metadata=metadata or {},
            created_at=datetime.now(),
            accessed_at=datetime.now()
        )
        
        # 存储到向量数据库
        self.vector_store.add(item)
        
        # 持久化
        self._save_to_disk()
    
    async def retrieve(
        self, 
        query: str, 
        top_k: int = 5,
        min_score: float = 0.5
    ) -> List[LongTermMemoryItem]:
        """检索相关记忆"""
        # 生成查询向量
        query_embedding = await self.embedding_generator.generate(query)
        query_vector = np.array(query_embedding)
        
        # 搜索相似记忆
        results = self.vector_store.search(query_vector, top_k)
        
        # 过滤低分结果
        filtered = [
            (item, score) for item, score in results 
            if score >= min_score
        ]
        
        # 更新访问时间
        for item, _ in filtered:
            item.accessed_at = datetime.now()
            item.access_count += 1
        
        return [item for item, _ in filtered]
    
    def _generate_id(self, content: str) -> str:
        """生成唯一 ID"""
        return hashlib.md5(content.encode()).hexdigest()
```

### 3. 专家记忆 (ExpertMemory)

存储领域专业知识，由专家 Agent 维护。

```python
class ExpertKnowledge(BaseModel):
    """专家知识"""
    domain: str  # 领域名称
    category: str  # 知识类别
    title: str
    content: str
    examples: List[str] = []
    references: List[str] = []
    confidence: float = 0.8  # 置信度
    created_by: str  # 创建者 Agent ID
    validated: bool = False  # 是否经过验证

class ExpertMemory:
    """专家记忆"""
    
    def __init__(self):
        self.knowledge_base: Dict[str, List[ExpertKnowledge]] = {}
        self._load_expert_knowledge()
    
    def _load_expert_knowledge(self):
        """加载专家知识"""
        # 从文件或数据库加载预定义的专家知识
        predefined = [
            ExpertKnowledge(
                domain="web_development",
                category="best_practices",
                title="RESTful API 设计原则",
                content="使用名词复数形式命名资源，使用 HTTP 方法表示操作...",
                examples=["GET /users", "POST /users", "PUT /users/1"],
                confidence=0.95,
                created_by="system",
                validated=True
            ),
            # 更多预定义知识...
        ]
        
        for knowledge in predefined:
            self.add(knowledge)
    
    def add(self, knowledge: ExpertKnowledge):
        """添加专家知识"""
        domain = knowledge.domain
        if domain not in self.knowledge_base:
            self.knowledge_base[domain] = []
        self.knowledge_base[domain].append(knowledge)
    
    def query(
        self, 
        domain: str, 
        category: str = None,
        query: str = None
    ) -> List[ExpertKnowledge]:
        """查询专家知识"""
        results = []
        
        # 获取领域知识
        domain_knowledge = self.knowledge_base.get(domain, [])
        
        for knowledge in domain_knowledge:
            # 按类别过滤
            if category and knowledge.category != category:
                continue
            
            # 按查询过滤
            if query and not self._matches_query(knowledge, query):
                continue
            
            results.append(knowledge)
        
        return results
    
    def _matches_query(
        self, 
        knowledge: ExpertKnowledge, 
        query: str
    ) -> bool:
        """检查知识是否匹配查询"""
        query_lower = query.lower()
        return (
            query_lower in knowledge.title.lower() or
            query_lower in knowledge.content.lower()
        )
```

### 4. 知识图谱 (KnowledgeGraph)

存储实体之间的关联关系。

```python
from typing import Dict, List, Set, Tuple

class Entity(BaseModel):
    """实体"""
    id: str
    name: str
    entity_type: str  # file, function, concept, pattern, etc.
    properties: Dict[str, Any] = {}

class Relation(BaseModel):
    """关系"""
    source_id: str
    target_id: str
    relation_type: str  # uses, depends_on, implements, etc.
    properties: Dict[str, Any] = {}

class KnowledgeGraph:
    """知识图谱"""
    
    def __init__(self):
        self.entities: Dict[str, Entity] = {}
        self.relations: List[Relation] = []
        self.adjacency: Dict[str, List[Tuple[str, str]]] = {}  # entity_id -> [(relation_type, target_id)]
    
    def add_entity(self, entity: Entity):
        """添加实体"""
        self.entities[entity.id] = entity
        if entity.id not in self.adjacency:
            self.adjacency[entity.id] = []
    
    def add_relation(self, relation: Relation):
        """添加关系"""
        self.relations.append(relation)
        
        # 更新邻接表
        if relation.source_id not in self.adjacency:
            self.adjacency[relation.source_id] = []
        self.adjacency[relation.source_id].append(
            (relation.relation_type, relation.target_id)
        )
    
    def get_related(
        self, 
        entity_id: str, 
        relation_type: str = None
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
    ):
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
```

### 5. MemorySystem (记忆系统)

整合所有记忆组件。

```python
class MemorySystem:
    """记忆系统"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        
        # 初始化各层记忆
        self.short_term = ShortTermMemory(
            capacity=self.config.get("short_term_capacity", 100)
        )
        self.long_term = LongTermMemory(
            storage_path=self.config.get("storage_path", "~/.auto-dev-crew/memory")
        )
        self.expert = ExpertMemory()
        self.knowledge_graph = KnowledgeGraph()
        
        # 记忆检索器
        self.retriever = MemoryRetriever(
            self.short_term, 
            self.long_term, 
            self.expert,
            self.knowledge_graph
        )
    
    async def store(
        self, 
        content: str, 
        memory_type: str = "observation",
        metadata: Dict[str, Any] = None
    ):
        """存储记忆"""
        # 创建记忆项
        item = MemoryItem(
            id=str(uuid.uuid4()),
            content=content,
            memory_type=memory_type,
            timestamp=datetime.now(),
            importance=self._calculate_importance(content),
            context=metadata or {}
        )
        
        # 存储到短期记忆
        self.short_term.store(item)
        
        # 如果重要，也存储到长期记忆
        if item.importance > 0.7:
            await self.long_term.store(content, metadata)
    
    async def retrieve(
        self, 
        query: str, 
        context: Dict[str, Any] = None,
        top_k: int = 10
    ) -> List[MemoryItem]:
        """检索相关记忆"""
        return await self.retriever.retrieve(query, context, top_k)
    
    async def learn_from_task(
        self, 
        task: str, 
        result: Dict[str, Any]
    ):
        """从任务中学习"""
        # 提取关键信息
        learnings = self._extract_learnings(task, result)
        
        # 存储到长期记忆
        for learning in learnings:
            await self.long_term.store(
                learning["content"],
                learning["metadata"]
            )
        
        # 更新知识图谱
        self._update_knowledge_graph(task, result)
    
    def _calculate_importance(self, content: str) -> float:
        """计算记忆重要性"""
        # 基于内容特征计算重要性
        importance = 0.5
        
        # 包含错误信息的重要性高
        if "error" in content.lower() or "失败" in content:
            importance += 0.2
        
        # 包含解决方案的重要性高
        if "解决" in content or "fix" in content.lower():
            importance += 0.2
        
        # 包含最佳实践的重要性高
        if "最佳实践" in content or "best practice" in content.lower():
            importance += 0.1
        
        return min(importance, 1.0)
    
    def _extract_learnings(
        self, 
        task: str, 
        result: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """从任务结果中提取学习内容"""
        learnings = []
        
        # 如果任务成功
        if result.get("success"):
            learnings.append({
                "content": f"成功完成任务: {task}",
                "metadata": {
                    "type": "success_pattern",
                    "task": task,
                    "approach": result.get("approach")
                }
            })
        
        # 如果遇到问题并解决
        if result.get("issues_resolved"):
            for issue in result["issues_resolved"]:
                learnings.append({
                    "content": f"解决问题: {issue['problem']} -> {issue['solution']}",
                    "metadata": {
                        "type": "problem_solution",
                        "problem": issue["problem"],
                        "solution": issue["solution"]
                    }
                })
        
        return learnings
    
    def _update_knowledge_graph(
        self, 
        task: str, 
        result: Dict[str, Any]
    ):
        """更新知识图谱"""
        # 添加任务实体
        task_entity = Entity(
            id=f"task:{hashlib.md5(task.encode()).hexdigest()}",
            name=task[:50],
            entity_type="task",
            properties={"result": result.get("success")}
        )
        self.knowledge_graph.add_entity(task_entity)
        
        # 添加相关文件实体和关系
        for file_path in result.get("files_modified", []):
            file_entity = Entity(
                id=f"file:{file_path}",
                name=file_path,
                entity_type="file"
            )
            self.knowledge_graph.add_entity(file_entity)
            
            relation = Relation(
                source_id=task_entity.id,
                target_id=file_entity.id,
                relation_type="modifies"
            )
            self.knowledge_graph.add_relation(relation)

class MemoryRetriever:
    """记忆检索器"""
    
    def __init__(
        self, 
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
        expert: ExpertMemory,
        knowledge_graph: KnowledgeGraph
    ):
        self.short_term = short_term
        self.long_term = long_term
        self.expert = expert
        self.knowledge_graph = knowledge_graph
    
    async def retrieve(
        self, 
        query: str, 
        context: Dict[str, Any] = None,
        top_k: int = 10
    ) -> List[MemoryItem]:
        """检索相关记忆"""
        results = []
        
        # 1. 从短期记忆检索
        short_term_results = self.short_term.retrieve(query, top_k)
        results.extend(short_term_results)
        
        # 2. 从长期记忆检索
        long_term_results = await self.long_term.retrieve(query, top_k)
        results.extend(long_term_results)
        
        # 3. 从专家记忆检索
        domain = context.get("domain") if context else None
        if domain:
            expert_results = self.expert.query(domain, query=query)
            results.extend([
                MemoryItem(
                    id=f"expert:{k.title}",
                    content=k.content,
                    memory_type="expert",
                    timestamp=datetime.now(),
                    importance=k.confidence
                )
                for k in expert_results
            ])
        
        # 4. 从知识图谱检索相关记忆
        if context and "entity_id" in context:
            related = self.knowledge_graph.get_related(context["entity_id"])
            for rel_type, entity in related:
                results.append(MemoryItem(
                    id=f"graph:{entity.id}",
                    content=f"{rel_type}: {entity.name}",
                    memory_type="graph",
                    timestamp=datetime.now(),
                    importance=0.6
                ))
        
        # 去重并排序
        unique_results = self._deduplicate(results)
        sorted_results = sorted(
            unique_results, 
            key=lambda x: x.importance, 
            reverse=True
        )
        
        return sorted_results[:top_k]
    
    def _deduplicate(self, items: List[MemoryItem]) -> List[MemoryItem]:
        """去重"""
        seen = set()
        unique = []
        for item in items:
            if item.id not in seen:
                seen.add(item.id)
                unique.append(item)
        return unique
```

## 使用示例

```python
# 初始化记忆系统
memory = MemorySystem({
    "short_term_capacity": 100,
    "storage_path": "~/.auto-dev-crew/memory"
})

# 存储记忆
await memory.store(
    "发现使用 FastAPI 的 Depends 可以简化依赖注入",
    memory_type="observation",
    metadata={"topic": "fastapi", "category": "best_practice"}
)

# 检索记忆
results = await memory.retrieve(
    "FastAPI 依赖注入",
    context={"domain": "web_development"}
)

for item in results:
    print(f"- {item.content} (重要性: {item.importance})")

# 从任务中学习
await memory.learn_from_task(
    task="实现用户认证功能",
    result={
        "success": True,
        "approach": "JWT + OAuth2",
        "files_modified": ["auth.py", "users.py"],
        "issues_resolved": [
            {
                "problem": "Token 过期处理",
                "solution": "使用 refresh token 机制"
            }
        ]
    }
)
```

## 配置

```yaml
# .auto-dev-crew/memory.yaml
memory:
  short_term:
    capacity: 100
  
  long_term:
    storage_path: "~/.auto-dev-crew/memory"
    embedding_model: "text-embedding-3-small"
    vector_store: "qdrant"  # 或 "milvus", "pinecone"
  
  expert:
    knowledge_base_path: "~/.auto-dev-crew/expert_knowledge"
    auto_validate: false
  
  knowledge_graph:
    storage_path: "~/.auto-dev-crew/knowledge_graph"
    max_entities: 10000
  
  retrieval:
    default_top_k: 10
    min_relevance_score: 0.5
    enable_graph_search: true
```
