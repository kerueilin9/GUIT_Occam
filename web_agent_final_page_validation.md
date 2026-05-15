# Web Agent Accessibility Snapshot 驗證方案

## 目標

本文件整理 Web Agent 任務執行過程中的頁面狀態保存與驗證方式。目標是建立一套可重現、可追溯的自動化驗證流程，用來比較「人工標記階段的預期執行軌跡」與「Agent 實際執行軌跡」是否一致。

本方案只保留一種主要方式：

```txt
人工標記階段與 Agent 執行階段皆保存：
- final_url / step_url
- screenshot
- raw_ax_tree
- normalized_ax_tree

最後直接比較 expected 與 actual 的 normalized_ax_tree 序列。
```

其中，`raw_ax_tree` 用於保存完整資訊與除錯，`normalized_ax_tree` 作為主要比對依據。

---

## 核心流程

本方案採用逐步保存頁面狀態的方式，而不是只保存最終畫面。

保存時機如下：

```txt
1. 任務開始時先保存一次初始頁面狀態，記為 step 0。
2. 之後 Agent 每完成一個有效操作後，保存一次頁面狀態。
3. stop action 不保存。
4. invalid action 不保存。
```

因此，一筆任務會形成一組 step-wise accessibility snapshot trace。人工標記階段會產生 expected trace，Agent 實際執行後會產生 actual trace。後續驗證時，系統比較兩者的 `normalized_ax_tree` 序列，用以判斷 Agent 執行過程是否與預期操作軌跡一致。

---

## 為什麼使用 AX Tree

本方案使用 Accessibility Tree，而不是直接比對 HTML DOM。Accessibility Tree 更接近 Web Agent 在任務執行中需要理解的頁面狀態，能保留頁面中的主要可見文字、連結、按鈕、表單欄位、標題、區塊結構與互動狀態。

相較於 raw HTML DOM，AX Tree 較少包含樣式、script、framework-generated attribute 等低層雜訊，因此更適合作為測試任務完成狀態與步驟對齊情形的比對依據。

---

## 保存資料

每一個任務輸出一個 JSON 檔案，放在：

```txt
AgentOccam-ScreamShot/{task_name}.json
```

檔名使用任務名稱，例如：

```txt
AgentOccam-ScreamShot/postmill_login_001.json
```

建議 JSON 結構如下：

```json
{
  "task_id": "postmill_login_001",
  "task_name": "postmill_login_001",
  "final_url": "https://example.com/login",
  "normalized_ax_tree": [
    {
      "step": 0,
      "url": "https://example.com/login",
      "screenshot_path": "AgentOccam-ScreamShot/postmill_login_001/step_000.png",
      "normalized_ax_tree": "RootWebArea \"Postmill\"..."
    },
    {
      "step": 1,
      "url": "https://example.com/login",
      "screenshot_path": "AgentOccam-ScreamShot/postmill_login_001/step_001.png",
      "normalized_ax_tree": "RootWebArea \"Postmill\"..."
    }
  ],
  "raw_ax_tree": [
    {
      "step": 0,
      "url": "https://example.com/login",
      "screenshot_path": "AgentOccam-ScreamShot/postmill_login_001/step_000.png",
      "raw_ax_tree": "RootWebArea \"Postmill\"..."
    },
    {
      "step": 1,
      "url": "https://example.com/login",
      "screenshot_path": "AgentOccam-ScreamShot/postmill_login_001/step_001.png",
      "raw_ax_tree": "RootWebArea \"Postmill\"..."
    }
  ]
}
```

說明：

```txt
task_id：任務識別名稱。
task_name：輸出檔案所使用的任務名稱。
normalized_ax_tree：每一步正規化後的 AX Tree，比對時使用。
raw_ax_tree：每一步原始 AX Tree，除錯時使用。
step：頁面狀態保存序號。step 0 為任務開始前的初始狀態。
url：該 step 保存時的頁面 URL。
screenshot_path：該 step 對應的截圖路徑。
```

---

## 正規化規則

`normalized_ax_tree` 的目標是降低不穩定資訊造成的比對差異，同時保留足以描述頁面狀態的關鍵內容。

建議正規化項目如下：

```txt
1. 移除 AX Tree 節點暫態 ID
   例如 [1]、[23]、[64]

2. 統一文字空白
   例如連續空白、換行、tab

3. 移除或忽略 focused 狀態
   focused 可能因游標位置或操作結束狀態不同而變動

4. URL 移除 query string 與 hash
   避免 session、tracking、排序參數造成 false negative

5. 移除空白 StaticText

6. 移除空 name 的 image

7. 視情況移除 LayoutTable、generic 等低語意節點

8. 保留節點角色、可見文字、互動狀態與階層關係
   例如 link、button、textbox、searchbox、heading、StaticText、checked、selected、expanded、required
```

正規化範例：

```txt
[63] link "Log in"
```

轉換為：

```txt
link "Log in"
```

另一個例子：

```txt
[17] button "Filter on: Featured" hasPopup: menu expanded: False focused: True
```

轉換為：

```txt
button "Filter on: Featured" hasPopup: menu expanded: False
```

---

## 比對方式

本方案以 `normalized_ax_tree` 作為主要比對對象。人工標記階段產生 expected trace，Agent 執行階段產生 actual trace，兩者皆為 step-wise snapshot 序列。

基本比對邏輯如下：

```txt
1. 比較 expected 與 actual 的 step 數量是否一致。
2. 逐步比較 expected.normalized_ax_tree[i] 與 actual.normalized_ax_tree[i]。
3. 若某一步不同，保存該 step 的 diff。
4. 若所有 step 皆一致，則視為 snapshot trace 驗證通過。
```

若 Agent 的實際操作步數與人工 reference 不完全一致，則可在分析階段標記為 step mismatch，並檢查差異發生的位置。這類結果可用來分析 Agent 是否跳過步驟、提前完成、重複操作，或在某一步進入不同頁面狀態。

---

## 驗證結果格式

建議另外輸出驗證結果檔，或將結果附加在同一份 JSON 中。

範例：

```json
{
  "task_id": "postmill_login_001",
  "status": "FAIL",
  "expected_steps": 5,
  "actual_steps": 6,
  "step_count_match": false,
  "matched_steps": [0, 1, 2],
  "mismatched_steps": [3, 4],
  "diff_paths": [
    "AgentOccam-ScreamShot/postmill_login_001/diff_step_003.txt",
    "AgentOccam-ScreamShot/postmill_login_001/diff_step_004.txt"
  ]
}
```

---

## 優點

```txt
1. 流程單純，人工 reference 與 Agent actual 對稱保存。
2. 每一步皆有 AX Tree 與 screenshot，可追溯 Agent 何時偏離預期。
3. normalized_ax_tree 可直接作為主要自動化比對依據。
4. raw_ax_tree 保留完整資訊，方便後續 debug。
5. screenshot 保留視覺證據，方便檢查 AX Tree 無法呈現的畫面差異。
6. 比只看最終頁面更能檢查 When 步驟對齊。
```

---

## 風險與注意事項

```txt
1. 整份 normalized_ax_tree 直接比對仍可能受動態內容影響。
2. 若頁面包含時間、通知數、隨機排序、推薦內容等動態區塊，可能產生 false negative。
3. 若 Agent 用不同但等價的操作路徑完成任務，step-wise exact match 可能判定不一致。
4. 需要明確定義 normalization 規則，並確保 expected 與 actual 使用完全相同規則。
5. 若任務允許多種完成路徑，可能需要為同一任務建立多份 reference trace，或在分析時另外標記等價路徑。
```

---

## 論文寫法建議

論文中可描述如下：

```txt
本研究於人工標記階段依照 Gherkin 任務規格操作目標系統，並於任務開始時與每一個操作步驟完成後擷取頁面狀態，保存 final URL、畫面截圖、raw accessibility tree 與 normalized accessibility tree，形成 reference accessibility trace。Agent 執行同一任務時，系統亦以相同方式保存 actual trace，但不針對 stop action 與 invalid action 額外擷取快照。後續驗證時，系統以 normalized accessibility tree 作為主要比對依據，逐步比較 reference trace 與 actual trace，以檢查 Agent 的操作過程是否與 Gherkin 測試步驟保持一致。
```
