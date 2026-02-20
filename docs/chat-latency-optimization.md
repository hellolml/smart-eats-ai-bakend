# Smart-Eats 对话链路性能优化方案（已落地版，含架构下沉）

## 1. 背景与目标

当前对话链路为 **Planner（决策）+ Writer（生成）+ Tool Loop（可选）** 多阶段架构，主要痛点：

- 首字返回慢（TTFB 高）
- 工具链路串行导致总时延偏高
- 简单闲聊请求链路过长
- 框架层与业务层曾存在耦合（fast path 业务逻辑在 `graph.py`）

本轮优化目标：

1. 降低 TTFB 与总耗时
2. 保留工具能力与可回滚能力
3. 将业务逻辑从框架层下沉到业务 Agent
4. 保持 SSE 语义稳定（`delta/final`、`thinking`、`stop`）

---

## 2. 当前架构（优化后）

### 2.1 框架层（通用）

- 流编排、checkpoint、取消控制、SSE 发送： [app/agent/graph.py](app/agent/graph.py)
- LLM 客户端复用与流式适配： [app/agent/llm_adapters.py](app/agent/llm_adapters.py)
- 记忆缓存策略： [app/agent/memory.py](app/agent/memory.py)

### 2.2 业务层（Smart-Eats）

- fast path 判定与 prompt 构建下沉到： [app/agent/agents/smart_eats.py](app/agent/agents/smart_eats.py)
- 通过 AgentConfig 注入框架： [app/agent/agent_registry.py](app/agent/agent_registry.py)

---

## 3. 已实施优化总览

| 优先级 | 优化项 | 状态 | 主要收益 |
|---|---|---|---|
| P0 | LLM 客户端连接池（单例复用） | ✅ | 降低重复建连（TCP/TLS） |
| P0 | observe 阶段并行 IO（history + memory） | ✅ | 降低串行等待 |
| P0 | simple chat fast path | ✅ | 闲聊链路更短 |
| P1 | Planner 流式收集 | ✅ | 更早建立上游流连接 |
| P1 | thinking 事件 | ✅ | 前端可立即反馈“思考中” |
| P2 | memory Redis 缓存 | ✅ | 降低 DB 压力与时延 |
| P2 | 移除 4 字符二次切块 | ✅ | 降低 SSE 事件与调度开销 |
| 架构治理 | fast path 业务逻辑下沉到业务 Agent | ✅ | 框架层职责更纯，扩展更清晰 |

---

## 4. 详细改动说明

### 4.1 LLM 客户端连接池（单例复用）

**位置**：

- 连接池定义与获取：[app/agent/llm_adapters.py#L110-L133](app/agent/llm_adapters.py#L110-L133)
- Planner/Writer 使用共享客户端：
  - [app/agent/llm_adapters.py#L136-L140](app/agent/llm_adapters.py#L136-L140)
  - [app/agent/llm_adapters.py#L216-L220](app/agent/llm_adapters.py#L216-L220)

**收益**：避免每次请求重复初始化 `AsyncOpenAI` 与连接握手。

---

### 4.2 observe 阶段并行化

**位置**：

- [app/agent/graph.py#L120-L129](app/agent/graph.py#L120-L129)

**核心**：`history.load_history(...)` 与 `memory.search_memories(...)` 并行执行（`asyncio.gather`）。

---

### 4.3 fast path 从框架层下沉到业务层（本次关键）

#### A) AgentConfig 扩展 fast path 回调

**位置**：

- 类型定义：[app/agent/agent_registry.py#L23-L25](app/agent/agent_registry.py#L23-L25)
- 字段定义：[app/agent/agent_registry.py#L45-L47](app/agent/agent_registry.py#L45-L47)
- 工厂参数与注入：
  - [app/agent/agent_registry.py#L68-L70](app/agent/agent_registry.py#L68-L70)
  - [app/agent/agent_registry.py#L88-L90](app/agent/agent_registry.py#L88-L90)

新增回调：

- `fast_path_decider`
- `fast_path_system_prompt_builder`
- `fast_path_writer_prompt_builder`

#### B) graph.py 改为“只编排，不承载业务规则”

**位置**：

- fast path 主流程（改为调用 `agent_config.fast_path_*`）：[app/agent/graph.py#L847-L907](app/agent/graph.py#L847-L907)

**说明**：

- `graph.py` 不再包含 Smart-Eats 关键词判定与业务 prompt 组装逻辑
- 继续复用通用流程：session 初始化、context 刷新、cancel 检查、SSE 输出、历史写入

#### C) Smart-Eats 承接业务策略

**位置**：

- 关键词与判定/构建函数：
  - [app/agent/agents/smart_eats.py#L68-L119](app/agent/agents/smart_eats.py#L68-L119)
- 在 AgentConfig 注册：
  - [app/agent/agents/smart_eats.py#L318-L339](app/agent/agents/smart_eats.py#L318-L339)

---

### 4.4 Planner 流式收集

**位置**：

- Planner 入口调用：[app/agent/llm_adapters.py#L171-L179](app/agent/llm_adapters.py#L171-L179)
- 流式收集实现：[app/agent/llm_adapters.py#L198-L213](app/agent/llm_adapters.py#L198-L213)

---

### 4.5 thinking 事件（体感优化）

**位置**：

- `thinking start`：[app/agent/graph.py#L991-L993](app/agent/graph.py#L991-L993)
- `thinking done`：[app/agent/graph.py#L1029-L1031](app/agent/graph.py#L1029-L1031)

---

### 4.6 memory Redis 缓存

**位置**：

- 缓存配置与 key：[app/agent/memory.py#L16-L22](app/agent/memory.py#L16-L22)
- 读缓存优先：[app/agent/memory.py#L60-L67](app/agent/memory.py#L60-L67)
- 回源 DB 最近 N 条：[app/agent/memory.py#L70-L76](app/agent/memory.py#L70-L76)
- 回写缓存：[app/agent/memory.py#L82-L88](app/agent/memory.py#L82-L88)

---

### 4.7 移除 4 字符二次切块

**位置**：

- Writer 输出直接透传 delta：[app/agent/graph.py#L1053-L1055](app/agent/graph.py#L1053-L1055)
- fast path 同样透传：[app/agent/graph.py#L891-L893](app/agent/graph.py#L891-L893)

> 备注：`_iter_delta_chunks` 仍在文件中但已不再被调用，可在后续清理版本中移除。

---

## 5. 配置现状

代码默认值见 [app/common/config.py#L60-L64](app/common/config.py#L60-L64)：

- `QWEN_MODEL_PLANNER = "deepseek-v3.2"`
- `QWEN_MODEL_WRITER = "qwen3.5-plus"`
- `AGENT_MAX_STEPS = 6`

示例建议值见 [.env.example#L295-L317](.env.example#L295-L317)：

- `QWEN_MODEL_PLANNER=qwen-turbo`
- `AGENT_MAX_STEPS=3`

实际以运行环境变量为准。

---

## 6. 测试与验证现状

已补充 fast path 业务函数单测：

- [app/tests/test_smart_eats_tool_result_handler.py#L159-L219](app/tests/test_smart_eats_tool_result_handler.py#L159-L219)

覆盖点：

- simple chat 命中
- 工具关键词拒绝
- `context_overrides` 拒绝
- checkpoint 恢复拒绝
- fast path prompt 构建

建议持续验收指标：

1. TTFB（首个 SSE 事件）
2. 首 token 时延（首个 `delta`）
3. 总耗时（到 `final`）
4. observe 阶段耗时
5. 缓存命中率（history/memory）

---

## 7. 回滚策略（建议按层）

1. 回滚业务下沉：恢复 `graph.py` 内联判定（不推荐长期）
2. 回滚 fast path：禁用 `agent_config.fast_path_decider`
3. 回滚 observe 并行：恢复串行读取
4. 回滚 Planner 流式：恢复原调用方式
5. 回滚 memory 缓存：仅走 DB

---

## 8. 总结

本轮优化已从“性能优化”升级为“性能 + 架构治理”双目标：

- 性能侧：减少关键路径、并行 IO、连接复用、减少事件碎片
- 架构侧：将 fast path 业务策略从框架层下沉到业务 Agent，通过 AgentConfig 标准扩展点接入

最终结果是：**更快、可回滚、可扩展、职责更清晰**。