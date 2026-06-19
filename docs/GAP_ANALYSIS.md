# VortoCode 与主流 Agent 框架差距分析

> **状态更新（2026-06）：本文为历史分析。** 此后已闭合多项差距：角色 Agent 真实 LLM 驱动、
> 测试达 58 用例、模块收敛（31→23 包）、`server.py` 拆分、安全加固。
> 当前真实现状以 [DEVELOPMENT_SUMMARY](DEVELOPMENT_SUMMARY.md) 为准。

## 一、功能对比矩阵

| 功能类别 | 功能项 | Claude Code | Cursor | Devin | OpenManus | MetaGPT | VortoCode |
|---------|--------|-------------|--------|-------|-----------|---------|---------------|
| **核心能力** | 代码理解 | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ 基础 |
| | 代码生成 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 代码编辑 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| | 多文件编辑 | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ |
| **工具集成** | 终端 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 浏览器 | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| | Git | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | MCP 协议 | ✅ | ❌ | ❌ | ✅ | ❌ | ⚠️ |
| **Agent 系统** | 子代理 | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| | 自定义 Agent | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| | Agent 通信 | ✅ | ❌ | ✅ | ✅ | ✅ | ⚠️ |
| **上下文** | 项目记忆 | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| | 代码索引 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| | 语义搜索 | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ |
| **工作流** | 技能系统 | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ |
| | Hooks | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| | 任务编排 | ⚠️ | ❌ | ✅ | ✅ | ✅ | ✅ |
| **执行环境** | 沙箱 | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ |
| | Docker | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| | 浏览器自动化 | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **协作** | 多 Agent 协作 | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| | 人工介入 | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| | 实时通信 | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ |

## 二、关键差距

### 1. 代码理解能力 ❌❌❌

**现状**: 只能读取文件，无法深入理解代码结构

**需要**:
- AST（抽象语法树）解析
- 代码索引和 embedding
- 依赖关系分析
- 语义搜索

**实现方案**:
```python
class CodeIndexer:
    """代码索引器"""
    
    def index_repository(self, path: str):
        # 1. 解析所有代码文件
        # 2. 提取函数、类、变量
        # 3. 生成 embeddings
        # 4. 建立依赖图
    
    def semantic_search(self, query: str) -> List[CodeChunk]:
        # 语义搜索代码
    
    def get_context(self, file: str, line: int) -> CodeContext:
        # 获取代码上下文
```

### 2. 代码编辑能力 ❌❌❌

**现状**: 只能写入整个文件，无法精确编辑

**需要**:
- Diff 生成和应用
- 精确行编辑
- 多文件协同编辑
- 撤销/重做

**实现方案**:
```python
class CodeEditor:
    """代码编辑器"""
    
    def apply_diff(self, file: str, diff: str):
        # 应用 diff
    
    def edit_lines(self, file: str, start: int, end: int, content: str):
        # 精确行编辑
    
    def refactor(self, pattern: str, replacement: str, files: List[str]):
        # 跨文件重构
```

### 3. 浏览器自动化 ❌❌

**现状**: 无法与 Web 应用交互

**需要**:
- Playwright/Puppeteer 集成
- 页面导航和交互
- 截图和内容提取
- 表单填写

**实现方案**:
```python
class BrowserTool:
    """浏览器工具"""
    
    async def navigate(self, url: str):
        # 导航到 URL
    
    async def click(self, selector: str):
        # 点击元素
    
    async def fill(self, selector: str, value: str):
        # 填写表单
    
    async def screenshot(self) -> bytes:
        # 截图
    
    async def get_content(self) -> str:
        # 获取页面内容
```

### 4. 沙箱执行环境 ❌❌

**现状**: 直接在宿主机执行命令，无隔离

**需要**:
- Docker 容器隔离
- 资源限制
- 安全边界
- 环境快照

**实现方案**:
```python
class Sandbox:
    """沙箱环境"""
    
    def create(self, image: str) -> str:
        # 创建容器
    
    def execute(self, container_id: str, command: str) -> str:
        # 在容器内执行
    
    def copy_file(self, container_id: str, local: str, remote: str):
        # 复制文件到容器
    
    def destroy(self, container_id: str):
        # 销毁容器
```

### 5. 代码索引和语义搜索 ❌❌

**现状**: 无法理解代码库结构

**需要**:
- 代码 embedding
- 向量数据库
- 语义搜索
- 上下文检索

**实现方案**:
```python
class CodeEmbedding:
    """代码嵌入"""
    
    def embed_code(self, code: str) -> List[float]:
        # 生成代码向量
    
    def find_similar(self, query: str, top_k: int) -> List[CodeChunk]:
        # 查找相似代码
    
    def get_context_window(self, file: str, line: int, window: int) -> str:
        # 获取上下文窗口
```

### 6. 多模态能力 ❌

**现状**: 只支持文本

**需要**:
- 图像理解（截图分析）
- 文档解析（PDF、图片）
- 语音输入

### 7. 任务规划和分解 ⚠️

**现状**: 基础的任务分解

**需要**:
- 更智能的任务规划
- 依赖关系优化
- 并行执行优化
- 失败恢复策略

### 8. 学习和适应 ❌

**现状**: 无学习能力

**需要**:
- 从成功/失败中学习
- 用户偏好记忆
- 代码风格适应
- 最佳实践积累

## 三、优先级排序

### P0 - 核心差距（必须解决）

1. **代码索引和理解** - 没有这个，Agent 就是瞎子
2. **精确代码编辑** - 只能写整个文件太原始
3. **沙箱执行** - 安全性必需

### P1 - 重要差距

4. **浏览器自动化** - 前端开发必需
5. **语义搜索** - 提升代码理解能力
6. **多文件重构** - 真实项目需要

### P2 - 增强功能

7. **多模态支持** - 图像理解
8. **学习系统** - 长期优化
9. **性能优化** - 大项目支持

## 四、实现路线图

### Phase 1: 代码理解（2-3 周）

```
Week 1: AST 解析器
  - Python AST
  - JavaScript/TypeScript AST
  - 代码结构提取

Week 2: 代码索引
  - Embedding 生成
  - 向量数据库集成
  - 语义搜索

Week 3: 上下文管理
  - 依赖关系图
  - 上下文窗口
  - 智能检索
```

### Phase 2: 代码编辑（2-3 周）

```
Week 4: Diff 系统
  - Diff 生成
  - Diff 应用
  - 冲突解决

Week 5: 精确编辑
  - 行级编辑
  - 块级编辑
  - 撤销/重做

Week 6: 多文件编辑
  - 跨文件重构
  - 批量编辑
  - 原子操作
```

### Phase 3: 沙箱和浏览器（2-3 周）

```
Week 7: Docker 沙箱
  - 容器管理
  - 资源限制
  - 文件挂载

Week 8: 浏览器自动化
  - Playwright 集成
  - 页面交互
  - 截图分析

Week 9: 安全增强
  - 权限细化
  - 审计日志
  - 恢复机制
```

### Phase 4: 智能增强（2-3 周）

```
Week 10: 学习系统
  - 成功/失败分析
  - 模式识别
  - 知识积累

Week 11: 多模态
  - 图像理解
  - 文档解析
  - 截图分析

Week 12: 性能优化
  - 缓存机制
  - 并行处理
  - 资源管理
```

## 五、技术选型建议

| 组件 | 推荐方案 | 备选方案 |
|------|----------|----------|
| AST 解析 | tree-sitter | ast (Python) |
| 向量数据库 | Qdrant | Milvus, Pinecone |
| Embedding | OpenAI text-embedding-3 | sentence-transformers |
| 浏览器 | Playwright | Puppeteer |
| 沙箱 | Docker | Firecracker |
| 代码编辑 | difflib | unidiff |
| 语义搜索 | FAISS | Annoy |

## 六、总结

当前 VortoCode 与主流框架的主要差距：

| 差距 | 严重程度 | 解决难度 | 优先级 |
|------|----------|----------|--------|
| 代码理解 | 🔴 严重 | 中等 | P0 |
| 精确编辑 | 🔴 严重 | 中等 | P0 |
| 沙箱执行 | 🔴 严重 | 简单 | P0 |
| 浏览器 | 🟡 中等 | 中等 | P1 |
| 语义搜索 | 🟡 中等 | 中等 | P1 |
| 学习能力 | 🟢 轻微 | 复杂 | P2 |

**建议**: 先实现 P0 功能（代码理解、精确编辑、沙箱），再逐步添加 P1、P2 功能。
