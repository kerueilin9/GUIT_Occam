# 🎯 AgentOccam Gherkin 整合 - 快速開始指南

## 修改總覽

你的 AgentOccam 專案已成功整合 **Gherkin (Given-When-Then) 格式**！以下是所有修改的檔案和使用方式。

---

## 📦 新增的檔案

### 1. 核心模組

- **`AgentOccam/gherkin_parser.py`** - Gherkin 解析器
- **`evaluation_harness/gherkin_evaluator.py`** - Gherkin 驗收標準評估器

### 2. 範例與配置

- **`config_files/tasks/gherkin_google_search.json`** - Gherkin 格式的範例任務
- **`config_files/gherkin_test_config.yml`** - 測試配置檔
- **`test_gherkin_parser.py`** - 解析器測試腳本

### 3. 文檔

- **`GHERKIN_INTEGRATION_GUIDE.md`** - 完整使用指南（中文）
- **`GHERKIN_QUICKSTART.md`** - 本檔案（快速開始）

---

## 🔧 修改的檔案

### 1. `AgentOccam/env.py`

- ✅ 新增 Gherkin 解析器導入
- ✅ 支援自動偵測 `intent` 或 `gherkin` 格式
- ✅ 新增方法：
  - `get_gherkin_scenario()` - 取得 Gherkin 場景物件
  - `get_acceptance_criteria()` - 取得驗收標準

### 2. `evaluation_harness/evaluators.py`

- ✅ 新增 `GherkinCriteriaEvaluator` 類別
- ✅ 在 `evaluator_router()` 中註冊 `"gherkin_criteria"` 評估類型

---

## 🚀 快速開始

### 步驟 1: 測試 Gherkin 解析器

```bash
conda activate agentoccam
python test_gherkin_parser.py
```

**預期輸出：**

```
✅ All tests passed!
```

### 步驟 2: 運行範例任務

```bash
python eval_webarena.py --config config_files/gherkin_test_config.yml
```

這會執行 `gherkin_google_search.json` 任務，該任務使用 Gherkin 格式定義。

---

## 📝 建立你的第一個 Gherkin 任務

### 範例：Wikipedia 搜尋任務

建立 `config_files/tasks/my_gherkin_task.json`：

```json
{
  "sites": ["test_site"],
  "task_id": "my_gherkin_task",
  "require_login": false,
  "start_url": "https://www.wikipedia.org",
  "gherkin": {
    "feature": "Wikipedia Search",
    "scenario": "Search for Artificial Intelligence",
    "given": ["I am on Wikipedia homepage"],
    "when": [
      "I search for \"Artificial Intelligence\"",
      "I click on the first result"
    ],
    "then": [
      "I should see an article about AI",
      "The page title should contain \"Artificial Intelligence\""
    ]
  },
  "require_reset": false,
  "eval": {
    "eval_types": ["gherkin_criteria"],
    "reference_answers": {
      "gherkin_acceptance_criteria": [
        "I should see an article about AI",
        "The page title should contain \"Artificial Intelligence\""
      ]
    }
  }
}
```

### 更新配置檔

建立 `config_files/my_config.yml`：

```yaml
env:
  task_ids: ["my_gherkin_task"]
  max_browser_rows: 300
  fullpage: true
  headless: false

agent:
  type: "AgentOccam"
  model_name: "gemini-2.0-flash-exp"
  output:
    - "thought"
    - "action"
  with_planning: false

max_steps: 15
verbose: true
logging: true
logdir: "output"
logname: "my_gherkin_test"
```

### 執行

```bash
python eval_webarena.py --config config_files/my_config.yml
```

---

## 🎯 Gherkin 格式說明

### 基本結構

```json
{
  "gherkin": {
    "feature": "功能名稱",
    "scenario": "場景描述",
    "given": ["前提條件1", "前提條件2"],
    "when": ["動作1", "動作2"],
    "then": ["預期結果1", "預期結果2"]
  }
}
```

### 自動轉換為自然語言

系統會自動將 Gherkin 轉換為 Agent 可理解的目標：

**輸入（Gherkin）：**

```json
{
  "given": ["I am on Google homepage"],
  "when": ["I search for \"Python\""],
  "then": ["I should see Python results"]
}
```

**輸出（Natural Language Objective）：**

```
Starting from I am on Google homepage, perform the following: I search for "Python", so that I should see Python results.
```

---

## 📊 驗收標準評估

### 支援的驗收標準模式

系統會自動識別以下模式：

1. **URL 檢查**

   ```gherkin
   Then The URL should be "https://example.com"
   Then The URL should contain "/profile"
   ```

2. **標題檢查**

   ```gherkin
   Then The page title should contain "Welcome"
   Then The title should be "Dashboard"
   ```

3. **內容檢查**

   ```gherkin
   Then I should see "Login successful"
   Then The page should contain "Welcome back"
   ```

4. **元素存在**

   ```gherkin
   Then There should be a logout button
   Then A search box should exist
   ```

5. **通用條件（使用 LLM 評估）**
   ```gherkin
   Then The search results should be relevant
   Then The page should display user information
   ```

### 評分機制

每個驗收標準會得到 0.0 到 1.0 的分數：

- **1.0** = 完全滿足
- **0.5** = 部分滿足
- **0.0** = 不滿足

最終分數 = 所有標準的平均分數

---

## 🔄 與傳統 Intent 的相容性

### 傳統格式（仍支援）

```json
{
  "task_id": "traditional_task",
  "intent": "Search for Python and click first result",
  "eval": {
    "eval_types": ["string_match"],
    "reference_answers": {
      "fuzzy_match": "Python"
    }
  }
}
```

### Gherkin 格式（新）

```json
{
  "task_id": "gherkin_task",
  "gherkin": {
    "given": ["I am on search page"],
    "when": ["I search for \"Python\"", "I click first result"],
    "then": ["I should see Python content"]
  },
  "eval": {
    "eval_types": ["gherkin_criteria"],
    "reference_answers": {
      "gherkin_acceptance_criteria": ["I should see Python content"]
    }
  }
}
```

**兩種格式可以混用！** 系統會自動偵測 task config 中是否有 `gherkin` 或 `intent` 欄位。

---

## 🔍 除錯與驗證

### 檢視解析結果

```python
from AgentOccam.gherkin_parser import parse_gherkin
import json

with open("config_files/tasks/your_task.json") as f:
    config = json.load(f)

scenario = parse_gherkin(config)
print(f"Objective: {scenario.to_natural_language()}")
print(f"Criteria: {scenario.get_acceptance_criteria()}")
```

### 驗證 Gherkin 語法

```python
from AgentOccam.gherkin_parser import GherkinParser

gherkin_text = """
Feature: Test
Scenario: Test scenario
  When I do something
  Then I should see result
"""

is_valid = GherkinParser.validate(gherkin_text)
print(f"Valid: {is_valid}")
```

---

## 💡 實用技巧

### 1. 組合多種評估方式

```json
{
  "eval": {
    "eval_types": ["string_match", "gherkin_criteria"],
    "reference_answers": {
      "fuzzy_match": "Python",
      "gherkin_acceptance_criteria": [
        "The page title should contain \"Python\"",
        "I should see documentation links"
      ]
    }
  }
}
```

### 2. 使用引號標記具體值

```gherkin
When I search for "Python 3.12"
And I click on the "Download" button
Then The file "python-3.12.exe" should download
```

### 3. 多步驟場景

```json
{
  "when": [
    "I click on \"Sign In\"",
    "I enter \"user@example.com\" in email field",
    "I enter \"password123\" in password field",
    "I click on \"Login\" button"
  ],
  "then": [
    "I should be logged in",
    "The URL should contain \"/dashboard\"",
    "I should see \"Welcome back\""
  ]
}
```

---

## 📚 進階使用

### 文字格式（單行 Gherkin）

```json
{
  "gherkin": "Feature: Search\nScenario: Find info\n  Given I am on homepage\n  When I search\n  Then I see results"
}
```

### 檢查多個條件

評估器會逐一檢查每個 `then` 條件，並計算平均分數：

```json
{
  "then": [
    "The login should be successful",
    "I should see my username",
    "The URL should be /dashboard",
    "There should be a logout button"
  ]
}
```

如果 4 個條件的分數分別是 `[1.0, 1.0, 1.0, 0.5]`，最終分數 = `(1.0 + 1.0 + 1.0 + 0.5) / 4 = 0.875`

---

## ✅ 驗證安裝

運行以下命令確認所有功能正常：

```bash
# 1. 測試解析器
python test_gherkin_parser.py

# 2. 運行範例 Gherkin 任務
python eval_webarena.py --config config_files/gherkin_test_config.yml
```

---

## 🎉 總結

你的專案現在支援：

1. ✅ **Gherkin 格式任務定義** - 使用 Given-When-Then 結構
2. ✅ **自動解析與轉換** - Gherkin → 自然語言目標
3. ✅ **智慧評估** - 基於驗收標準的自動評分
4. ✅ **向後相容** - 傳統 `intent` 格式仍可使用
5. ✅ **LLM 輔助** - 複雜條件使用 Gemini 評估

開始使用 Gherkin 讓你的 Web Agent 測試更結構化、更易維護！

---

## 📖 更多資訊

詳細文檔請參閱：**`GHERKIN_INTEGRATION_GUIDE.md`**

有問題或需要協助？檢查範例任務：

- `config_files/tasks/gherkin_google_search.json`
- `test_gherkin_parser.py`
