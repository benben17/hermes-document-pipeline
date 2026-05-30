# Hermes Document Pipeline

面向生产环境的文档与发票处理流水线：统一 CLI、健康检查、结构化入库与语义检索。

[English Version](./README.md)

## 这个项目是做什么的

Hermes Agent 很适合做编排，但业务流程需要独立、稳定、可交接的运行时和工具层。这个仓库把这层能力整理成一个小型 Python 项目，并提供统一入口 `./project-tool`。

目标是让一个新用户可以：

1. clone 仓库，
2. 创建 venv，
3. 填好 `.env`，
4. 跑几条命令，
5. 就能完成安装与基础验证。

## 核心功能

- **发票入库**
  - 接收 JSON 载荷
  - Upsert 到 Cloudflare D1
  - 把发票文本同步到 ChromaDB
- **文档处理**
  - 支持 PDF / DOCX / TXT / MD / LOG / CSV / XLSX / XLS
  - 本地归档规范化副本
  - 文档元数据写入 D1
  - 提取文本写入 ChromaDB
- **运维健康检查**
  - 校验 Python 运行时、依赖、D1、ChromaDB 与 CLI 入口
  - 导出 JSON / Markdown 报告
  - 用 `doctor --fix` 修复常见漂移

## 项目亮点

- **Tool-First**：核心能力沉淀为脚本和 CLI，而不是散落在聊天操作里。
- **混合存储**：D1 管结构化数据，ChromaDB 管语义文本检索。
- **强可观测性**：内置 `doctor` 检查与报告产物。
- **开源友好上手**：补充了 `examples/` 示例载荷和 GitHub Actions bootstrap 工作流，保证 clone 后首轮验证可复现。
- **适合公开仓库**：敏感配置与投递目标改成环境变量驱动，不再写死在源码里。
- **上手快**：本地 `./project-tool` 在 venv 准备后即可直接运行。

## 快速开始

### 1）克隆仓库

```bash
git clone <你的仓库地址>
cd project
```

### 2）创建 Python 环境

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 3）配置环境变量

```bash
cp .env.example .env
```

至少填写：

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_FINANCE_D1_DATABASE_ID`
- `CHROMA_HOST`
- `CHROMA_PORT`
- 如果你要用 Hermes 做探针，再填写 `HERMES_NEWS_TARGET`

### 4）确认 CLI 正常

```bash
./project-tool --help
```

### 5）执行安全的 bootstrap 检查

```bash
./project-tool doctor --bootstrap --json
```

这是最推荐的新环境首次无副作用验证方式。它只校验本地 Python 运行时、依赖、入口脚本和 CLI 是否正常，不要求 D1、ChromaDB、cron wrapper、Hermes 投递链路已经配置完。

### 6）执行完整集成检查（可选）

```bash
./project-tool doctor --no-qq-send
```

当你已经配置好 D1、ChromaDB，以及（可选）Hermes 投递后，再执行这一项。

## 最短安装命令

如果别人 clone 后只想最快装起来，可以直接按下面做：

```bash
git clone <你的仓库地址>
cd project
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
./project-tool doctor --bootstrap --json
```

## CLI 用法

```bash
./project-tool invoice payload.json
./project-tool doc payload.json
./project-tool report invoices
./project-tool doctor --bootstrap --json
./project-tool doctor --json
./project-tool doctor --fix
```

也支持从 stdin 管道输入 JSON：

```bash
cat payload.json | ./project-tool invoice -
cat payload.json | ./project-tool doc -
```

## 输入约定

### invoice 命令

常见 JSON 字段：

- `invoice_number`
- `invoice_date`
- `buyer_name`
- `seller_name`
- `item_name`
- `amount_net`
- `tax_amount`
- `total_amount`
- `file_path`
- 可选：`raw_text`

### doc 命令

常见 JSON 字段：

- `title`
- `company`
- `category`
- `summary`
- `tags`
- `file_path`
- 可选：`raw_text`

## 架构概览

```text
Operator / CLI / Hermes
        │
        ▼
   ./project-tool
        │
        ├── project_manager.py  -> 文档 / 发票处理
        └── project_doctor.py   -> 健康检查 / 报告输出
                │
                ├── Cloudflare D1   （结构化数据）
                ├── ChromaDB        （语义检索）
                └── 本地 archive     （文档 / 报告 / 输出）
```

## 仓库结构

```text
.
├── .github/
│   └── workflows/
│       └── bootstrap.yml
├── .env.example
├── .gitignore
├── README.md
├── README.zh-CN.md
├── examples/
│   ├── README.md
│   ├── invoice.sample.json
│   ├── document.sample.json
│   └── sample_document.txt
├── project-tool
├── requirements.txt
├── hermes_core.py
├── core_engine.py
├── project_manager.py
├── project_doctor.py
├── invoice_engine.py
├── doc_engine.py
├── pdf_engine.py
└── project_manager.py
```

## 配置加载顺序

配置按以下顺序加载：

1. 进程环境变量
2. 项目本地 `.env`
3. `$HERMES_HOME/.env`
4. 内置默认值

关键变量包括：

- `PROJECT_ROOT`
- `HERMES_HOME`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_FINANCE_D1_DATABASE_ID`
- `CHROMA_HOST`
- `CHROMA_PORT`
- `HERMES_NEWS_TARGET`
- `HERMES_PROJECT_REPORT_DIR`
- `HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR`
- `HERMES_CRON_JOBS_FILE`
- `HERMES_PROJECT_TOOL_PATH`
- `FIRECRAWL_API_KEY`

## `doctor` 会检查什么

- 项目 `.venv`
- 必需 Python 依赖是否可 import
- 入口脚本是否正常
- D1 连通性与 schema
- ChromaDB 连通性与集合
- Firecrawl key / 搜索探针
- Hermes 投递目标可用性

报告会写到：

```text
doctor-reports/
```

## GitHub 公开规范

这个仓库现在是按“可公开”方向整理的。

公开前仍建议逐项确认：

- `.env` 不进入 git
- 运行期产物不进入 git
- 不提交真实 bot ID、token、chat ID、私人路径
- 文档统一用 `.env.example` 展示配置

当前仓库已忽略：

- `.venv/`
- `venv/`
- `archive/`
- `doctor-reports/`
- 生成的 markdown 输出
- 本地 env 文件

## 示例与 CI

- `examples/invoice.sample.json` 提供 `./project-tool invoice` 的公开安全示例载荷
- `examples/document.sample.json` + `examples/sample_document.txt` 提供 `./project-tool doc` 的文档 smoke test 路径
- `.github/workflows/bootstrap.yml` 会在 GitHub Actions 中验证文档里写的 bootstrap 流程：
  - 创建 `.venv`
  - 安装 `requirements.txt`
  - 复制 `.env.example` 为 `.env`
  - 执行 `./project-tool --help`
  - 执行 `./project-tool doctor --bootstrap --json`
  - 执行 `python -m py_compile *.py`

## 已知前提

- 当前业务逻辑默认 D1 中存在 `invoices` 和 `documents` 表
- ChromaDB 需支持 HTTP 访问
- 如果要使用投递检查或资讯推送，需要安装 Hermes CLI
- 首次安装验证最推荐 `doctor --bootstrap --json`
- 配好 D1 / ChromaDB / Hermes 后，再执行 `doctor --no-qq-send` 做完整集成验证

## 建议继续完善

- 增加 sample fixture 与自动化测试
- 增加 Docker / Compose 一键拉起方案
- 增加 schema migration 支持
- 如果要保留发布/架构分析资料，建议统一迁移到 `docs/` 目录下

## License

当前仓库已使用 [MIT](./LICENSE) 许可证。

## 贡献与安全

另见：

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [SECURITY.md](./SECURITY.md)
