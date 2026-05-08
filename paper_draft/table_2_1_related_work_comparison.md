## 表 2.1 相關研究方法之比較

> 註：參考文獻編號請依論文最終 bibliography 順序替換。

| 研究 | 任務表示方式 | 主要應用場景 | 核心方法 | 結果驗收方式 | 與本研究之差異 |
|---|---|---|---|---|---|
| AgentOccam | 自然語言任務 | 一般網頁任務 | 透過 observation space 與 action space alignment，將網頁任務轉換為較適合 LLM 處理的文字化觀察與動作表示 | 主要依賴 WebArena / WebVoyager 既有 evaluator 與 benchmark success rate | 作為本研究基礎架構，但原始設計未導入 Gherkin 測試規格、Then 驗收條件與 ISP 測試案例生成 |
| WebOperator + GPT-4o | 自然語言任務 | 一般網頁任務 | 結合 GPT-4o 與 action-aware tree search，透過搜尋、回溯與節點選擇策略提升長流程任務穩定性 | 主要依賴 benchmark 任務完成率與環境既有評估機制 | 著重任務規劃與搜尋策略，未聚焦結構化測試規格與測試驗收流程 |
| IBM CUGA | 自然語言任務 | 一般網頁任務、企業流程自動化 | 採用階層式 planner-executor 架構，將任務分解、狀態管理與子任務執行模組化 | 以 WebArena、AppWorld 與實務流程任務完成情形為主 | 著重通用代理與企業場景整合，未特別處理 Gherkin 任務執行與 ISP 表單測試案例 |
| SeeAct-ATA | 自然語言測試案例 | 測試場景網頁代理 | 由 SeeAct 架構改造而成，依據自然語言測試步驟逐步執行操作並判斷斷言結果 | 根據測試案例執行結果與最終 verdict 進行評估 | 已進入測試場景，但任務表示仍以自然語言為主，未採用 Gherkin 結構化規格 |
| PinATA | 自然語言測試案例 | 測試場景網頁代理 | 將測試流程拆分為 orchestrator、actor 與 assertor，以提升步驟執行、狀態判讀與 verdict 穩定性 | 根據測試案例執行結果與 verdict 正確性進行評估 | 聚焦自然語言測試案例執行與判定，未進一步結合 Gherkin 規格與 ISP 輸入案例生成 |
| OccamQA（本研究） | Gherkin 任務規格 | 結構化網頁自動化測試 | 延續 AgentOccam 架構，導入 Gherkin 任務規格、Then 驗收條件、ISP 測試案例生成，以及 LLM Judge 輔助判定 | 結合 Then 條件、規則式檢查、LLM Judge，並於實驗中輔以人工檢查代理操作是否與 Gherkin 測試案例對齊 | 聚焦結構化測試任務、可驗證的驗收流程，以及 LLM 是否能根據欄位與頁面資訊生成具代表性的 ISP 測試案例 |


## 精簡版（若論文版面不足可改用）

| 研究 | 主要應用場景 | 核心方法 | 與本研究之差異 |
|---|---|---|---|
| AgentOccam | 一般網頁任務 | 空間對齊的 LLM 網頁代理 | 為本研究基底，但未支援 Gherkin 與 ISP |
| WebOperator + GPT-4o | 一般網頁任務 | LLM 結合 tree search 與回溯 | 著重任務完成率，未聚焦測試規格與驗收 |
| IBM CUGA | 一般網頁任務、企業流程 | 階層式 planner-executor 架構 | 著重通用代理應用，未探討結構化測試規格 |
| SeeAct-ATA | 測試場景網頁代理 | 自然語言測試案例執行 | 已進入測試場景，但任務表示仍偏自然語言 |
| PinATA | 測試場景網頁代理 | 模組化測試代理與 verdict 判定 | 未結合 Gherkin 任務規格與 ISP 輸入案例 |
| OccamQA（本研究） | 結構化網頁自動化測試 | Gherkin + ISP + LLM Judge + 人工檢查 | 聚焦結構化測試規格、表單案例生成與可驗證性 |
