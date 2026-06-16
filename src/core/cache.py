"""缓存系统"""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CacheEntry(BaseModel):
    """缓存条目"""
    key: str
    value: Any
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    access_count: int = 0
    last_accessed: datetime = Field(default_factory=datetime.now)


class CacheStats(BaseModel):
    """缓存统计"""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class MemoryCache:
    """内存缓存"""
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 3600):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.stats = CacheStats()
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        async with self._lock:
            entry = self.cache.get(key)
            
            if not entry:
                self.stats.misses += 1
                return None
            
            # 检查是否过期
            if entry.expires_at and datetime.now() > entry.expires_at:
                del self.cache[key]
                self.stats.misses += 1
                return None
            
            # 更新访问信息
            entry.access_count += 1
            entry.last_accessed = datetime.now()
            
            # 移到末尾（LRU）
            self.cache.move_to_end(key)
            
            self.stats.hits += 1
            return entry.value
    
    async def set(
        self, 
        key: str, 
        value: Any, 
        ttl: Optional[int] = None
    ):
        """设置缓存"""
        async with self._lock:
            # 计算过期时间
            expires_at = None
            if ttl or self.default_ttl:
                ttl = ttl or self.default_ttl
                expires_at = datetime.now() + timedelta(seconds=ttl)
            
            # 创建缓存条目
            entry = CacheEntry(
                key=key,
                value=value,
                expires_at=expires_at
            )
            
            # 如果已存在，更新
            if key in self.cache:
                self.cache[key] = entry
                self.cache.move_to_end(key)
            else:
                # 检查是否需要淘汰
                if len(self.cache) >= self.max_size:
                    self._evict()
                
                self.cache[key] = entry
            
            self.stats.size = len(self.cache)
    
    async def delete(self, key: str) -> bool:
        """删除缓存"""
        async with self._lock:
            if key in self.cache:
                del self.cache[key]
                self.stats.size = len(self.cache)
                return True
            return False
    
    async def clear(self):
        """清除所有缓存"""
        async with self._lock:
            self.cache.clear()
            self.stats = CacheStats()
    
    def _evict(self):
        """淘汰缓存"""
        if self.cache:
            # LRU 淘汰
            self.cache.popitem(last=False)
            self.stats.evictions += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "hit_rate": self.stats.hit_rate,
            "evictions": self.stats.evictions
        }


class RedisCache:
    """Redis 缓存"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379", prefix: str = "auto-dev:"):
        self.redis_url = redis_url
        self.prefix = prefix
        self.client = None
    
    async def connect(self):
        """连接 Redis"""
        try:
            import redis.asyncio as redis
            self.client = redis.from_url(self.redis_url)
            await self.client.ping()
            logger.info("Connected to Redis")
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            self.client = None
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        if not self.client:
            return None
        
        try:
            data = await self.client.get(f"{self.prefix}{key}")
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None
    
    async def set(
        self, 
        key: str, 
        value: Any, 
        ttl: Optional[int] = None
    ):
        """设置缓存"""
        if not self.client:
            return
        
        try:
            data = json.dumps(value)
            if ttl:
                await self.client.setex(f"{self.prefix}{key}", ttl, data)
            else:
                await self.client.set(f"{self.prefix}{key}", data)
        except Exception as e:
            logger.error(f"Redis set error: {e}")
    
    async def delete(self, key: str) -> bool:
        """删除缓存"""
        if not self.client:
            return False
        
        try:
            result = await self.client.delete(f"{self.prefix}{key}")
            return result > 0
        except Exception as e:
            logger.error(f"Redis delete error: {e}")
            return False
    
    async def clear(self):
        """清除所有缓存"""
        if not self.client:
            return
        
        try:
            keys = await self.client.keys(f"{self.prefix}*")
            if keys:
                await self.client.delete(*keys)
        except Exception as e:
            logger.error(f"Redis clear error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "connected": self.client is not None,
            "redis_url": self.redis_url
        }


class CacheManager:
    """缓存管理器"""
    
    def __init__(self, use_redis: bool = False, redis_url: str = None):
        self.memory_cache = MemoryCache()
        self.redis_cache = None
        
        if use_redis and redis_url:
            self.redis_cache = RedisCache(redis_url)
    
    async def initialize(self):
        """初始化缓存"""
        if self.redis_cache:
            await self.redis_cache.connect()
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        # 先查内存缓存
        value = await self.memory_cache.get(key)
        if value is not None:
            return value
        
        # 再查 Redis
        if self.redis_cache:
            value = await self.redis_cache.get(key)
            if value is not None:
                # 写入内存缓存
                await self.memory_cache.set(key, value)
                return value
        
        return None
    
    async def set(
        self, 
        key: str, 
        value: Any, 
        ttl: Optional[int] = None,
        memory_only: bool = False
    ):
        """设置缓存"""
        # 写入内存缓存
        await self.memory_cache.set(key, value, ttl)
        
        # 写入 Redis
        if self.redis_cache and not memory_only:
            await self.redis_cache.set(key, value, ttl)
    
    async def delete(self, key: str):
        """删除缓存"""
        await self.memory_cache.delete(key)
        
        if self.redis_cache:
            await self.redis_cache.delete(key)
    
    async def clear(self):
        """清除所有缓存"""
        await self.memory_cache.clear()
        
        if self.redis_cache:
            await self.redis_cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            "memory": self.memory_cache.get_stats()
        }
        
        if self.redis_cache:
            stats["redis"] = self.redis_cache.get_stats()
        
        return stats


def cache_key(*args, **kwargs) -> str:
    """生成缓存键"""
    key_parts = [str(arg) for arg in args]
    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    key_str = ":".join(key_parts)
    return hashlib.md5(key_str.encode()).hexdigest()


def cached(ttl: int = 3600, key_prefix: str = ""):
    """缓存装饰器"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 生成缓存键
            key = f"{key_prefix}:{func.__name__}:{cache_key(*args, **kwargs)}"
            
            # 获取缓存管理器
            cache = getattr(wrapper, '_cache', None)
            if not cache:
                return await func(*args, **kwargs)
            
            # 尝试获取缓存
            result = await cache.get(key)
            if result is not None:
                return result
            
            # 执行函数
            result = await func(*args, **kwargs)
            
            # 缓存结果
            await cache.set(key, result, ttl)
            
            return result
        
        return wrapper
    return decorator
