# LangGraph ToolNode 原理与自定义 Tool Calling 对比

## 1. 背景

在这次 agent 架构收敛里，运行时已经统一到 `smart_eats` dedicated graph。现在真正承担工具调用主链路的，不再是过去那套自定义 `function calling / tool calling + ToolExecutor + 各种 legacy helper`，而是 LangGraph 的标准消息协议和 `ToolNode`。

从表面上看，会很容易产生一种感觉：

- 以前自己写的 tool calling 很死板
- LangGraph 的 `ToolNode` 很顺手
- 好像 `ToolNode` 更“智能”

但更准确的说法其实是：

> `ToolNode` 不是更智能，而是把“工具调用”这件事标准化了、协议化了、图化了。

它把原来你自己要手工维护的大量胶水逻辑，拆成了几个清晰的层：

1. **模型决策层**：模型决定要不要调工具、调哪个工具、参数是什么
2. **消息协议层**：用标准 `AIMessage.tool_calls` / `ToolMessage` 表达调用与结果
3. **执行层**：`ToolNode` 按统一协议执行工具
4. **编排层**：LangGraph 决定下一步是继续思考、继续调工具、还是结束
5. **业务映射层**：你自己的 `tool_postprocess` / `finalize` 把工具结果转成业务状态

也就是说，`ToolNode` 看起来“聪明”，本质上不是因为它替你思考了更多业务，而是因为它替你承担了更多**通用运行时基础设施**。

---

## 2. 先说结论

如果一句话总结两者差别：

> 你以前的方案，是“自己手写一整套工具调用运行时”；LangGraph ToolNode 的方案，是“把工具调用降级成标准消息流的一部分”。

再展开一点：

### 你以前的自定义方案更像是在做这些事

- 自己定义 action 协议
- 自己告诉模型输出什么 JSON / function 格式
- 自己解析模型返回
- 自己判断是不是要调工具
- 自己查找工具实现
- 自己做参数注入和归一化
- 自己执行工具
- 自己把结果塞回 state
- 自己决定下一步继续走哪个分支
- 自己处理异常、重试、fallback、终止条件
- 自己兼容 checkpoint / replay / streaming

### LangGraph ToolNode 方案更像是在做这些事

- 你只负责让模型产出标准 `tool_calls`
- `ToolNode` 负责把 `tool_calls` 执行成 `ToolMessage`
- `StateGraph` 负责控制节点流转
- 你只在少数节点里写业务逻辑：观察、规划、后处理、收尾

所以差距并不在“模型智商”，而在：

> **你之前把“业务逻辑 + 通用运行时逻辑”耦在一起了；LangGraph 把它们拆开了。**

---

## 3. ToolNode 在 LangGraph 里的真实定位

很多人第一次接触 `ToolNode`，会误以为它是“会自动决定怎么调用工具的智能节点”。

其实不是。

`ToolNode` 的职责很窄，也很明确：

> **读取上一条 AI 消息里的 tool_calls，找到对应工具，执行它们，再把结果包装成 ToolMessage 输出。**

它本质上是一个**标准化工具执行器**，而不是一个“代理大脑”。

真正决定“要不要调用工具”的，不是 `ToolNode`，而是：

- 上游的模型输出
- 条件路由（如 `tools_condition`）
- 你的 graph 编排逻辑

所以整个调用链应该拆开看：

1. **LLM 先决定**：我要不要调工具
2. **Graph 再判断**：如果有 tool calls，就路由到 `ToolNode`
3. **ToolNode 再执行**：把工具结果变成 `ToolMessage`
4. **下游节点再解释**：这些工具结果对当前业务状态意味着什么

这是一个非常重要的认知点。

---

## 4. ToolNode 的底层执行原理

下面按底层执行链路拆开讲。

## 4.1 工具先被注册成统一对象

在当前仓库里，`smart_eats` graph 会先把可用工具整理成 LangChain/LangGraph 能理解的工具对象，然后交给 `ToolNode`：

见 [smart_eats.py:1405-1414](../app/agent/agents/smart_eats.py#L1405-L1414)：

```python
tool_node = ToolNode(
    [
        *to_langchain_tools(
            allowlist=allowed_tools,
            runtime_context_factory=lambda: _SMART_EATS_TOOL_RUNTIME_CONTEXT.get(),
        ),
        _build_submit_final_answer_tool(),
    ],
    messages_key="messages",
)
```

这里发生了几件事：

1. `allowed_tools` 决定本轮 agent 允许用哪些工具
2. `to_langchain_tools(...)` 把内部工具注册表转成 LangChain Tool 对象
3. `runtime_context_factory` 提供运行时上下文注入能力
4. `_build_submit_final_answer_tool()` 把“提交最终答案”也包装成了一个工具
5. `ToolNode(..., messages_key="messages")` 告诉它从 state 的 `messages` 字段中读取工具调用请求

注意：

- `ToolNode` 并不知道你的业务是什么
- 它只知道“我有一组工具对象”和“我要从 messages 里读 tool calls”

这就是标准化的第一步：**工具不再是散落在各处的 if/else 函数，而是统一注册对象。**

---

## 4.2 LLM 先产出标准 tool_calls

在当前实现里，真正做决策的是 `think_node`，不是 `ToolNode`。

见 [smart_eats.py:1463-1495](../app/agent/agents/smart_eats.py#L1463-L1495)：

```python
decision = await planner.plan_tool_calls(system, user, available_tool_schemas)
```

这里的 planner 会返回类似两种结果之一：

1. **要调工具**：带 `tool_calls`
2. **不调工具**：直接给可终结内容

当模型决定要调工具时，代码会把返回值归一化成标准 `AIMessage.tool_calls`：

```python
output["messages"] = [AIMessage(content="", tool_calls=tool_calls)]
output["next_action"] = tools_condition(output, messages_key="messages")
```

见 [smart_eats.py:1491-1494](../app/agent/agents/smart_eats.py#L1491-L1494)。

这里非常关键：

- 模型输出先被归一化
- 归一化后的结构是 `AIMessage(tool_calls=[...])`
- LangGraph 不再关心你原始 LLM 返回到底是 OpenAI 风格、Qwen 风格，还是别的 provider 风格
- 一旦归一成标准消息对象，后面的执行流程就稳定了

这一步解决了过去自定义方案中最痛苦的一类问题：

> 模型返回结构漂移、provider 差异、字段名变形、id 缺失、参数格式不一致。

在当前代码里，这些差异在进入 `ToolNode` 之前就被吸收掉了。

---

## 4.3 `tools_condition` 只负责判断“要不要进 tools 节点”

很多人会把 `tools_condition` 也误会成“智能路由器”。

其实它的工作也很朴素：

> **检查最后一条 AIMessage 有没有 tool_calls。**

有，就走 tools；没有，就不走。

在当前 graph 里：

```python
output["next_action"] = tools_condition(output, messages_key="messages")
```

然后图里再根据 `next_action` 走条件边：

见 [smart_eats.py:1588-1591](../app/agent/agents/smart_eats.py#L1588-L1591)：

```python
graph.add_conditional_edges(
    "think",
    lambda state: "tools" if state.get("next_action") == "tools" else "finalize",
)
```

所以 `tools_condition` 不是在做复杂推理，它只是在做**协议判断**：

- last AI message 有 `tool_calls` → 去执行工具
- 否则 → 进入 finalize

也就是说：

> 智能决策是模型做的，稳定分流是图做的。

---

## 4.4 ToolNode 的执行核心：按名称找到工具并执行

当 graph 进入 `tools_node` 时，当前代码会先准备运行时上下文，然后调用 `ToolNode.ainvoke(...)`：

见 [smart_eats.py:1523-1533](../app/agent/agents/smart_eats.py#L1523-L1533)：

```python
runtime_payload = _build_official_runtime_context(
    chat_state,
    db=db,
    redis_client=redis_client,
    servers_path=settings.MCP_SERVERS_CONFIG_PATH,
)
token = _SMART_EATS_TOOL_RUNTIME_CONTEXT.set(runtime_payload)
try:
    tool_output = await tool_node.ainvoke({"messages": ai_messages})
finally:
    _SMART_EATS_TOOL_RUNTIME_CONTEXT.reset(token)
```

这里你可以把 `ToolNode` 想成下面这个通用流程：

1. 从 `messages` 中拿到最后一条 `AIMessage`
2. 读取其中的 `tool_calls`
3. 对每个 tool call：
   - 取出 `name`
   - 取出 `args`
   - 取出 `id`
4. 在注册工具表里按 `name` 找到对应工具
5. 把 `args` 传进去执行
6. 拿到返回值后包装成 `ToolMessage`
7. 把这些 `ToolMessage` 作为输出返回

你可以把它抽象成：

```text
AIMessage(tool_calls)
    -> ToolNode
    -> [执行 tool1, tool2, ...]
    -> ToolMessage(result1), ToolMessage(result2)
```

它不是一个“会思考工具策略”的模块，它是一个**严格执行标准调用协议的执行器**。

---

## 4.5 ToolNode 输出的不是业务状态，而是 ToolMessage

这是 `ToolNode` 和很多自定义执行器最本质的差别之一。

### 自定义执行器常见做法

过去自己写的时候，最自然的方式往往是：

- 执行工具
- 直接改 state
- 或直接决定下一步 action
- 或直接拼最终回答

这会导致工具执行器逐渐膨胀：

- 它既是执行器
- 又是状态写入器
- 又是流程控制器
- 又是部分业务决策器

### ToolNode 的做法

ToolNode 更克制：

> 它只返回 `ToolMessage`，不替你做业务决策。

在当前仓库中，`ToolNode` 的原始输出会先被规范化：

见 [smart_eats.py:1535-1542](../app/agent/agents/smart_eats.py#L1535-L1542)：

```python
tool_messages = _normalize_official_tool_messages(tool_output)
call_args_map = _collect_tool_call_args(ai_messages)

output = dict(state)
output.update(_state_to_dict(chat_state))
output["_tool_messages"] = tool_messages
output["_tool_call_args"] = call_args_map
output["next_action"] = "tool_postprocess"
```

然后真正把这些工具结果翻译成业务语义的，是后面的 `tool_postprocess_node`：

见 [smart_eats.py:1545-1558](../app/agent/agents/smart_eats.py#L1545-L1558)。

这说明了一件很重要的事：

> `ToolNode` 只负责“把工具跑出来”；`tool_postprocess` 才负责“这些结果在你业务里意味着什么”。

这就是解耦。

---

## 4.6 业务后处理节点才是真正的“智能业务层”

`tool_postprocess_node` 做的是你业务真正关心的事：

- 工具调用结果怎么变成 observation
- 是否需要刷新上下文
- 是否已经足够生成 final
- 是否还要继续下一轮 observe / think
- `submit_final_answer` 结果要怎么回写成 `final_json`

见 [smart_eats.py:1550-1565](../app/agent/agents/smart_eats.py#L1550-L1565)：

```python
await _apply_official_tool_postprocess(...)
_finalize_official_after_tools(chat_state, agent_config)
...
output["next_action"] = "final" if chat_state.final_json else "observe"
```

你会发现，这种设计把职责切得很清楚：

- **ToolNode**：工具执行
- **tool_postprocess**：结果吸收
- **finalize**：兜底终结
- **observe / think**：下一轮计划

这比“一个超级 ToolExecutor 干所有事”稳定得多。

---

## 4.7 finalize 是兜底，而不是工具执行器的一部分

当前实现里还有一个单独的 `finalize_node`：

见 [smart_eats.py:1568-1575](../app/agent/agents/smart_eats.py#L1568-L1575)。

```python
async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    chat_state = _state_from_dict(state)
    if not chat_state.final_json:
        chat_state.final_json = _fallback_final()
```

它的意义是：

- 最终收尾逻辑集中处理
- fallback 机制集中处理
- 不要把“结束语义”塞进 ToolNode 或执行器内部

这和自定义方案的差别也很大。

自定义方案里，执行器常常会长成这样：

- 工具没结果 → 在执行器里直接 fallback
- 参数异常 → 在执行器里直接终止
- 某工具成功 → 在执行器里直接拼最终回复

这会让执行器越来越像一个**隐式状态机**。

而在 LangGraph 方案里，状态机是显式画在图里的。

---

## 5. 当前仓库里的完整调用链

结合当前实现，整个 dedicated graph 的主链路可以概括为：

```text
observe
  -> think
    -> (有 tool_calls) tools
    -> tool_postprocess
    -> observe / finalize
  -> finalize
  -> END
```

对应代码见 [smart_eats.py:1577-1603](../app/agent/agents/smart_eats.py#L1577-L1603)。

而外层生产运行时 `run_chat_stream()` 只负责：

- 构建 graph
- 处理 checkpoint / resume / replay
- 产出 SSE 事件
- 持久化最终消息
- 记录 fallback / non_fallback 指标

见 [graph.py:104-285](../app/agent/graph.py#L104-L285)。

特别是这里：

```python
graph = build_smart_eats_graph(
    db=db,
    redis_client=redis_client,
    provider=provider,
).compile(checkpointer=checkpointer)
```

见 [graph.py:124-128](../app/agent/graph.py#L124-L128)。

以及最终指标记录：

```python
if _is_fallback_payload(final_json):
    record_agent_metric(session_id, "fallback_final")
else:
    record_agent_metric(session_id, "non_fallback_final")
```

见 [graph.py:240-244](../app/agent/graph.py#L240-L244)。

这说明现在系统已经被切成两层：

### 图内层
负责 agent 逻辑：

- 观察
- 思考
- 工具调用
- 结果吸收
- 终结

### 图外层
负责运行时基础设施：

- checkpoint
- resume/replay
- SSE streaming
- assistant message 落库
- preference extraction
- metrics

这也是为什么现在这套结构比以前顺手很多：

> 因为控制流终于被分层了。

---

## 6. 你之前自定义 function calling / tool calling 为什么会难用

这是最关键的问题。

根本原因不是你不会写，而是你以前在做的事情，本来就不是一个“小功能”。

你实际上是在手写一个迷你版 agent runtime。

下面按层拆开说。

## 6.1 你以前通常会把三件事耦在一起

### 第一件：模型协议
你要规定模型输出什么格式：

- JSON
- action + args
- function name + parameters
- 某种自定义 DSL

### 第二件：执行协议
你要规定执行器怎么理解这些格式：

- 怎么判断是否真要调用工具
- 怎么找工具
- 怎么传参数
- 怎么处理异常

### 第三件：状态机协议
你还要决定：

- 工具执行完之后 state 怎么更新
- 下一轮要不要继续让模型思考
- 什么时候结束
- 什么时候 fallback
- 什么时候澄清用户问题

这三件事本来就不该揉成一团。

但手写方案里它们很容易被揉在一起，所以系统会越来越难改。

---

## 6.2 自定义协议往往很脆弱

如果你是自己定义输出格式，常见问题会很多：

- 模型有时少字段
- 参数 key 拼错
- 某些轮次返回文本而不是 JSON
- 工具 id 丢失
- 多工具调用格式不统一
- provider 切换后结构漂移
- prompt 一改，输出协议就变形

于是你会不断补这些代码：

- 容错 parser
- 正则兜底
- 缺省字段修补
- “如果不是合法 JSON 就当普通文本”
- “如果 action 不认识就直接 final”

这些补丁加多了之后，系统就会给人一种很强的“死板感”：

> 不是因为它过于严格，而是因为它过于脆弱，只能靠很多硬编码兜住。

---

## 6.3 旧方案的控制流通常是隐式的

很多自定义 tool calling 最容易失控的地方，不是工具本身，而是**工具调用后的下一步**。

例如：

- 工具成功了，下一步要不要继续推理？
- 工具返回空结果，要不要换个工具？
- 工具返回部分结果，要不要直接生成 final？
- 某些工具成功后必须调用另一个工具，这个约束放哪？
- 用户中断 / 恢复时，当前阶段怎么接上？

如果没有显式 graph，这些逻辑通常会散落在：

- executor 里
- service 层里
- helper 里
- agent 主循环里
- prompt 里

于是系统虽然还能跑，但你自己会越来越难回答一个问题：

> “当前这一轮到底处于哪个状态？”

而 LangGraph 把这个问题变得非常清楚：

- 在 `observe`
- 在 `think`
- 在 `tools`
- 在 `tool_postprocess`
- 在 `finalize`

图就是状态机。

---

## 6.4 旧方案会把“工具执行”做成“业务决策中心”

这是很多自定义 executor 越写越胖的核心原因。

一开始你可能只是想做：

- 执行某个工具
- 把结果返回给模型

但很快就会扩张成：

- 执行前做参数补全
- 执行后写 observation
- 判定是否触发 fallback
- 判定是否结束
- 判定是否触发澄清
- 给不同工具套不同后处理逻辑

最后 executor 就会变成一个巨大的“总控器”。

这类设计最麻烦的地方是：

- 新增一个工具时，常常要改 4~6 个位置
- 换一个模型时，又要改解析器
- 想做 checkpoint/resume 时，发现执行器里埋了太多隐式状态
- 想测试单个环节时，发现所有逻辑都缠在一起

于是它自然会显得难用、死板、不可组合。

---

## 6.5 旧方案对“多工具、多轮、恢复”尤其不友好

如果只是“一问一调一答”，自己写 function calling 其实不算特别难。

真正开始痛苦，是当需求变成下面这样：

- 一轮里可能调多个工具
- 工具结果要累积到 context
- 下一轮推理要读上一轮 observation
- 中途可能 pause / resume
- 最终答案可能来自工具，也可能来自 fallback
- streaming 过程中还要持续产出事件

这时你写的就不再是一个“函数调用封装”，而是一个**会话状态机**。

LangGraph 的价值，恰恰就是把这件事承认了：

> agent 不是普通函数；agent 是带状态、可恢复、可分支的图执行过程。

一旦你接受这一点，很多之前乱成一团的问题就自然有了更合适的归宿。

---

## 7. 为什么 LangGraph ToolNode 会显得“更智能”

这部分最容易被误判。

严格说，`ToolNode` 本身并不智能。

但它会**制造出一种很智能、很丝滑的体感**。原因主要有下面几条。

## 7.1 因为协议更贴近模型训练分布

现在主流大模型本来就被大量训练在这些结构上：

- function calling
- tool calling
- structured tool schema
- assistant message / tool message 对话回合

也就是说，模型天然更习惯输出这种结构化调用，而不是你自己发明的一套 action DSL。

所以不是 `ToolNode` 更聪明，而是：

> **它和模型熟悉的调用协议更一致。**

这会直接带来两个好处：

1. 模型更容易稳定地产出调用格式
2. 你需要写的 prompt 约束更少

你过去如果要求模型输出自定义 JSON/action 协议，实际上是在让它偏离最常见的训练接口。

当然会更难驯。

---

## 7.2 因为消息流是显式的，不靠脑补

在 LangGraph 里，消息流是明确的：

- `AIMessage(tool_calls=...)`
- `ToolMessage(...)`
- 后处理节点吸收结果
- 再进入下一轮

这意味着：

- 你可以清楚知道模型发起了什么调用
- 你可以清楚知道工具返回了什么
- 你可以清楚知道这些结果在哪一步被吸收

而在旧方案里，很多信息会混在各种 dict/state 字段里，甚至边执行边覆盖。

当系统可观察性更好时，人就会觉得它更“聪明”。

其实很多时候那不是聪明，而是：

> 它终于不黑箱了。

---

## 7.3 因为控制流显式，行为更稳定

LangGraph 的 graph 把控制流画出来了，所以：

- 什么时候思考
- 什么时候调工具
- 什么时候后处理
- 什么时候结束

都不是隐式约定，而是显式节点和边。

因此当你新增功能时，通常只需要回答：

- 这是新工具？
- 这是新后处理规则？
- 这是新路由条件？
- 这是新终结条件？

问题会被局部化。

而在旧方案里，新增一个需求常常会波及：

- prompt
- parser
- executor
- state update
- finalizer
- streaming 输出

这种“改一点动全身”的体验，正是死板感的来源之一。

---

## 7.4 因为框架替你承担了大量边界处理

过去你要自己处理：

- 工具调用消息格式
- 多工具调用顺序
- tool id 对齐
- tool result message 包装
- 图执行状态延续
- checkpoint 对接
- resume/replay 恢复

而现在这些通用基础设施，要么由 LangGraph 提供，要么很自然地挂在图外围。

例如当前仓库里：

- 图内部专注 agent 逻辑
- 图外部的 [graph.py](../app/agent/graph.py) 专注 checkpoint、流式输出、最终落库、metrics

这种结构一旦建立起来，你新增能力会轻松很多。

人会把这种“可扩展、可预测、少出怪问题”的体验感受成“智能”。

但本质上它是**工程分层更合理**。

---

## 8. 两种方案的逐项对比

| 维度 | 自定义 function/tool calling | LangGraph ToolNode |
|---|---|---|
| 工具调用协议 | 自己定义，容易漂移 | 标准 `tool_calls` / `ToolMessage` |
| 模型输出稳定性 | 依赖 prompt 驯化，容易变形 | 更贴近主流模型原生调用格式 |
| 工具执行 | 自己写 executor | `ToolNode` 统一执行 |
| 结果表达 | 常直接改 state / 拼结果 | 先变成 `ToolMessage`，再后处理 |
| 控制流 | 常是隐式 if/else | 显式 graph 节点与边 |
| 多轮工具调用 | 容易缠在一起 | 天然适合 graph 循环 |
| 可测试性 | executor 往往很胖 | 节点职责清晰，更容易拆测 |
| 可恢复性 | 需要自己设计 | 更适合接 checkpoint / resume |
| 可观察性 | 日志常散乱 | 消息流与节点流更清晰 |
| 业务扩展 | 经常改全链路 | 通常只改局部节点 |

这张表里最重要的一行其实是：

> **自定义方案“直接改 state”，ToolNode 方案“先产出标准消息，再做后处理”。**

这是两套设计哲学。

前者偏命令式；后者偏消息驱动。

---

## 9. 为什么你以前的实现会显得“死板”

如果把“死板”翻译成工程语言，大概就是下面几种症状：

## 9.1 每加一个工具都像在改框架

如果系统没有把工具调用抽象成标准层，那么每加一个工具通常要改：

- prompt
- schema
- parser
- executor dispatch
- state update
- downstream handling

于是“加一个业务工具”这件事，变成了“再改一次底层框架”。

当然会难用。

---

## 9.2 每换一个模型都像重新适配协议

如果你依赖的是自定义 action 格式，而不是模型天然支持的 tool calling 格式，那么 provider 一变，问题就会很多：

- 字段名不同
- 参数结构不同
- 返回层级不同
- 是否严格遵守 JSON 不同
- 是否支持多工具调用不同

你会不断在适配器、parser、prompt 上补胶水。

这不是业务复杂，而是协议没站在标准面上。

---

## 9.3 很多逻辑只能靠 prompt 硬拽

自定义方案常见的做法是：

- 提示模型“先调 A，再调 B”
- 提示模型“如果失败就直接返回某种格式”
- 提示模型“不要输出别的文本，只输出 JSON”

这类方案的致命问题是：

> 你把本该由运行时保证的约束，交给了模型自觉遵守。

模型不是状态机，不会稳定承担这一层职责。

而 LangGraph 的做法是：

- 模型只负责决策
- 图负责控制流
- ToolNode 负责执行协议

这样 prompt 压力自然会小很多。

---

## 9.4 系统越大，隐式约定越成为负担

小系统里，自定义 executor 也许还能靠“大家心里知道怎么回事”维持。

但系统一变大，就会开始出现这些问题：

- 新人看不懂控制流
- 某个工具结果在哪里被消费看不出来
- fallback 为什么触发说不清
- checkpoint 恢复从哪一层接不清楚
- 测试写着写着就全是 monkeypatch

这就是典型的：

> 逻辑不是不能跑，而是**不再容易被理解和维护**。

LangGraph 的价值，恰恰就在于把这些隐式约定外显化。

---

## 10. 为什么现在这套 dedicated graph 更顺手

结合当前仓库，可以非常具体地说明为什么现在顺手。

## 10.1 `think_node` 只负责决策，不直接执行

见 [smart_eats.py:1442-1512](../app/agent/agents/smart_eats.py#L1442-L1512)。

它只做：

- 读取上下文
- 调 planner
- 决定是否产生 `tool_calls`
- 如果不调工具，则产出 final

这说明“思考”与“执行”已经拆开。

---

## 10.2 `tools_node` 只负责调用 ToolNode

见 [smart_eats.py:1514-1543](../app/agent/agents/smart_eats.py#L1514-L1543)。

它只做：

- 注入运行时上下文
- 调 `tool_node.ainvoke(...)`
- 收集 ToolMessage
- 把结果交给下一节点

它没有直接决定最终业务答案。

---

## 10.3 `tool_postprocess_node` 才负责业务吸收

见 [smart_eats.py:1545-1566](../app/agent/agents/smart_eats.py#L1545-L1566)。

这意味着如果以后要改业务策略，例如：

- 某类工具结果如何写 observation
- 某个工具空结果时是否继续扩圈搜索
- `submit_final_answer` 怎样回写

你只需要改后处理逻辑，不需要动 ToolNode 本身。

这就是所谓的“松耦合”。

---

## 10.4 `run_chat_stream()` 不再承担 agent 业务分发

过去 legacy 体系里，graph/runtime 分发、registry bridge、helper 兼容层都混在一起。

现在 [graph.py:104-285](../app/agent/graph.py#L104-L285) 更像一个干净的 runtime 壳：

- 编译 graph
- 处理中断恢复
- 发 SSE
- 记录 metrics
- 落 assistant 消息

这是非常关键的收敛。

因为它意味着：

> “agent 如何思考” 和 “系统如何运行” 终于被拆开了。

---

## 11. 一个最值得记住的抽象：ToolNode 不是大脑，是插座

如果一定要找一个最容易记住的比喻：

- **模型**是大脑，决定要不要调用工具
- **ToolNode** 是标准插座，负责把调用接出去
- **tool_postprocess** 是业务翻译器，负责把工具结果变成领域状态
- **graph** 是总线路图，决定电流怎么流

所以 `ToolNode` 的价值不在于“替你思考”，而在于：

> 它让工具调用从“手搓飞线”变成“标准插座接线”。

你过去难受，不是因为你不会接工具，而是因为你一直在自己焊整套电路板。

---

## 12. 对后续设计的启发

如果后面还继续扩 agent 能力，比较推荐继续坚持下面这些原则。

### 12.1 不要再让工具执行器直接写业务最终状态

让工具执行层只做：

- 执行
- 返回标准结果

业务状态更新放在独立节点做。

### 12.2 不要把控制流写回 prompt 里

像“先调 A，再调 B，失败就如何如何”这种约束，能放在图里，就别主要靠 prompt 保证。

### 12.3 不要让 runtime 壳层重新膨胀

`run_chat_stream()` 这类入口应继续只做运行时基础设施，不再塞回 legacy 风格的 agent 分发逻辑。

### 12.4 业务特殊性应放在节点或后处理里

不要污染通用执行层。

这样新工具、新状态、新策略的成本都会更低。

---

## 13. 最终总结

### 一句话总结 ToolNode 的底层原理

> `ToolNode` 读取标准 `AIMessage.tool_calls`，按名称找到注册工具执行，并把结果包装成 `ToolMessage` 返回给图的下一步节点。

### 一句话总结它和你之前自定义 tool calling 的本质差别

> 你之前是在手写整套工具调用运行时；`ToolNode` 是把工具执行收敛成一个标准消息节点，再由 graph 负责状态流转。

### 一句话总结为什么以前难用、现在顺手

> 以前难用，是因为模型协议、执行协议、状态机协议全耦在一起；现在顺手，是因为 LangGraph 把这些层拆开了，各层职责清楚了。

### 一句话总结为什么它看起来更“智能”

> 不是它更智能，而是它更标准、更稳定、更可组合，所以系统整体表现得更像一个成熟 agent，而不是一堆胶水逻辑。

---

## 14. 对当前仓库最贴切的落地结论

对这个仓库来说，现在最有价值的不是“继续发明新的工具调用抽象”，而是：

1. 继续以 `StateGraph + ToolNode + tool_postprocess + finalize` 为主结构
2. 让 `smart_eats` 成为唯一事实来源
3. 把业务差异留在节点和后处理层
4. 把通用运行时能力留在 graph 外层壳中

这样后面再加能力时，系统仍然会保持现在这种清晰度。

这比回到过去那种：

- 自定义 executor
- 自定义 action DSL
- 自定义多层 helper
- 自定义 runtime 分发

要健康得多。