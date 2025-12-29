# Plan: 整合 Google ADK 到 AgentOccam

結合 Google Agent Development Kit 的 toolkit、memory、agent orchestration 能力，擴展現有的 LLM provider 架構，並保持與現有 Actor-Critic-Judge 模式的相容性。

## Background & Architecture Analysis

### Current LLM Integration Architecture

**Provider Integration Pattern:**

- 模組化、provider-agnostic 架構，每個 provider 有三個核心函數：
  - `call_<provider>()` - 簡單文本提示介面
  - `call_<provider>_with_messages()` - 結構化訊息介面（主要方法）
  - `arrange_message_for_<provider>()` - 將內部訊息格式轉換為 provider 特定格式

**支援的 Providers:**

- Claude, GPT, Gemini, Mistral, Cohere, Llama, Titan

**整合機制:**

- 在 `AgentOccam.py` 中有三個字典映射 provider 名稱到函數
- Base `Agent` 類別自動從 model ID 檢測 model family
- 使用 `functools.partial` 設定適當的函數

### Agent Architecture

**類別層次:**

1. `Agent` (基礎類別) - 管理模型選擇和訊息格式化
2. `Actor` (主要 agent) - 管理 planning tree、處理 DOM 最佳化
3. `Critic` - 評估 actor 性能、提供反饋
4. `Judge` - 從多個候選動作中選擇最佳動作
5. `AgentOccam` (orchestrator) - 協調 Actor、Critic 和 Judge

**LLM Call Flow:**

```
AgentOccam.predict_action()
  → Critic.get_criticism_elements()  [LLM call]
  → Actor.predict_action()           [LLM call(s)]
  → Judge.judge()                     [LLM call]
```

### Message Format

**內部訊息格式:**

```python
[
  ("text", "string content"),
  ("image", image_data_or_path),
  ("text", "more content")
]
```

**LLM Response Parsing:**

- 使用 regex 提取結構化輸出 (例如 `REASON:`, `ACTION:`)
- 方法: `parse_elements()`
- 針對當前觀察驗證動作

## Integration Steps

### Step 1: 在 AgentOccam/llms/ 新增 ADK provider 模組

**Files to create:**

- `AgentOccam/llms/adk.py`

**Implementation details:**

- 實作 `call_adk()`, `call_adk_with_messages()`, `arrange_message_for_adk()`
- 初始化 ADK runtime 和 client
- 處理 ADK 的結構化回應並轉換為內部格式
- 支援 function calling 與 tool use

**Modifications needed:**

- 在 `AgentOccam/AgentOccam.py` (Lines 27-51) 註冊到：
  - `MODEL_FAMILIES` 列表
  - `CALL_MODEL_MAP`
  - `CALL_MODEL_WITH_MESSAGES_FUNCTION_MAP`
  - `ARRANGE_MESSAGE_FOR_MODEL_MAP`

### Step 2: 擴展 Agent 基礎類別以支援 ADK 特性

**Target file:** `AgentOccam/AgentOccam.py` - `Agent` class (Line 55)

**Modifications:**

1. 新增 `self.adk_tools` 屬性來儲存註冊的 tools
2. 新增方法:

   - `register_adk_tool(tool_definition)` - 註冊單一 tool
   - `register_adk_toolset(toolset_name)` - 註冊預定義 toolset
   - `get_adk_tools()` - 返回當前註冊的 tools

3. 修改 `call_model_with_message()` wrapper:

   - 檢查 model family 是否為 'adk'
   - 若是，則傳遞 tools 參數給底層 ADK call
   - 處理 ADK 的 tool call responses

4. 新增 `adk_context` 屬性用於管理 ADK session state

**Example tool registration:**

```python
def register_browser_tools(self):
    if self.model_family == 'adk':
        self.register_adk_toolset('browser_automation')
        # 或註冊個別 tools
        self.register_adk_tool({
            'name': 'click_element',
            'description': 'Click on a web element',
            'parameters': {...}
        })
```

### Step 3: 整合 ADK Agent Orchestration 到 Actor-Critic-Judge 流程

**Target file:** `AgentOccam/AgentOccam.py` - `AgentOccam.predict_action()` (Line 1389)

**Approach A: Hybrid Mode (Recommended)**

- 保持現有 Actor-Critic-Judge 架構
- 當 model 為 ADK 時，內部使用 ADK orchestration
- Critic 和 Judge 作為 ADK sub-agents 或保持原有實作

**Approach B: Full ADK Mode**

- 將整個 Actor-Critic-Judge 流程封裝為 ADK multi-agent system
- 使用 ADK 的 agent coordination primitives
- 更大幅度的重構

**Implementation for Approach A:**

```python
def predict_action(self):
    if self.config.actor.model.startswith('adk-'):
        # Use ADK orchestration internally
        return self._predict_action_with_adk()
    else:
        # Use existing flow
        criticism_elements = self.critic.get_criticism_elements()
        action_element_list = self.actor.predict_action(criticism_elements)
        # ...existing code...
```

**ADK-specific orchestration:**

- 定義 sub-agents: critic_agent, actor_agent, judge_agent
- 使用 ADK 的 message passing 或 shared context
- 將結果轉換回原有格式以保持相容性

### Step 4: 更新配置系統以支援 ADK 參數

**Target files:**

- `config_files/*.yml` (all config files)
- `AgentOccam/env.py` (config parsing)

**New config structure:**

```yaml
agent:
  actor:
    model: "adk-gemini-2.0-flash" # 使用 'adk-' prefix
    adk_config:
      runtime: "local" # or "cloud"
      toolsets: ["browser_automation", "planning"]
      memory_backend: "default" # or "persistent"
      orchestration_mode: "sequential" # or "parallel"
      max_tool_calls: 10
      tool_choice: "auto" # or "required" or "none"

  critic:
    model: "adk-gemini-2.0-flash"
    adk_config:
      toolsets: ["evaluation"]

  judge:
    model: "adk-gemini-2.0-flash"
    adk_config:
      toolsets: ["decision_making"]
```

**Config parsing logic:**

- 檢查 model string 是否以 'adk-' 開頭
- 解析 `adk_config` section
- 傳遞給 Agent 初始化

### Step 5: 建立 ADK 與 Playwright 的 bridge

**Files to create:**

- `browser_env/adk_tools.py` - ADK tool definitions
- `browser_env/adk_bridge.py` - Conversion layer

**Tool definitions needed:**

```python
# browser_env/adk_tools.py
ADK_BROWSER_TOOLS = [
    {
        'name': 'click_element',
        'description': 'Click on a web element by ID',
        'parameters': {
            'element_id': {'type': 'string', 'description': 'Element ID from observation'}
        }
    },
    {
        'name': 'type_text',
        'description': 'Type text into an input field',
        'parameters': {
            'element_id': {'type': 'string'},
            'text': {'type': 'string'},
            'press_enter': {'type': 'boolean', 'default': True}
        }
    },
    {
        'name': 'scroll_page',
        'description': 'Scroll the page up or down',
        'parameters': {
            'direction': {'type': 'string', 'enum': ['up', 'down']}
        }
    },
    {
        'name': 'navigate_to',
        'description': 'Navigate to a URL',
        'parameters': {
            'url': {'type': 'string'}
        }
    },
    {
        'name': 'go_back',
        'description': 'Go back in browser history',
        'parameters': {}
    }
]
```

**Bridge functionality:**

```python
# browser_env/adk_bridge.py
class ADKBrowserBridge:
    def __init__(self, env):
        self.env = env

    def execute_tool_call(self, tool_name, parameters):
        """Convert ADK tool call to browser_env action"""
        if tool_name == 'click_element':
            return self.env.step(f"click [{parameters['element_id']}]")
        elif tool_name == 'type_text':
            enter_flag = 1 if parameters.get('press_enter', True) else 0
            return self.env.step(f"type [{parameters['element_id']}] [{parameters['text']}] [{enter_flag}]")
        # ...more conversions...

    def convert_action_to_tool_calls(self, action_string):
        """Convert existing action format to ADK tool calls (for backward compatibility)"""
        # Parse action_string and return list of tool calls
        pass
```

**Integration point:**

- In `Actor.predict_action()`, if using ADK:
  - Pass browser tools to ADK
  - Receive tool calls from ADK
  - Use bridge to execute them
  - Convert results back to observation format

### Step 6: 實作 ADK memory provider 整合

**Target file:** `AgentOccam/AgentOccam.py` - `Agent` class (Lines 65-73)

**Current memory structure:**

```python
self.previous_interactions = {k: [] for k in DEFAULT_DOCUMENTED_INTERACTION_ELEMENTS}
self.online_interaction = {k: None for k in DEFAULT_ONLINE_INTERACTION_ELEMENTS}
```

**ADK memory integration options:**

**Option A: Hybrid (Recommended)**

- 保留現有內存結構
- 選擇性地同步到 ADK memory
- 使用 config flag 控制: `adk_config.use_persistent_memory: true`

**Option B: Full migration**

- 完全使用 ADK memory backend
- 實作 adapter 將現有 dict-based memory 遷移到 ADK

**Implementation for Option A:**

```python
class Agent:
    def __init__(self, config, objective, prompt_template):
        # ...existing code...

        # ADK memory integration
        self.use_adk_memory = (
            self.model_family == 'adk' and
            getattr(config, 'adk_config', {}).get('use_persistent_memory', False)
        )

        if self.use_adk_memory:
            self.adk_memory_client = self._init_adk_memory()

    def update_history(self, **interaction_dict):
        # Update local memory
        for k in interaction_dict.keys():
            if k in self.previous_interactions.keys():
                self.previous_interactions[k].append(interaction_dict[k])

        # Sync to ADK memory if enabled
        if self.use_adk_memory:
            self._sync_to_adk_memory(interaction_dict)

    def _sync_to_adk_memory(self, interaction_dict):
        """Sync interaction to ADK persistent memory"""
        memory_entry = {
            'step': self.get_step(),
            'timestamp': time.time(),
            'data': interaction_dict
        }
        self.adk_memory_client.add(memory_entry)

    def _retrieve_from_adk_memory(self, query, k=5):
        """Retrieve relevant memories from ADK"""
        return self.adk_memory_client.search(query, top_k=k)
```

**Benefits of ADK memory:**

- Cross-session persistence
- Semantic search over history
- Memory compression and summarization
- Efficient retrieval for long-running tasks

## Implementation Priority & Phases

### Phase 1: Basic ADK Support (Minimal Viable Integration)

1. Create `adk.py` provider (Step 1)
2. Register in model maps
3. Basic config support (Step 4 - minimal)
4. Test with simple tasks

**Success criteria:** Can run existing tasks with `model: "adk-gemini-2.0-flash"`

### Phase 2: Tool Integration

1. Define ADK browser tools (Step 5)
2. Build conversion bridge
3. Test with navigation tasks

**Success criteria:** Agent can perform click, type, scroll via ADK tools

### Phase 3: Advanced Features

1. ADK agent orchestration (Step 3)
2. Memory integration (Step 6)
3. Extended tool support
4. Evaluation metrics

**Success criteria:** Full feature parity + ADK enhancements

## Technical Considerations & Trade-offs

### 1. 向後相容性

**Decision: Opt-in approach**

- Pros: 不破壞現有功能、gradual migration path
- Cons: 維護兩套系統
- Implementation: Use `adk-` model prefix as feature flag

**Compatibility strategy:**

- Keep all existing LLM providers functional
- ADK features only activate when model starts with `adk-`
- Provide adapters to convert between formats

### 2. ADK function calling vs 現有 action parsing

**Current:** Regex parsing of `REASON:` and `ACTION:` sections

**ADK:** Structured JSON responses with function calls

**Solution: Dual-mode parser**

```python
def parse_model_response(self, response, model_family):
    if model_family == 'adk' and self._is_structured_response(response):
        return self._parse_adk_structured_response(response)
    else:
        return self.parse_elements(text=response, key_list=self.config.output)

def _parse_adk_structured_response(self, response):
    """Convert ADK structured response to internal format"""
    action_elements = {}
    if 'tool_calls' in response:
        # Convert tool calls to action string
        action_elements['action'] = self._tool_calls_to_action_string(response['tool_calls'])
    if 'reasoning' in response:
        action_elements['reason'] = response['reasoning']
    # ...more conversions...
    return action_elements
```

### 3. 評估框架整合

**Current:** String matching, URL matching, HTML content checking, Gherkin criteria

**ADK:** May have built-in evaluation capabilities

**Approach:**

- Keep existing evaluators as primary
- Add optional `ADKEvaluator` class in `evaluation_harness/`
- Use ADK metrics as additional signals, not replacement

```python
# evaluation_harness/adk_evaluator.py
class ADKEvaluator(Evaluator):
    """Optional evaluator using ADK evaluation tools"""

    def __call__(self, trajectory, config_file, page, client):
        if not self._has_adk_evaluation_tools():
            return 1.0  # Skip if not available

        # Use ADK evaluation APIs
        adk_score = self._evaluate_with_adk(trajectory)

        # Combine with traditional evaluators
        return adk_score
```

### 4. Performance & Latency

**Considerations:**

- ADK may add overhead (runtime initialization, tool registration)
- Network latency for cloud runtime
- Tool call execution time

**Optimization strategies:**

- Cache ADK runtime instances
- Batch tool calls when possible
- Use local runtime for development
- Profile and compare with baseline

### 5. Error Handling & Fallback

**Strategy:**

```python
def call_adk_with_messages(messages, tools=None, model_id='gemini-2.0-flash'):
    try:
        # Try ADK call
        response = adk_client.generate(
            messages=messages,
            tools=tools,
            model=model_id
        )
        return response
    except ADKError as e:
        logger.warning(f"ADK call failed: {e}, falling back to direct API")
        # Fallback to direct Gemini API
        return call_gemini_with_messages(messages=messages, model_id=model_id)
```

## Testing Strategy

### Unit Tests

- Test ADK provider functions independently
- Test message format conversions
- Test tool definition and registration

### Integration Tests

- Test ADK with simple browser tasks
- Test Actor-Critic-Judge flow with ADK
- Test memory persistence and retrieval

### Regression Tests

- Ensure existing configs still work
- Verify non-ADK models unchanged
- Compare performance on benchmark tasks

### Benchmark Tasks

- Use existing timeoff tasks
- Measure: success rate, step count, latency
- Compare ADK vs baseline (Gemini direct)

## Configuration Examples

### Minimal ADK config:

```yaml
# config_files/timeoff_adk_minimal.yml
agent:
  actor:
    model: "adk-gemini-2.0-flash"
  critic:
    model: "gemini-2.0-flash"
    mode: false
  judge:
    model: "gemini-2.0-flash"
    mode: false
```

### Full ADK config:

```yaml
# config_files/timeoff_adk_full.yml
agent:
  actor:
    model: "adk-gemini-2.0-flash"
    adk_config:
      runtime: "local"
      toolsets: ["browser_automation", "planning"]
      memory_backend: "persistent"
      orchestration_mode: "sequential"
      max_tool_calls: 10
      tool_choice: "auto"
      enable_reasoning_traces: true

  critic:
    model: "adk-gemini-2.0-flash"
    adk_config:
      runtime: "local"
      toolsets: ["evaluation"]
      as_subagent: true # Run as ADK sub-agent

  judge:
    model: "adk-gemini-2.0-flash"
    adk_config:
      runtime: "local"
      toolsets: ["decision_making"]
      as_subagent: true
```

## Documentation Needs

1. **ADK Integration Guide** - How to enable and configure ADK
2. **Tool Development Guide** - How to create custom ADK tools
3. **Migration Guide** - How to migrate existing configs to ADK
4. **Troubleshooting** - Common issues and solutions
5. **Performance Tuning** - Optimization tips for ADK

## Open Questions

1. **ADK version compatibility:** Which ADK version to target? How to handle version upgrades?

2. **Prompt engineering for ADK:** Do existing prompts work well with ADK, or do they need adaptation for tool use?

3. **Multi-modal ADK support:** How well does ADK handle image inputs? Any changes needed?

4. **ADK licensing & deployment:** Any constraints on usage, especially for research/academic use?

5. **ADK vs direct API costs:** Cost comparison for production use?

6. **Custom tool development:** What's the best way to extend ADK tools for domain-specific needs?

## Success Metrics

- **Functionality:** All existing tasks run successfully with ADK models
- **Performance:** Latency within 2x of baseline
- **Accuracy:** Success rate ≥ baseline on benchmark tasks
- **Code quality:** Clean integration without breaking existing code
- **Maintainability:** Clear separation of concerns, easy to update

## References & Resources

- Google ADK documentation (to be added)
- Existing LLM provider implementations: `AgentOccam/llms/*.py`
- Agent architecture: `AgentOccam/AgentOccam.py`
- Browser environment: `browser_env/`
- Evaluation framework: `evaluation_harness/`
