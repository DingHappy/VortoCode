"""向量数据库集成的记忆系统"""

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from .base import Memory, MemoryItem

logger = logging.getLogger(__name__)


class VectorMemoryConfig(BaseModel):
    """向量记忆配置"""
    backend: str = "qdrant"  # qdrant, milvus, chromadb, faiss, local
    host: str = "localhost"
    port: int = 6333
    collection_name: str = "auto_dev_memory"
    embedding_model: str = "all-MiniLM-L6-v2"  # 或 "text-embedding-ada-002"
    embedding_dimension: int = 384
    use_openai_embeddings: bool = False
    openai_api_key: Optional[str] = None
    openai_api_base: Optional[str] = None
    storage_path: str = ".auto-dev-crew/vector_memory"


class EmbeddingGenerator(ABC):
    """嵌入向量生成器基类"""
    
    @abstractmethod
    async def generate_embedding(self, text: str) -> List[float]:
        """生成嵌入向量"""
        pass
    
    @abstractmethod
    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量生成嵌入向量"""
        pass


class LocalEmbeddingGenerator(EmbeddingGenerator):
    """本地嵌入向量生成器"""
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.model = None
    
    async def _load_model(self):
        """加载模型"""
        if self.model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(self.model_name)
                logger.info(f"Loaded embedding model: {self.model_name}")
            except ImportError:
                logger.error("sentence-transformers not installed. Install with: pip install sentence-transformers")
                raise
    
    async def generate_embedding(self, text: str) -> List[float]:
        """生成嵌入向量"""
        await self._load_model()
        
        if self.model is None:
            raise RuntimeError("Failed to load embedding model")
        
        embedding = self.model.encode(text)
        return embedding.tolist()
    
    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量生成嵌入向量"""
        await self._load_model()
        
        if self.model is None:
            raise RuntimeError("Failed to load embedding model")
        
        embeddings = self.model.encode(texts)
        return embeddings.tolist()


class OpenAIEmbeddingGenerator(EmbeddingGenerator):
    """OpenAI嵌入向量生成器"""
    
    def __init__(
        self,
        model: str = "text-embedding-ada-002",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.client = None
    
    async def _get_client(self):
        """获取OpenAI客户端"""
        if self.client is None:
            try:
                import openai
                self.client = openai.AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.api_base
                )
            except ImportError:
                logger.error("openai not installed. Install with: pip install openai")
                raise
        return self.client
    
    async def generate_embedding(self, text: str) -> List[float]:
        """生成嵌入向量"""
        client = await self._get_client()
        
        response = await client.embeddings.create(
            model=self.model,
            input=text
        )
        
        return response.data[0].embedding
    
    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量生成嵌入向量"""
        client = await self._get_client()
        
        response = await client.embeddings.create(
            model=self.model,
            input=texts
        )
        
        return [item.embedding for item in response.data]


class VectorStore(ABC):
    """向量存储基类"""
    
    @abstractmethod
    async def add_vectors(
        self,
        vectors: List[List[float]],
        metadata: List[Dict[str, Any]],
        ids: Optional[List[str]] = None
    ) -> List[str]:
        """添加向量"""
        pass
    
    @abstractmethod
    async def search_vectors(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """搜索向量"""
        pass
    
    @abstractmethod
    async def delete_vectors(self, ids: List[str]) -> bool:
        """删除向量"""
        pass
    
    @abstractmethod
    async def update_vectors(
        self,
        ids: List[str],
        vectors: Optional[List[List[float]]] = None,
        metadata: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """更新向量"""
        pass


class QdrantVectorStore(VectorStore):
    """Qdrant向量存储"""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection_name: str = "auto_dev_memory",
        vector_size: int = 384
    ):
        self.host = host
        self.port = port
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.client = None
    
    async def _get_client(self):
        """获取Qdrant客户端"""
        if self.client is None:
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.models import Distance, VectorParams
                
                self.client = QdrantClient(host=self.host, port=self.port)
                
                # 检查集合是否存在，不存在则创建
                collections = self.client.get_collections().collections
                collection_names = [c.name for c in collections]
                
                if self.collection_name not in collection_names:
                    self.client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=VectorParams(
                            size=self.vector_size,
                            distance=Distance.COSINE
                        )
                    )
                    logger.info(f"Created Qdrant collection: {self.collection_name}")
                
            except ImportError:
                logger.error("qdrant-client not installed. Install with: pip install qdrant-client")
                raise
        return self.client
    
    async def add_vectors(
        self,
        vectors: List[List[float]],
        metadata: List[Dict[str, Any]],
        ids: Optional[List[str]] = None
    ) -> List[str]:
        """添加向量"""
        from qdrant_client.models import PointStruct
        
        client = await self._get_client()
        
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in vectors]
        
        points = []
        for i, (vector, meta) in enumerate(zip(vectors, metadata)):
            point = PointStruct(
                id=ids[i],
                vector=vector,
                payload=meta
            )
            points.append(point)
        
        client.upsert(
            collection_name=self.collection_name,
            points=points
        )
        
        return ids
    
    async def search_vectors(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """搜索向量"""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        client = await self._get_client()
        
        # 构建过滤条件
        search_filter = None
        if filter_dict:
            conditions = []
            for key, value in filter_dict.items():
                condition = FieldCondition(
                    key=key,
                    match=MatchValue(value=value)
                )
                conditions.append(condition)
            
            if conditions:
                search_filter = Filter(must=conditions)
        
        # 执行搜索
        results = client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k,
            query_filter=search_filter
        )
        
        # 格式化结果
        formatted_results = []
        for result in results:
            formatted_results.append({
                "id": result.id,
                "score": result.score,
                "metadata": result.payload
            })
        
        return formatted_results
    
    async def delete_vectors(self, ids: List[str]) -> bool:
        """删除向量"""
        client = await self._get_client()
        
        client.delete(
            collection_name=self.collection_name,
            points_selector=ids
        )
        
        return True
    
    async def update_vectors(
        self,
        ids: List[str],
        vectors: Optional[List[List[float]]] = None,
        metadata: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """更新向量"""
        from qdrant_client.models import PointStruct
        
        client = await self._get_client()
        
        # 获取现有点
        existing_points = client.retrieve(
            collection_name=self.collection_name,
            ids=ids
        )
        
        # 更新点
        updated_points = []
        for i, point in enumerate(existing_points):
            new_vector = vectors[i] if vectors and i < len(vectors) else point.vector
            new_payload = metadata[i] if metadata and i < len(metadata) else point.payload
            
            updated_point = PointStruct(
                id=point.id,
                vector=new_vector,
                payload=new_payload
            )
            updated_points.append(updated_point)
        
        client.upsert(
            collection_name=self.collection_name,
            points=updated_points
        )
        
        return True


class LocalVectorStore(VectorStore):
    """本地向量存储（基于FAISS或简单实现）"""
    
    def __init__(self, storage_path: str = ".auto-dev-crew/vector_memory"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        self.vectors_file = self.storage_path / "vectors.json"
        self.vectors: Dict[str, Dict[str, Any]] = {}
        
        self._load_from_disk()
    
    def _load_from_disk(self) -> None:
        """从磁盘加载向量"""
        import json
        
        if self.vectors_file.exists():
            try:
                with open(self.vectors_file, 'r', encoding='utf-8') as f:
                    self.vectors = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load vectors: {e}")
    
    def _save_to_disk(self) -> None:
        """保存向量到磁盘"""
        import json
        
        try:
            with open(self.vectors_file, 'w', encoding='utf-8') as f:
                json.dump(self.vectors, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save vectors: {e}")
    
    async def add_vectors(
        self,
        vectors: List[List[float]],
        metadata: List[Dict[str, Any]],
        ids: Optional[List[str]] = None
    ) -> List[str]:
        """添加向量"""
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in vectors]
        
        for i, (vector, meta) in enumerate(zip(vectors, metadata)):
            self.vectors[ids[i]] = {
                "vector": vector,
                "metadata": meta
            }
        
        self._save_to_disk()
        return ids
    
    async def search_vectors(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """搜索向量"""
        import numpy as np
        
        results = []
        
        for vector_id, vector_data in self.vectors.items():
            # 应用过滤器
            if filter_dict:
                metadata = vector_data.get("metadata", {})
                match = True
                for key, value in filter_dict.items():
                    if metadata.get(key) != value:
                        match = False
                        break
                if not match:
                    continue
            
            # 计算余弦相似度
            vector = np.array(vector_data["vector"])
            query = np.array(query_vector)
            
            dot_product = np.dot(vector, query)
            norm_vector = np.linalg.norm(vector)
            norm_query = np.linalg.norm(query)
            
            if norm_vector == 0 or norm_query == 0:
                similarity = 0.0
            else:
                similarity = dot_product / (norm_vector * norm_query)
            
            results.append({
                "id": vector_id,
                "score": float(similarity),
                "metadata": vector_data.get("metadata", {})
            })
        
        # 按相似度排序
        results.sort(key=lambda x: x["score"], reverse=True)
        
        return results[:top_k]
    
    async def delete_vectors(self, ids: List[str]) -> bool:
        """删除向量"""
        for vector_id in ids:
            if vector_id in self.vectors:
                del self.vectors[vector_id]
        
        self._save_to_disk()
        return True
    
    async def update_vectors(
        self,
        ids: List[str],
        vectors: Optional[List[List[float]]] = None,
        metadata: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """更新向量"""
        for i, vector_id in enumerate(ids):
            if vector_id not in self.vectors:
                continue
            
            if vectors and i < len(vectors):
                self.vectors[vector_id]["vector"] = vectors[i]
            
            if metadata and i < len(metadata):
                self.vectors[vector_id]["metadata"] = metadata[i]
        
        self._save_to_disk()
        return True


class VectorMemory(Memory):
    """向量记忆系统"""
    
    def __init__(self, config: Optional[VectorMemoryConfig] = None):
        if config is None:
            config = VectorMemoryConfig()
        
        self.config = config
        
        # 初始化嵌入生成器
        if config.use_openai_embeddings:
            self.embedding_generator = OpenAIEmbeddingGenerator(
                model=config.embedding_model,
                api_key=config.openai_api_key,
                api_base=config.openai_api_base
            )
        else:
            self.embedding_generator = LocalEmbeddingGenerator(
                model_name=config.embedding_model
            )
        
        # 初始化向量存储
        if config.backend == "qdrant":
            self.vector_store = QdrantVectorStore(
                host=config.host,
                port=config.port,
                collection_name=config.collection_name,
                vector_size=config.embedding_dimension
            )
        else:
            self.vector_store = LocalVectorStore(
                storage_path=config.storage_path
            )
        
        # 记忆项缓存
        self.items: Dict[str, MemoryItem] = {}
    
    async def store(self, item: MemoryItem) -> None:
        """存储记忆"""
        # 生成嵌入向量
        embedding = await self.embedding_generator.generate_embedding(item.content)
        
        # 准备元数据
        metadata = {
            "content": item.content,
            "memory_type": item.memory_type,
            "importance": item.importance,
            "timestamp": item.timestamp.isoformat(),
            **item.metadata
        }
        
        # 存储到向量数据库
        ids = await self.vector_store.add_vectors(
            vectors=[embedding],
            metadata=[metadata],
            ids=[item.id]
        )
        
        # 缓存记忆项
        self.items[item.id] = item
        
        logger.debug(f"Stored memory item: {item.id}")
    
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.5,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[MemoryItem]:
        """检索记忆"""
        # 生成查询向量
        query_embedding = await self.embedding_generator.generate_embedding(query)
        
        # 搜索向量
        results = await self.vector_store.search_vectors(
            query_vector=query_embedding,
            top_k=top_k,
            filter_dict=filter_dict
        )
        
        # 转换为记忆项
        memory_items = []
        for result in results:
            if result["score"] < min_score:
                continue
            
            # 从缓存或元数据构建记忆项
            item_id = result["id"]
            if item_id in self.items:
                memory_items.append(self.items[item_id])
            else:
                metadata = result.get("metadata", {})
                item = MemoryItem(
                    id=item_id,
                    content=metadata.get("content", ""),
                    memory_type=metadata.get("memory_type", "observation"),
                    importance=metadata.get("importance", 0.5),
                    metadata=metadata
                )
                memory_items.append(item)
                self.items[item_id] = item
        
        return memory_items
    
    async def store_batch(self, items: List[MemoryItem]) -> List[str]:
        """批量存储记忆"""
        if not items:
            return []
        
        # 批量生成嵌入向量
        contents = [item.content for item in items]
        embeddings = await self.embedding_generator.generate_embeddings(contents)
        
        # 准备元数据
        metadata_list = []
        ids = []
        for item in items:
            metadata = {
                "content": item.content,
                "memory_type": item.memory_type,
                "importance": item.importance,
                "timestamp": item.timestamp.isoformat(),
                **item.metadata
            }
            metadata_list.append(metadata)
            ids.append(item.id)
        
        # 批量存储
        stored_ids = await self.vector_store.add_vectors(
            vectors=embeddings,
            metadata=metadata_list,
            ids=ids
        )
        
        # 缓存记忆项
        for item in items:
            self.items[item.id] = item
        
        logger.debug(f"Stored {len(items)} memory items")
        return stored_ids
    
    async def delete(self, item_id: str) -> bool:
        """删除记忆"""
        success = await self.vector_store.delete_vectors([item_id])
        
        if success and item_id in self.items:
            del self.items[item_id]
        
        return success
    
    async def update(self, item: MemoryItem) -> bool:
        """更新记忆"""
        # 生成新的嵌入向量
        embedding = await self.embedding_generator.generate_embedding(item.content)
        
        # 准备元数据
        metadata = {
            "content": item.content,
            "memory_type": item.memory_type,
            "importance": item.importance,
            "timestamp": item.timestamp.isoformat(),
            **item.metadata
        }
        
        # 更新向量
        success = await self.vector_store.update_vectors(
            ids=[item.id],
            vectors=[embedding],
            metadata=[metadata]
        )
        
        if success:
            self.items[item.id] = item
        
        return success
    
    async def clear(self) -> None:
        """清除所有记忆"""
        # 获取所有ID
        ids = list(self.items.keys())
        
        if ids:
            await self.vector_store.delete_vectors(ids)
        
        self.items.clear()
    
    async def get_by_type(
        self,
        memory_type: str,
        top_k: int = 10
    ) -> List[MemoryItem]:
        """按类型获取记忆"""
        return await self.retrieve(
            query="",
            top_k=top_k,
            min_score=0.0,
            filter_dict={"memory_type": memory_type}
        )
    
    async def get_important(
        self,
        min_importance: float = 0.7,
        top_k: int = 10
    ) -> List[MemoryItem]:
        """获取重要记忆"""
        all_items = list(self.items.values())
        
        # 按重要性过滤和排序
        important_items = [
            item for item in all_items
            if item.importance >= min_importance
        ]
        important_items.sort(key=lambda x: x.importance, reverse=True)
        
        return important_items[:top_k]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_items": len(self.items),
            "memory_types": self._count_by_type(),
            "average_importance": self._average_importance()
        }
    
    def _count_by_type(self) -> Dict[str, int]:
        """按类型统计"""
        counts = {}
        for item in self.items.values():
            memory_type = item.memory_type
            counts[memory_type] = counts.get(memory_type, 0) + 1
        return counts
    
    def _average_importance(self) -> float:
        """计算平均重要性"""
        if not self.items:
            return 0.0
        
        total = sum(item.importance for item in self.items.values())
        return total / len(self.items)


def create_vector_memory(
    backend: str = "local",
    **kwargs
) -> VectorMemory:
    """创建向量记忆系统工厂函数"""
    config = VectorMemoryConfig(backend=backend, **kwargs)
    return VectorMemory(config)
