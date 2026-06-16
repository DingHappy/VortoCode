"""任务队列系统"""

import asyncio
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    """任务优先级"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class Task(BaseModel):
    """任务"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    func: str  # 函数名
    args: List[Any] = Field(default_factory=list)
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retries: int = 0
    max_retries: int = 3
    timeout: int = 300  # 秒


class TaskQueue:
    """任务队列"""
    
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.tasks: Dict[str, Task] = {}
        self.workers: List[asyncio.Task] = []
        self.running = False
        self.handlers: Dict[str, Callable] = {}
    
    def register_handler(self, name: str, handler: Callable):
        """注册任务处理器"""
        self.handlers[name] = handler
    
    async def submit(self, task: Task) -> str:
        """提交任务"""
        self.tasks[task.id] = task
        await self.queue.put((task.priority.value, task.id))
        
        logger.info(f"Task submitted: {task.name} ({task.id})")
        return task.id
    
    async def start(self):
        """启动队列"""
        self.running = True
        
        # 启动工作线程
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self.workers.append(worker)
        
        logger.info(f"Task queue started with {self.max_workers} workers")
    
    async def stop(self):
        """停止队列"""
        # 先等待队列排空（此时 workers 仍在运行才能消费完，否则 join 永久阻塞）
        await self.queue.join()

        # 再停止 worker 循环并取消
        self.running = False
        for worker in self.workers:
            worker.cancel()
        
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        
        logger.info("Task queue stopped")
    
    async def _worker(self, worker_id: str):
        """工作线程"""
        logger.info(f"Worker {worker_id} started")
        
        while self.running:
            try:
                # 获取任务
                priority, task_id = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=1.0
                )
                
                task = self.tasks.get(task_id)
                if not task:
                    continue
                
                # 执行任务
                await self._execute_task(worker_id, task)
                
                # 标记任务完成
                self.queue.task_done()
            
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
    
    async def _execute_task(self, worker_id: str, task: Task):
        """执行任务"""
        logger.info(f"Worker {worker_id} executing task: {task.name}")
        
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        
        try:
            # 获取处理器
            handler = self.handlers.get(task.func)
            if not handler:
                raise ValueError(f"No handler registered for: {task.func}")
            
            # 执行任务
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(
                    handler(*task.args, **task.kwargs),
                    timeout=task.timeout
                )
            else:
                result = handler(*task.args, **task.kwargs)
            
            # 更新任务状态
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.now()
            
            logger.info(f"Task completed: {task.name}")
        
        except asyncio.TimeoutError:
            logger.error(f"Task timeout: {task.name}")
            task.status = TaskStatus.FAILED
            task.error = "Task timed out"
            
            # 重试
            if task.retries < task.max_retries:
                task.retries += 1
                task.status = TaskStatus.PENDING
                await self.queue.put((task.priority.value, task.id))
                logger.info(f"Retrying task: {task.name} (attempt {task.retries})")
        
        except Exception as e:
            logger.error(f"Task failed: {task.name} - {e}")
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now()
            
            # 重试
            if task.retries < task.max_retries:
                task.retries += 1
                task.status = TaskStatus.PENDING
                await self.queue.put((task.priority.value, task.id))
                logger.info(f"Retrying task: {task.name} (attempt {task.retries})")
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        return self.tasks.get(task_id)
    
    def list_tasks(self, status: Optional[TaskStatus] = None) -> List[Task]:
        """列出任务"""
        tasks = list(self.tasks.values())
        
        if status:
            tasks = [t for t in tasks if t.status == status]
        
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        tasks = list(self.tasks.values())
        
        return {
            "total": len(tasks),
            "pending": len([t for t in tasks if t.status == TaskStatus.PENDING]),
            "running": len([t for t in tasks if t.status == TaskStatus.RUNNING]),
            "completed": len([t for t in tasks if t.status == TaskStatus.COMPLETED]),
            "failed": len([t for t in tasks if t.status == TaskStatus.FAILED]),
            "cancelled": len([t for t in tasks if t.status == TaskStatus.CANCELLED]),
            "queue_size": self.queue.qsize(),
            "workers": len(self.workers)
        }
    
    async def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self.tasks.get(task_id)
        if task and task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
            return True
        return False
    
    def clear_completed(self):
        """清除已完成的任务"""
        self.tasks = {
            k: v for k, v in self.tasks.items()
            if v.status not in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]
        }


class TaskScheduler:
    """任务调度器"""
    
    def __init__(self, queue: TaskQueue):
        self.queue = queue
        self.scheduled_tasks: Dict[str, Dict[str, Any]] = {}
    
    def schedule(
        self,
        name: str,
        func: str,
        cron: str = None,
        interval: int = None,
        args: List[Any] = None,
        kwargs: Dict[str, Any] = None
    ):
        """调度任务"""
        task_config = {
            "name": name,
            "func": func,
            "cron": cron,
            "interval": interval,
            "args": args or [],
            "kwargs": kwargs or {},
            "last_run": None,
            "next_run": None
        }
        
        self.scheduled_tasks[name] = task_config
        logger.info(f"Scheduled task: {name}")
    
    async def run_scheduled(self):
        """运行调度任务"""
        while True:
            now = datetime.now()
            
            for name, config in self.scheduled_tasks.items():
                # 检查是否到了执行时间
                if config["interval"]:
                    last_run = config["last_run"]
                    if not last_run or (now - last_run).total_seconds() >= config["interval"]:
                        # 提交任务
                        task = Task(
                            name=name,
                            func=config["func"],
                            args=config["args"],
                            kwargs=config["kwargs"]
                        )
                        await self.queue.submit(task)
                        
                        config["last_run"] = now
            
            await asyncio.sleep(1)
