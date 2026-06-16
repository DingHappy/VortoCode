---
name: api-developer
description: 实现 RESTful API 端点
version: "1.0.0"
capabilities:
  - api_development
  - code_generation
tools:
  - Read
  - Write
  - Bash
arguments:
  - name: endpoint
    description: API 端点路径
    required: true
  - name: method
    description: HTTP 方法
    required: false
    default: "GET"
---

## API 开发任务

实现 API 端点 `$endpoint`，HTTP 方法为 `$method`。

## 实现步骤

### 1. 需求分析
- 理解端点用途
- 确定请求/响应格式
- 识别业务逻辑

### 2. 数据模型
- 设计请求模型
- 设计响应模型
- 定义验证规则

### 3. 实现端点
- 创建路由处理函数
- 实现业务逻辑
- 添加输入验证
- 处理错误情况

### 4. 编写测试
- 单元测试
- 集成测试
- 边界条件测试

## 代码规范

- 使用类型注解
- 添加文档字符串
- 遵循 PEP 8
- 处理所有错误

## 输出

```python
# 路由实现
@router.$method("$endpoint")
async def handler(request: Request):
    # 实现
    pass
```
