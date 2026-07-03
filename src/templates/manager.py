"""项目模板系统 - 预定义项目模板"""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TemplateFile(BaseModel):
    """模板文件"""
    path: str
    content: str
    is_template: bool = True  # 是否需要变量替换


class TemplateVariable(BaseModel):
    """模板变量"""
    name: str
    description: str
    default: str = ""
    required: bool = True


class ProjectTemplate(BaseModel):
    """项目模板"""
    id: str
    name: str
    description: str
    category: str  # web, api, cli, library, etc.
    tech_stack: List[str] = Field(default_factory=list)
    variables: List[TemplateVariable] = Field(default_factory=list)
    files: List[TemplateFile] = Field(default_factory=list)
    instructions: str = ""
    icon: str = "📦"


# 预定义模板
TEMPLATES: List[ProjectTemplate] = [
    ProjectTemplate(
        id="fastapi-react",
        name="FastAPI + React 全栈应用",
        description="使用 FastAPI 后端 + React 前端的全栈 Web 应用",
        category="web",
        tech_stack=["FastAPI", "React", "TypeScript", "PostgreSQL"],
        icon="🌐",
        variables=[
            TemplateVariable(name="project_name", description="项目名称", required=True),
            TemplateVariable(name="description", description="项目描述", default="A full-stack web application"),
        ],
        files=[
            TemplateFile(
                path="backend/main.py",
                content='''"""FastAPI 后端"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="${project_name}")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Welcome to ${project_name}"}

@app.get("/api/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
'''
            ),
            TemplateFile(
                path="backend/requirements.txt",
                content="fastapi>=0.100.0\nuvicorn>=0.23.0\npydantic>=2.0.0\n"
            ),
            TemplateFile(
                path="frontend/package.json",
                content='''{
  "name": "${project_name}-frontend",
  "version": "1.0.0",
  "scripts": {
    "dev": "vite",
    "build": "vite build"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  }
}'''
            ),
            TemplateFile(
                path="README.md",
                content='''# ${project_name}

${description}

## 技术栈
${tech_stack}

## 快速开始

### 后端
```bash
cd backend
pip install -r requirements.txt
python main.py
```

### 前端
```bash
cd frontend
npm install
npm run dev
```
'''
            ),
            TemplateFile(
                path=".gitignore",
                content="__pycache__/\n*.pyc\nnode_modules/\n.env\n"
            ),
        ],
        instructions="这是一个全栈 Web 应用模板，包含 FastAPI 后端和 React 前端。"
    ),
    
    ProjectTemplate(
        id="fastapi-api",
        name="FastAPI REST API",
        description="轻量级 REST API 服务",
        category="api",
        tech_stack=["FastAPI", "SQLAlchemy", "Pydantic"],
        icon="🔌",
        variables=[
            TemplateVariable(name="project_name", description="项目名称", required=True),
            TemplateVariable(name="database_url", description="数据库 URL", default="sqlite:///./app.db"),
        ],
        files=[
            TemplateFile(
                path="main.py",
                content='''"""${project_name} API"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import uvicorn

app = FastAPI(title="${project_name}")

# 数据模型
class Item(BaseModel):
    id: Optional[int] = None
    name: str
    description: Optional[str] = None

# 内存存储
items_db: Dict[int, Item] = {}
next_id = 1

@app.get("/items", response_model=List[Item])
async def list_items():
    return list(items.values())

@app.post("/items", response_model=Item)
async def create_item(item: Item):
    global next_id
    item.id = next_id
    next_id += 1
    items[item.id] = item
    return item

@app.get("/items/{item_id}", response_model=Item)
async def get_item(item_id: int):
    if item_id not in items:
        raise HTTPException(status_code=404, detail="Item not found")
    return items[item_id]

@app.put("/items/{item_id}", response_model=Item)
async def update_item(item_id: int, item: Item):
    if item_id not in items:
        raise HTTPException(status_code=404, detail="Item not found")
    item.id = item_id
    items[item_id] = item
    return item

@app.delete("/items/{item_id}")
async def delete_item(item_id: int):
    if item_id not in items:
        raise HTTPException(status_code=404, detail="Item not found")
    del items[item_id]
    return {"message": "Deleted"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
'''
            ),
            TemplateFile(
                path="requirements.txt",
                content="fastapi>=0.100.0\nuvicorn>=0.23.0\n"
            ),
            TemplateFile(
                path="README.md",
                content="# ${project_name}\n\n${description}\n"
            ),
        ],
        instructions="这是一个 REST API 模板，包含 CRUD 操作。"
    ),
    
    ProjectTemplate(
        id="python-cli",
        name="Python CLI 工具",
        description="命令行工具模板",
        category="cli",
        tech_stack=["Python", "Click", "Rich"],
        icon="⌨️",
        variables=[
            TemplateVariable(name="project_name", description="项目名称", required=True),
            TemplateVariable(name="author", description="作者", default="Developer"),
        ],
        files=[
            TemplateFile(
                path="src/cli.py",
                content='''"""${project_name} CLI"""

import click
from rich.console import Console
from rich.table import Table

console = Console()

@click.group()
@click.version_option(version="1.0.0")
def cli():
    """${project_name} - 命令行工具"""
    pass

@cli.command()
@click.argument("name")
def hello(name: str):
    """打招呼"""
    console.print(f"Hello, {name}! 👋")

@cli.command()
def info():
    """显示信息"""
    table = Table(title="${project_name}")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Version", "1.0.0")
    table.add_row("Author", "${author}")
    console.print(table)

if __name__ == "__main__":
    cli()
'''
            ),
            TemplateFile(
                path="requirements.txt",
                content="click>=8.0.0\nrich>=13.0.0\n"
            ),
            TemplateFile(
                path="setup.py",
                content='''from setuptools import setup, find_packages

setup(
    name="${project_name}",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.0.0",
        "rich>=13.0.0",
    ],
    entry_points={
        "console_scripts": [
            "${project_name}=src.cli:cli",
        ],
    },
)
'''
            ),
        ],
        instructions="这是一个 CLI 工具模板，使用 Click 和 Rich。"
    ),
    
    ProjectTemplate(
        id="python-library",
        name="Python 库",
        description="可发布的 Python 库模板",
        category="library",
        tech_stack=["Python", "Pytest", "Sphinx"],
        icon="📚",
        variables=[
            TemplateVariable(name="project_name", description="项目名称", required=True),
            TemplateVariable(name="author", description="作者", default="Developer"),
            TemplateVariable(name="description", description="项目描述", default="A Python library"),
        ],
        files=[
            TemplateFile(
                path="src/__init__.py",
                content='''"""${project_name}"""

__version__ = "1.0.0"
__author__ = "${author}"

def hello():
    return "Hello from ${project_name}!"
'''
            ),
            TemplateFile(
                path="tests/test_main.py",
                content='''"""测试"""

import pytest
from src import hello

def test_hello():
    assert hello() == "Hello from ${project_name}!"
'''
            ),
            TemplateFile(
                path="pyproject.toml",
                content='''[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "${project_name}"
version = "1.0.0"
description = "${description}"
authors = [{name = "${author}"}]
requires-python = ">=3.9"

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
]
'''
            ),
            TemplateFile(
                path="README.md",
                content='''# ${project_name}

${description}

## 安装

```bash
pip install ${project_name}
```

## 使用

```python
from ${project_name} import hello

print(hello())
```

## 开发

```bash
pip install -e ".[dev]"
pytest
```
'''
            ),
        ],
        instructions="这是一个 Python 库模板，包含测试和文档。"
    ),
    
    ProjectTemplate(
        id="data-pipeline",
        name="数据管道",
        description="数据处理管道模板",
        category="data",
        tech_stack=["Python", "Pandas", "SQLAlchemy"],
        icon="📊",
        variables=[
            TemplateVariable(name="project_name", description="项目名称", required=True),
            TemplateVariable(name="data_source", description="数据源", default="csv"),
        ],
        files=[
            TemplateFile(
                path="pipeline/extract.py",
                content='''"""数据提取"""

import pandas as pd
from pathlib import Path

def extract_from_csv(file_path: str) -> pd.DataFrame:
    """从 CSV 提取数据"""
    return pd.read_csv(file_path)

def extract_from_db(query: str, connection_string: str) -> pd.DataFrame:
    """从数据库提取数据"""
    from sqlalchemy import create_engine
    engine = create_engine(connection_string)
    return pd.read_sql(query, engine)
'''
            ),
            TemplateFile(
                path="pipeline/transform.py",
                content='''"""数据转换"""

import pandas as pd

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """清洗数据"""
    # 去除重复
    df = df.drop_duplicates()
    # 去除空值
    df = df.dropna()
    return df

def transform(df: pd.DataFrame) -> pd.DataFrame:
    """转换数据"""
    df = clean_data(df)
    # 添加你的转换逻辑
    return df
'''
            ),
            TemplateFile(
                path="pipeline/load.py",
                content='''"""数据加载"""

import pandas as pd

def load_to_csv(df: pd.DataFrame, file_path: str):
    """保存到 CSV"""
    df.to_csv(file_path, index=False)

def load_to_db(df: pd.DataFrame, table_name: str, connection_string: str):
    """保存到数据库"""
    from sqlalchemy import create_engine
    engine = create_engine(connection_string)
    df.to_sql(table_name, engine, if_exists='replace', index=False)
'''
            ),
            TemplateFile(
                path="main.py",
                content='''"""${project_name} - 数据管道"""

from pipeline.extract import extract_from_csv
from pipeline.transform import transform
from pipeline.load import load_to_csv

def run_pipeline(input_file: str, output_file: str):
    """运行管道"""
    print(f"提取数据: {input_file}")
    df = extract_from_csv(input_file)
    
    print("转换数据...")
    df = transform(df)
    
    print(f"加载数据: {output_file}")
    load_to_csv(df, output_file)
    
    print("完成!")

if __name__ == "__main__":
    run_pipeline("data/input.csv", "data/output.csv")
'''
            ),
        ],
        instructions="这是一个数据管道模板，包含提取、转换、加载流程。"
    ),
]


class TemplateManager:
    """模板管理器"""
    
    def __init__(self):
        self.templates: Dict[str, ProjectTemplate] = {}
        self._load_default_templates()
    
    def _load_default_templates(self):
        """加载默认模板"""
        for template in TEMPLATES:
            self.templates[template.id] = template
    
    def get_template(self, template_id: str) -> Optional[ProjectTemplate]:
        """获取模板"""
        return self.templates.get(template_id)
    
    def list_templates(self, category: str = None) -> List[ProjectTemplate]:
        """列出模板"""
        if category:
            return [t for t in self.templates.values() if t.category == category]
        return list(self.templates.values())
    
    def create_project(
        self,
        template_id: str,
        output_dir: str,
        variables: Dict[str, str] = None
    ) -> List[str]:
        """从模板创建项目"""
        template = self.templates.get(template_id)
        if not template:
            raise ValueError(f"Template not found: {template_id}")
        
        variables = variables or {}
        created_files = []
        
        # 创建输出目录
        project_dir = Path(output_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建文件
        for file_template in template.files:
            file_path = project_dir / file_template.path
            
            # 创建父目录
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 替换变量
            content = file_template.content
            if file_template.is_template:
                for key, value in variables.items():
                    content = content.replace(f"${{{key}}}", str(value))
                
                # 替换模板变量
                for var in template.variables:
                    if var.name in variables:
                        content = content.replace(f"${{{var.name}}}", variables[var.name])
                    elif var.default:
                        content = content.replace(f"${{{var.name}}}", var.default)
            
            # 写入文件
            file_path.write_text(content, encoding='utf-8')
            created_files.append(str(file_path))
            
            logger.info(f"Created file: {file_path}")
        
        return created_files
