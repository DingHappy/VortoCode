"""代码索引器 - 代码 Embedding 和向量存储"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ast_parser import (
    ASTParserFactory,
    CodeLanguage,
    CodeNode
)

logger = logging.getLogger(__name__)


class CodeEmbedding:
    """代码 Embedding 生成器"""
    
    def __init__(self, api_key: str = None, model: str = "text-embedding-3-small"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model
        self.base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    
    async def embed_text(self, text: str) -> List[float]:
        """生成文本 embedding"""
        try:
            import aiohttp
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "input": text
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json=payload
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["data"][0]["embedding"]
                    else:
                        logger.error(f"Embedding API error: {response.status}")
                        return self._fallback_embedding(text)
        
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return self._fallback_embedding(text)
    
    async def embed_code(self, code: str, language: CodeLanguage = CodeLanguage.PYTHON) -> List[float]:
        """生成代码 embedding"""
        # 添加语言上下文
        enriched_text = f"Language: {language.value}\n\n{code}"
        return await self.embed_text(enriched_text)
    
    def _fallback_embedding(self, text: str, dim: int = 384) -> List[float]:
        """回退 embedding：词袋 + 哈希技巧（feature hashing）+ L2 归一化。

        与对整段文本做 md5（雪崩、词重叠不体现）不同，这里把每个词哈希到桶里累加，
        使「词重叠」反映在余弦相似度上——无 embedding API 时也能给出有意义的语义检索排序。
        """
        import math
        import re as _re

        vec = [0.0] * dim
        # 英文单词 + 单个中文字符作为 token
        tokens = _re.findall(r"[a-z_][a-z0-9_]*|[一-鿿]", text.lower())
        for tok in tokens:
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0   # 符号哈希，降低碰撞偏置
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class VectorStore:
    """向量存储（内存实现）"""
    
    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self.vectors: Dict[str, List[float]] = {}
        self.metadata: Dict[str, Dict[str, Any]] = {}
    
    def add(self, id: str, vector: List[float], metadata: Dict[str, Any] = None):
        """添加向量"""
        self.vectors[id] = vector
        self.metadata[id] = metadata or {}

    def delete(self, id: str) -> None:
        """删除向量（与 Qdrant 后端接口一致）"""
        self.vectors.pop(id, None)
        self.metadata.pop(id, None)
    
    def search(self, query_vector: List[float], top_k: int = 10) -> List[Tuple[str, float, Dict]]:
        """搜索相似向量"""
        if not self.vectors:
            return []
        
        # 计算余弦相似度
        scores = []
        for id, vector in self.vectors.items():
            similarity = self._cosine_similarity(query_vector, vector)
            scores.append((id, similarity, self.metadata.get(id, {})))
        
        # 按相似度排序
        scores.sort(key=lambda x: x[1], reverse=True)
        
        return scores[:top_k]
    
    def get(self, id: str) -> Optional[Tuple[List[float], Dict[str, Any]]]:
        """获取向量"""
        if id in self.vectors:
            return (self.vectors[id], self.metadata.get(id, {}))
        return None
    
    def delete(self, id: str):
        """删除向量"""
        self.vectors.pop(id, None)
        self.metadata.pop(id, None)
    
    def save(self, path: Path):
        """保存到文件"""
        data = {
            "dimension": self.dimension,
            "vectors": self.vectors,
            "metadata": self.metadata
        }
        path.write_text(json.dumps(data, default=str), encoding='utf-8')
    
    def load(self, path: Path):
        """从文件加载"""
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            self.dimension = data.get("dimension", 384)
            self.vectors = data.get("vectors", {})
            self.metadata = data.get("metadata", {})
    
    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        """计算余弦相似度"""
        if len(v1) != len(v2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(b * b for b in v2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)


class CodeIndexer:
    """代码索引器"""
    
    def __init__(self, workdir: str, embedding: CodeEmbedding = None):
        self.workdir = Path(workdir)
        self.embedding = embedding or CodeEmbedding()
        self.vector_store = make_vector_store()
        self.code_nodes: Dict[str, CodeNode] = {}
        self.file_index: Dict[str, List[str]] = {}  # file -> node/chunk ids
        self.file_hashes: Dict[str, str] = {}       # file -> 内容 hash（用于增量）

        # 索引文件路径
        self.index_path = self.workdir / ".vortocode" / "code_index.json"
    
    async def index_repository(self, progress_callback=None, incremental: bool = True):
        """索引仓库。

        incremental=True 时：先载入已持久化的索引，跳过内容未变更的文件，
        只对新增/变更文件重嵌入，并移除已删除文件的索引——大仓库重索引大幅提速。
        """
        logger.info(f"Indexing repository: {self.workdir} (incremental={incremental})")

        if incremental:
            self.load_index()

        extensions = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs'}
        ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv',
                       'dist', 'build', '.vortocode'}
        files = []
        for ext in extensions:
            files.extend(self.workdir.rglob(f"*{ext}"))
        files = [f for f in files if not any(d in f.parts for d in ignore_dirs)]
        current = {str(f) for f in files}

        # 移除已删除文件的索引
        for stale in [fk for fk in list(self.file_hashes.keys()) if fk not in current]:
            self._remove_file(stale)

        skipped = reindexed = 0
        for i, file_path in enumerate(files):
            fkey = str(file_path)
            try:
                h = self._file_hash(file_path)
                if incremental and self.file_hashes.get(fkey) == h:
                    skipped += 1
                else:
                    self._remove_file(fkey)          # 变更：先清旧节点
                    await self.index_file(file_path)
                    self.file_hashes[fkey] = h
                    reindexed += 1

                if progress_callback:
                    progress_callback(i + 1, len(files), fkey)
            except Exception as e:
                logger.error(f"Failed to index {file_path}: {e}")

        self.save_index()
        logger.info(f"Indexing complete. reindexed={reindexed} skipped={skipped} "
                    f"nodes={len(self.code_nodes)}")

    def _file_hash(self, file_path: Path) -> str:
        try:
            return hashlib.md5(file_path.read_bytes()).hexdigest()
        except Exception:
            return ""

    def _remove_file(self, file_key: str) -> None:
        """移除某文件的全部节点/块向量与元数据。"""
        for nid in self.file_index.get(file_key, []):
            self.vector_store.delete(nid)
            self.code_nodes.pop(nid, None)
        self.file_index.pop(file_key, None)
        self.file_hashes.pop(file_key, None)
    
    async def index_file(self, file_path: Path):
        """索引单个文件"""
        # 解析 AST
        parser = ASTParserFactory.get_parser_for_file(file_path)
        nodes = parser.parse_file(file_path)
        
        # 提取代码块
        chunks = parser.extract_chunks(file_path)
        
        # 为每个节点生成 embedding
        for node in nodes:
            node_id = self._generate_node_id(node)
            
            # 生成 embedding
            text = self._node_to_text(node)
            embedding = await self.embedding.embed_text(text)
            
            # 存储
            self.code_nodes[node_id] = node
            self.vector_store.add(node_id, embedding, {
                "name": node.name,
                "type": node.node_type.value,
                "file": node.location.file,
                "line": node.location.line
            })
            
            # 更新文件索引（node 与 chunk 都登记，便于增量时按文件清理）
            self.file_index.setdefault(str(file_path), []).append(node_id)

        # 为代码块生成 embedding
        for chunk in chunks:
            embedding = await self.embedding.embed_code(chunk.content, chunk.language)

            self.vector_store.add(chunk.id, embedding, {
                "name": chunk.name,
                "type": "chunk",
                "file": chunk.location.file,
                "line": chunk.location.line
            })
            self.file_index.setdefault(str(file_path), []).append(chunk.id)
    
    async def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """语义搜索"""
        # 生成查询向量
        query_embedding = await self.embedding.embed_text(query)
        
        # 搜索
        results = self.vector_store.search(query_embedding, top_k)
        
        # 格式化结果
        formatted = []
        for id, score, metadata in results:
            formatted.append({
                "id": id,
                "score": score,
                "name": metadata.get("name", ""),
                "type": metadata.get("type", ""),
                "file": metadata.get("file", ""),
                "line": metadata.get("line", 0),
                "content": self._get_node_content(id)
            })
        
        return formatted
    
    def get_file_nodes(self, file_path: str) -> List[CodeNode]:
        """获取文件的所有节点"""
        node_ids = self.file_index.get(file_path, [])
        return [self.code_nodes[nid] for nid in node_ids if nid in self.code_nodes]
    
    def get_node(self, node_id: str) -> Optional[CodeNode]:
        """获取节点"""
        return self.code_nodes.get(node_id)
    
    def save_index(self):
        """保存索引"""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 保存向量存储
        vector_path = self.index_path.parent / "vectors.json"
        self.vector_store.save(vector_path)
        
        # 保存节点信息
        nodes_data = {}
        for node_id, node in self.code_nodes.items():
            nodes_data[node_id] = {
                "name": node.name,
                "type": node.node_type.value,
                "file": node.location.file,
                "line": node.location.line,
                "docstring": node.docstring,
                "parameters": node.parameters
            }
        
        self.index_path.write_text(json.dumps({
            "nodes": nodes_data,
            "file_index": self.file_index,
            "file_hashes": self.file_hashes,
        }, indent=2), encoding='utf-8')
    
    def load_index(self):
        """加载索引"""
        if self.index_path.exists():
            data = json.loads(self.index_path.read_text(encoding='utf-8'))
            self.file_index = data.get("file_index", {})
            self.file_hashes = data.get("file_hashes", {})
            
            # 加载向量存储
            vector_path = self.index_path.parent / "vectors.json"
            self.vector_store.load(vector_path)
            
            logger.info(f"Loaded index with {len(self.file_index)} files")
    
    def _generate_node_id(self, node: CodeNode) -> str:
        """生成节点 ID"""
        return f"{node.location.file}:{node.location.line}:{node.name}"
    
    def _node_to_text(self, node: CodeNode) -> str:
        """将节点转换为文本"""
        parts = [f"{node.node_type.value} {node.name}"]
        
        if node.docstring:
            parts.append(node.docstring)
        
        if node.parameters:
            parts.append(f"parameters: {', '.join(node.parameters)}")
        
        return "\n".join(parts)
    
    def _get_node_content(self, node_id: str) -> str:
        """获取节点内容"""
        node = self.code_nodes.get(node_id)
        if node:
            return f"{node.name} (line {node.location.line})"
        return ""


class QdrantVectorStore:
    """Qdrant 持久后端（需 qdrant-client + 运行中的 Qdrant；与 VectorStore 接口兼容）。

    point id 用 uuid5(字符串 id) 派生，确定且可跨重启复用。save/load 为空操作
    （Qdrant 自带持久化）。
    """

    def __init__(self, url: str, dimension: int = 384, collection: str = "autodevcrew"):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.dimension = dimension
        self.collection = collection
        self.client = QdrantClient(url=url)
        try:
            self.client.get_collection(collection)
        except Exception:
            self.client.recreate_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )

    @staticmethod
    def _pid(id: str) -> str:
        import uuid as _uuid
        return str(_uuid.uuid5(_uuid.NAMESPACE_URL, id))

    def add(self, id: str, vector: List[float], metadata: Dict[str, Any] = None):
        from qdrant_client.models import PointStruct
        self.client.upsert(self.collection, points=[
            PointStruct(id=self._pid(id), vector=vector,
                        payload={**(metadata or {}), "_id": id})
        ])

    def delete(self, id: str) -> None:
        self.client.delete(self.collection, points_selector=[self._pid(id)])

    def search(self, query_vector: List[float], top_k: int = 10) -> List[Tuple[str, float, Dict]]:
        res = self.client.search(self.collection, query_vector=query_vector, limit=top_k)
        out = []
        for p in res:
            payload = p.payload or {}
            out.append((payload.get("_id", str(p.id)), float(p.score), payload))
        return out

    def save(self, path=None) -> None:
        pass   # Qdrant 自持久化

    def load(self, path=None) -> None:
        pass


def make_vector_store(dimension: int = 384):
    """按 env 选择向量后端：AUTODEV_QDRANT_URL 配置且 qdrant-client 可用 → Qdrant，否则内存。"""
    import os
    url = os.getenv("AUTODEV_QDRANT_URL", "").strip()
    if url:
        try:
            return QdrantVectorStore(url=url, dimension=dimension)
        except Exception as e:
            logger.warning("Qdrant 不可用，降级内存向量库: %s", e)
    return VectorStore(dimension)
