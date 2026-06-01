# Hermes Document Pipeline

面向生产环境的文档与发票处理流水线：统一 CLI、健康检查、结构化入库、语义检索，以及公开仓库安全发布。

[English Version](./README.md)

## 这个项目做什么

Hermes Agent 适合做编排，但业务流程需要独立、稳定、可交接的运行时和工具层。本仓库将这层能力整理为一个小型 Python 项目，并提供统一入口 `./project-tool`。

新用户可以按以下步骤快速完成到可用状态：
1. Clone 仓库
2. 创建 venv
3. 填好 `.env`
4. 执行基础验证
5. 获得可用的发票和文档处理流水线

## 核心能力

- 发票入库：接收 JSON 载荷，UPSERT 到 Cloudflare D1，并同步发票文本到 ChromaDB 以便语义检索。
- 文档处理：支持 PDF / DOCX / TXT / MD / LOG / CSV / XLSX / XLS 文本提取，本地 MD5 去重归档，元数据写入 D1，检索向量写入 ChromaDB。
- 健康检查：校验 Python 运行时、依赖、D1、ChromaDB 与 CLI 入口；导出 JSON / Markdown 报告；`doctor --fix` 可自动修复常见漂移。
- 腾讯会议自动机器人：Headless 二维码登录、定时入会、本地录音、分段转写加 AI 摘要、结果推送到 Telegram。

## 快速开始

### 1）克隆仓库

```bash
git clone https://github.com/benben17/hermes-document-pipeline.git
cd hermes-document-pipeline
```

### 2）创建 Python 环境

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 3）配置环境

```bash
cp .env.example .env
```

必填：
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_FINANCE_D1_DATABASE_ID`
- `CHROMA_HOST`
- `CHROMA_PORT`

可选：
- `HERMES_NEWS_TARGET`

### 4）验证 CLI

```bash
./project-tool --help
```

### 5）Bootstrap 检查（无副作用，推荐首次执行）

```bash
./project-tool doctor --bootstrap --json
```

仅校验本地 Python 运行时、依赖、入口脚本，无需 D1 或 ChromaDB 已就绪。

### 6）完整集成检查（配置好 D1 + ChromaDB 后再执行）

```bash
./project-tool doctor --no-qq-send
```

## CLI 用法

```bash
# 发票处理
echo '{"invoice_number": "INV-001", ...}' | ./project-tool invoice

# 文档处理
echo '{"title": "合同", "company": "XX公司", "file_path_src": "/tmp/a.pdf"}' | ./project-tool doc

# 报表
./project-tool report invoices
./project-tool report documents

# 健康检查
./project-tool doctor --bootstrap --json   # 首次安装验证
./project-tool doctor --json               # 完整检查
./project-tool doctor --fix                # 自动修复运行时漂移
```

## 输入约定

### `invoice` 命令

| 字段 | 必填 | 说明 |
|------|------|------|
| `invoice_number` | 是 | 发票号（主键） |
| `invoice_date` | 否 | 开票日期（yyyy-mm-dd） |
| `buyer_name` | 否 | 购买方 |
| `seller_name` | 否 | 销售方 |
| `item_name` | 否 | 商品或服务名称 |
| `amount_net` | 否 | 不含税金额 |
| `tax_amount` | 否 | 税额 |
| `total_amount` | 否 | 价税合计 |
| `file_path` | 否 | 原始文件路径（默认 `manual_entry`） |
| `raw_text` | 否 | 发票提取全文，用于语义检索索引 |

### `doc` 命令

| 字段 | 必填 | 说明 |
|------|------|------|
| `file_path_src` | 是 | 源文件绝对路径 |
| `title` | 是 | 文档标题（用作 ChromaDB 唯一 ID） |
| `company` | 否 | 所属公司或机构 |
| `category` | 否 | 文档类型（合同、报告、协议等） |
| `summary` | 否 | 内容摘要 |
| `tags` | 否 | 逗号分隔的标签 |
| `raw_text` | 否 | 已提取文本，提供后跳过重新提取 |

## D1 表结构

需在 Cloudflare D1 中提前建立的表：

```sql
CREATE TABLE invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT UNIQUE NOT NULL,
    invoice_date   TEXT,
    buyer_name     TEXT,
    seller_name    TEXT,
    item_name      TEXT,
    amount_net     REAL,
    tax_amount     REAL,
    total_amount   REAL,
    file_path      TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT,
    company    TEXT,
    category   TEXT,
    summary    TEXT,
    tags       TEXT,
    file_path  TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

## 配置加载顺序

1. `$HERMES_PROJECT_ENV`（显式指定路径，若设置则最先加载）
2. `<project_root>/.env`
3. `$HERMES_HOME/.env`
4. 进程环境变量（始终优先）
5. 内置默认值兜底

关键变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROJECT_ROOT` | 脚本所在目录 | 项目根目录 |
| `HERMES_HOME` | `~/.hermes` | Hermes 配置目录 |
| `CLOUDFLARE_API_TOKEN` | — | **必填**，D1 鉴权 |
| `CLOUDFLARE_ACCOUNT_ID` | — | **必填**，D1 鉴权 |
| `CLOUDFLARE_FINANCE_D1_DATABASE_ID` | — | **必填**，D1 数据库 ID |
| `CHROMA_HOST` | `localhost` | ChromaDB 主机 |
| `CHROMA_PORT` | `8000` | ChromaDB 端口 |
| `HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR` | `<root>/archive/documents` | 文档归档路径 |
| `HERMES_PROJECT_REPORT_DIR` | `<root>/doctor-reports` | 报告输出路径 |
| `HERMES_NEWS_TARGET` | `qqbot` | Hermes 投递目标 |
| `FIRECRAWL_API_KEY` | — | 可选，启用 doctor Firecrawl 探针 |

## `doctor` 检查项

| 检查 | 触发条件 |
|------|----------|
| `.venv` 存在 | 始终 |
| 必需 Python 依赖可 import | 始终 |
| 入口脚本完整性 | 始终 |
| D1 连通性 + schema | `--json` 或默认模式 |
| ChromaDB 连通性 + 集合 | `--json` 或默认模式 |
| Firecrawl key + 搜索探针 | `--json` 或默认模式 |
| Hermes 投递目标可用性 | `--json` 或默认模式 |

报告写入 `doctor-reports/`，格式为 `.json` 和 `.md`。

## 公开仓库安全规范

本仓库按“可公开”标准整理，推送前请确认：
- `.env` 不进入 git
- 运行期产物不进入 git（`archive/`、`doctor-reports/` 已在忽略列表中）
- 不提交真实 bot ID、token、chat ID 或私人路径
- 配置展示统一使用 `.env.example`

## CI

`.github/workflows/bootstrap.yml` 在每次推送时验证文档中的 bootstrap 流程：
1. 创建 `.venv`
2. 安装 `requirements.txt`
3. 复制 `.env.example` -> `.env`
4. `./project-tool --help`
5. `./project-tool doctor --bootstrap --json`
6. `python3 -m py_compile *.py`

## 已知前提

- D1 数据库需提前建表（DDL 见上方）
- ChromaDB 需支持 HTTP 访问
- 需要 Hermes CLI 才能使用投递检查功能
- 首次安装推荐先执行 `doctor --bootstrap --json`

## 文档检索协议

本仓库执行严格的文档检索协议：
- 优先从 D1 元数据检索
- 其次做 ChromaDB 语义检索
- 然后使用 `doc_summarize`
- 必要时才做目标文件检查
- 所有返回需带来源引用：`[来源: <title> (id:<id>)]`。

按检索规则来，禁止未检索就全文读入，禁止只给网页搜索结果。

## License

[MIT](./LICENSE)

## 贡献与安全

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [SECURITY.md](./SECURITY.md)
