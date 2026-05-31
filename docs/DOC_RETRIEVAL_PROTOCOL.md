---
name: doc-retriever
description: MANDATORY retrieval skill when the user asks about ANY data related to business, finance, or documents managed by /opt/hermes/project. Prevents LLM hallucinations by enforcing local database queries first.
---

# Document Retriever (Search-First Protocol)

This skill ensures Hermes NEVER guesses or answers from general knowledge when local business data is available.

## Trigger
User asks about a specific company, contract, report, or project.
User asks for data points (e.g., "What was the total in invoice X?", "Who signed contract Y?").
User asks a general question that *could* be answered by local documents.
Any question that might reference project documents, invoices, contracts, or archived work.

## Mandatory Procedure
ALL steps are MANDATORY. Do NOT stop after Step 1 unless the answer is fully resolved. If uncertain whether the result is complete, escalate to the next step.

### Step 1. Intent Check (ARE THERE LOCAL DOCUMENTS?)
Before doing anything else, determine if this question is about documents managed by /opt/hermes/project. If **ANY** of the following are true, the answer MUST come from the local database:
- Question references a company, project, contract, invoice, report, or technician name
- Question uses words like "那份", "这份", "方案", "报价", "合同", "发票", "上次", "上个月"
- User is asking about a file that already exists in `/opt/hermes/project/archive/documents/`

If triggered: Proceed to Step 2. **Never answer without searching first.**

### Step 2. Search Escalation (Try each level in order)
Execute following in sequence until a satisfactory result is found.

**First: Metadata Query (D1)**
Run SQLite/Cloudflare D1 query on the `documents` table against title, company, file_path.
- Target matches: `/opt/hermes/project/documents` table; columns `id, title, company, summary, file_path, created_at`.
- If hit, note the `id` and continue to Step 3.

**Second: Semantic Retrieval (ChromaDB)**
Query ChromaDB using the user's natural language query with `n_results=3` to get top semantic chunks.
- Collection name: `documents`
- Use: `col.query(query_texts=[...], n_results=3)`
- Do NOT pull the full document yet.

**Third: Summary Anchor (doc_summarize)**
If metadata did not provide sufficient context, run doc_summarize on the candidate file(s) to obtain a structured summary anchor (title, summary, md5, status).
- Command: `/opt/hermes/project/project-tool summarize <file_path>`
- Maximum 500 characters from this output should be passed into context.

**Fourth: (Only if still unresolved) Full file inspection**
Only when previous steps are insufficient should you open the original file for targeted section reading (never load the entire file blindly).

### Step 3. Synthesis & Citation
When composing the answer:
- ALWAYS cite the source document: `[来源: <title> (id:<id>)]`
- If the evidence is partial, say so explicitly: "已检索文档，但相关章节未覆盖此问题" rather than guessing.
- If D1 + ChromaDB both returned empty: Respond with exactly: `[检索报告：本地库未发现匹配项]`
- Do not mix generic knowledge with local data. If the data exists locally, use local data only.

## Core Rule
**DO NOT HALLUCINATE.** If the tool returns nothing, the data does not exist in the local project.

## Forbidden Actions
- Read full files before running at least one search query
- Answer document questions without citing a source
- Use web search as the first response to any question about project documents
