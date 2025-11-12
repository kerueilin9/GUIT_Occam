# Gherkin Integration Guide for AgentOccam

## 概述

AgentOccam 現在支援使用 **Gherkin 格式** (Given-When-Then) 來定義任務目標和驗收標準。這使得任務定義更加結構化、可測試，並符合 BDD (Behavior-Driven Development) 最佳實踐。

---

## 🎯 什麼是 Gherkin？

Gherkin 是一種用於描述軟體行為的語言，使用自然語言的結構化格式：

```gherkin
Feature: 功能描述
Scenario: 場景描述
  Given 前提條件
  When 執行動作
  Then 預期結果
```

### 範例

```gherkin
Feature: Search functionality
Scenario: Search for Python programming resources
  Given I am on Google homepage
  When I search for "Python programming"
  And I click on the first result
  Then I should see Python-related content
  And The page title should contain "Python"
```

---

## 📁 任務配置格式

### 選項 1: 結構化格式 (推薦)

```json
{
  "sites": ["test_site"],
  "task_id": "gherkin_google_search",
  "require_login": false,
  "storage_state": null,
  "start_url": "https://www.google.com",
  "geolocation": null,
  "gherkin": {
    "feature": "Search functionality",
    "scenario": "Search for Python programming resources",
    "given": ["I am on Google homepage"],
    "when": [
      "I search for \"Python programming\"",
      "I click on the first result"
    ],
    "then": [
      "I should see Python-related content",
      "The page title should contain \"Python\""
    ]
  },
  "require_reset": false,
  "eval": {
    "eval_types": ["string_match", "gherkin_criteria"],
    "reference_answers": {
      "fuzzy_match": "Python",
      "gherkin_acceptance_criteria": [
        "I should see Python-related content",
        "The page title should contain \"Python\""
      ]
    }
  }
}
```

### 選項 2: 純文字格式

```json
{
  "sites": ["test_site"],
  "task_id": "gherkin_text_format",
  "start_url": "https://www.google.com",
  "gherkin": "Feature: Search functionality\nScenario: Search for Python\n  Given I am on Google homepage\n  When I search for \"Python\"\n  Then I should see Python content",
  "eval": {
    "eval_types": ["gherkin_criteria"],
    "reference_answers": {
      "gherkin_acceptance_criteria": ["I should see Python content"]
    }
  }
}
```

### 選項 3: 傳統 intent 格式 (仍支援)

```json
{
  "task_id": "traditional_intent",
  "start_url": "https://www.google.com",
  "intent": "Search for Python programming and click the first result",
  "eval": {
    "eval_types": ["string_match"],
    "reference_answers": {
      "fuzzy_match": "Python"
    }
  }
}
```

---

## 🔧 系統架構

### 1. Gherkin 解析器 (`AgentOccam/gherkin_parser.py`)

```python
from AgentOccam.gherkin_parser import parse_gherkin, gherkin_to_objective

# 解析 Gherkin 場景
scenario = parse_gherkin(gherkin_text_or_dict)

# 轉換為自然語言目標
objective = gherkin_to_objective(gherkin_text_or_dict)
```

**功能：**

- 解析 Gherkin 文字或字典格式
- 驗證 Gherkin 語法
- 轉換為 Agent 可理解的自然語言目標
- 提取驗收標準 (acceptance criteria)

### 2. 環境包裝器 (`AgentOccam/env.py`)

**自動偵測格式：**

- 如果 task config 包含 `"gherkin"` 欄位 → 使用 Gherkin 模式
- 如果包含 `"intent"` 欄位 → 使用傳統模式
- 兩者都不存在 → 拋出錯誤

**新方法：**

```python
env = WebArenaEnvironmentWrapper(config_file)

# 取得目標 (自動轉換為自然語言)
objective = env.get_objective()

# 取得原始 Gherkin 場景
scenario = env.get_gherkin_scenario()

# 取得驗收標準
criteria = env.get_acceptance_criteria()
```

### 3. Gherkin 評估器 (`evaluation_harness/gherkin_evaluator.py`)

**評估方法：**

1. **URL 檢查**

   - "The URL should be X"
   - "The URL should contain X"

2. **標題檢查**

   - "The title should contain X"
   - "The title should be X"

3. **內容檢查**

   - "I should see X"
   - "The page should contain X"
   - 使用 LLM 進行模糊匹配

4. **元素檢查**

   - "There should have X"
   - "X should exist"
   - 使用 LLM 判斷元素是否存在

5. **通用 LLM 評估**
   - 對於複雜的驗收標準，使用 LLM 進行綜合評估
   - 返回 0.0 到 1.0 的分數

---

## 🚀 使用方式

### 步驟 1: 建立 Gherkin 任務檔案

在 `config_files/tasks/` 目錄下建立新的任務檔案：

```bash
config_files/tasks/gherkin_wikipedia_search.json
```

```json
{
  "sites": ["test_site"],
  "task_id": "gherkin_wikipedia_search",
  "require_login": false,
  "start_url": "https://www.wikipedia.org",
  "gherkin": {
    "feature": "Wikipedia Search",
    "scenario": "Find information about Python programming language",
    "given": ["I am on Wikipedia homepage"],
    "when": [
      "I search for \"Python programming language\"",
      "I click on the first search result"
    ],
    "then": [
      "I should see the Python programming language article",
      "The page title should contain \"Python\"",
      "The page should contain information about Guido van Rossum"
    ]
  },
  "require_reset": false,
  "eval": {
    "eval_types": ["gherkin_criteria"],
    "reference_answers": {
      "gherkin_acceptance_criteria": [
        "I should see the Python programming language article",
        "The page title should contain \"Python\"",
        "The page should contain information about Guido van Rossum"
      ]
    }
  }
}
```

### 步驟 2: 更新 YAML 配置

```yaml
env:
  task_ids: ["gherkin_wikipedia_search"]
  max_browser_rows: 300
  fullpage: true

agent:
  type: "AgentOccam"
  model_name: "gemini-2.0-flash-exp"

max_steps: 15
```

### 步驟 3: 執行實驗

```bash
conda activate agentoccam
python eval_webarena.py --config your_config.yml
```

---

## 📊 Gherkin 語法指南

### Given (前提條件)

描述測試開始前的初始狀態：

```gherkin
Given I am on the homepage
Given I am logged in as "user@example.com"
Given the shopping cart is empty
Given I have navigated to "https://example.com"
```

### When (執行動作)

描述使用者執行的操作：

```gherkin
When I click on "Login" button
When I enter "password123" in the password field
When I search for "Python"
When I scroll down to the footer
```

### Then (預期結果)

描述預期的結果或系統狀態：

```gherkin
Then I should see "Welcome back"
Then The page title should be "Dashboard"
Then The URL should contain "/profile"
Then There should be a logout button
Then The page should contain "Your order has been placed"
```

### And / But (連接詞)

用於連接多個相同類型的步驟：

```gherkin
Given I am on the homepage
And I am logged in
And My cart contains 3 items

When I click on "Checkout"
And I enter my shipping address
And I select "Express Shipping"

Then I should see the order confirmation
And I should receive a confirmation email
But I should not see any error messages
```

---

## 🎯 驗收標準撰寫技巧

### 1. 明確且可測試

❌ **不好**

```gherkin
Then The page should look good
```

✅ **好**

```gherkin
Then The page title should contain "Success"
And The success message should be visible
```

### 2. 使用具體的值

❌ **不好**

```gherkin
Then I should see some results
```

✅ **好**

```gherkin
Then I should see at least 5 search results
And The first result should contain "Python"
```

### 3. 檢查多個條件

```gherkin
Then The login should be successful
And I should see my username "John Doe"
And The URL should be "https://example.com/dashboard"
And There should be a logout button
```

### 4. 支援的驗收標準模式

系統會自動識別以下模式：

| 模式     | 範例                         | 評估方式     |
| -------- | ---------------------------- | ------------ |
| URL 檢查 | "The URL should be X"        | 精確匹配     |
| URL 包含 | "The URL should contain X"   | 部分匹配     |
| 標題檢查 | "The title should contain X" | 部分匹配     |
| 內容檢查 | "I should see X"             | LLM 模糊匹配 |
| 元素存在 | "There should be X"          | LLM 判斷     |
| 通用條件 | 其他任何陳述                 | LLM 綜合評估 |

---

## 💡 進階使用

### 組合多種評估方式

```json
{
  "eval": {
    "eval_types": ["string_match", "url_match", "gherkin_criteria"],
    "reference_answers": {
      "exact_match": "Python",
      "url_match": "python.org",
      "gherkin_acceptance_criteria": [
        "I should see Python documentation",
        "The page should contain download links"
      ]
    }
  }
}
```

### 使用變數和引號

在 When 和 Then 中使用引號標記具體的值：

```gherkin
When I search for "Python programming"
And I click on the "First" result
Then The page should contain "Welcome to Python.org"
```

### 複雜場景範例

```json
{
  "gherkin": {
    "feature": "E-commerce Shopping",
    "scenario": "Complete a purchase workflow",
    "given": [
      "I am on the product page for \"MacBook Pro\"",
      "I am logged in as a registered user"
    ],
    "when": [
      "I click on \"Add to Cart\" button",
      "I navigate to the shopping cart",
      "I click on \"Proceed to Checkout\"",
      "I enter my shipping address",
      "I select \"Credit Card\" as payment method",
      "I click on \"Place Order\""
    ],
    "then": [
      "I should see the order confirmation page",
      "The page should contain my order number",
      "The URL should contain \"/order-confirmation\"",
      "I should receive an email confirmation",
      "The order total should match the cart total"
    ]
  }
}
```

---

## 🔍 除錯技巧

### 檢視轉換後的目標

```python
from AgentOccam.gherkin_parser import parse_gherkin

# 讀取你的 task config
with open("config_files/tasks/your_task.json") as f:
    config = json.load(f)

# 解析 Gherkin
scenario = parse_gherkin(config)

# 查看轉換後的自然語言目標
print(scenario.to_natural_language())
print(scenario.get_acceptance_criteria())
```

### 測試驗收標準評估

評估器會在執行時輸出每個標準的評分：

```
[Gherkin Criteria] Evaluating: "The page title should contain Python"
Score: 1.0

[Gherkin Criteria] Evaluating: "I should see Python documentation"
Score: 0.85
```

---

## 📝 與傳統 Intent 的對照

| 傳統 Intent              | Gherkin 等價                                                                                                                         |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| "Search for Python"      | Given: "I am on search page"<br>When: "I search for \"Python\""<br>Then: "I should see results"                                      |
| "Login with credentials" | Given: "I am on login page"<br>When: "I enter username and password"<br>And: "I click login button"<br>Then: "I should be logged in" |

---

## 🎉 總結

使用 Gherkin 格式的優點：

1. ✅ **更結構化** - 清楚分離前提、動作、預期結果
2. ✅ **可測試性** - 驗收標準明確且可自動化評估
3. ✅ **可讀性** - 非技術人員也能理解和撰寫
4. ✅ **可維護性** - 容易修改和擴展測試場景
5. ✅ **BDD 標準** - 符合業界最佳實踐

開始使用範例任務：

```bash
python eval_webarena.py --config config_files/gherkin_test_config.yml
```
