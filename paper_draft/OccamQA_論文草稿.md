# OccamQA：結合大型語言模型與結構化探索記錄之網頁應用程式自動探索與測試任務生成

# A Study on Automated Web Application Exploration and Test Task Generation with Large Language Models

# 論文草稿

研究生：［請填入姓名］

指導教授：［請填入指導教授］

中華民國 115 年 4 月

# 摘要

隨著網頁應用程式的功能規模與互動流程日益複雜，測試人員在撰寫端到端測試任務前，往往需要先理解系統頁面結構、操作流程、表單欄位與可驗收狀態。既有大型語言模型網頁代理多以完成已知任務為目標，較少處理「尚未有任務規格」時如何探索軟體受測系統並產生可重播測試任務的問題。

本研究以 AgentOccam1 專案為基礎，提出 OccamQA，一套結合大型語言模型、瀏覽器自動化與結構化探索記錄的網頁應用程式測試任務生成流程。OccamQA 先以安全探索策略操作受測系統，避免刪除、登出、提交等高風險行為，並在每一步擷取畫面文字、互動元素、表單欄位、畫面摘要與轉移關係。系統接著建立去重後的 screen graph，將相似頁面彙整為 page family，抽取具覆蓋價值的 task seed，再生成 Gherkin 風格任務設定與 LLM judge 驗收條件，使任務可回到 AgentOccam 執行迴圈中進行驗證。

本草稿規劃以 TimeOff、KeystoneJS、Spring Petclinic 與自訂範例系統作為實驗對象，評估自動探索覆蓋率、重複狀態去除、任務生成品質與端到端可執行率。初版論文先完成研究動機、系統設計、實驗規劃與待補數據表格，後續將依實際執行結果補齊量化分析。

關鍵字：大型語言模型、網頁代理、軟體測試、自動探索、Gherkin、LLM Judge、AgentOccam、OccamQA

# ABSTRACT

Modern web applications contain complex navigation structures, form workflows, and dynamic states. Before writing end-to-end tests, testers often need to understand the system under test, identify meaningful user workflows, and define verifiable acceptance criteria. Existing LLM-based web agents primarily focus on solving predefined tasks, leaving the problem of discovering an unknown application and generating executable test tasks less explored.

This study proposes OccamQA, a pipeline built on the current AgentOccam1 project for automated web application exploration and task generation. OccamQA combines conservative browser exploration, structured screen recording, screen-graph construction, page-family consolidation, task-seed extraction, and Gherkin-style task synthesis. The pipeline records ScreenRecord and TransitionRecord artifacts, applies safe action filtering, uses LLM-assisted page annotation and action selection, and generates task configurations that can be replayed by the AgentOccam execution loop with Gherkin or LLM-judge based evaluation.

The planned experiments evaluate OccamQA on TimeOff, KeystoneJS, Spring Petclinic, and custom web applications. The metrics include discovered screens, page families, transitions, generated task count, duplicate rate, executable task rate, and end-to-end pass rate. This draft provides the thesis structure, method description, and experiment plan, while quantitative results are intentionally marked as pending until the final experimental runs are completed.

Keywords: Large Language Models, Web Agents, Software Testing, Automated Exploration, Gherkin, LLM Judge, AgentOccam, OccamQA

# 誌謝

本頁為誌謝草稿占位。後續可加入對指導教授、實驗室成員、學長姐、同學與家人的感謝內容。

本研究目前以 AgentOccam1 專案為實作基礎，論文中暫定系統名稱為 OccamQA；若後續專案命名、實驗範圍或貢獻描述調整，誌謝與摘要中的名稱也應一併確認。

# 目錄

摘要

ABSTRACT

誌謝

第一章 緒論

第二章 背景與相關研究

第三章 研究方法

第四章 實驗

第五章 結論與未來展望

附錄A 提示範例

參考文獻

註：請在 Word 中選取目錄後執行「更新功能變數」，以產生正式頁碼。

# 表目錄

表 3-1 OccamQA 主要資料結構

表 4-1 實驗對象規劃

表 4-2 評估指標

表 4-3 自動探索結果（待補）

表 4-4 任務生成與回放結果（待補）

# 圖目錄

圖 3-1 OccamQA 系統架構

圖 3-2 探索到任務生成之資料流

圖 4-1 實驗流程

# 第一章 緒論

## 研究背景與動機

網頁應用程式測試需要同時處理頁面導覽、表單輸入、狀態驗證與例外流程。當系統規模增加時，測試人員不只需要撰寫測試腳本，也需要先理解系統有哪些頁面、使用者可以執行哪些工作，以及哪些畫面狀態能作為驗收依據。這個前置理解過程通常仰賴人工探索，因此容易受到時間、人員經驗與文件完整性的限制。

大型語言模型具備理解自然語言與網頁文字內容的能力，使其能被應用於網頁代理、使用者任務執行與自動化測試。AgentOccam 的核心精神是將網頁觀察與動作空間調整成模型較熟悉的閱讀理解與問答形式，讓模型在較少額外範例的情況下也能完成網頁任務。然而，原始任務執行流程通常假設任務設定已存在，例如使用者已經提供目標、起始網址與評估規則。

本研究關注的問題則更接近測試任務設計的前半段：當測試人員只有一個軟體受測系統與登入狀態時，能否讓系統自動探索頁面、整理系統知識、推導可測試流程，並產生可交由網頁代理執行的任務設定。這也是 OccamQA 名稱中「QA」的核心意義：使大型語言模型不只作為任務執行代理，也成為協助測試人員理解系統與建立測試任務的品質保證工具。

## 研究目的

本研究旨在設計並實作 OccamQA，一套以 AgentOccam 為基礎的網頁應用程式自動探索與測試任務生成流程。系統需能在安全限制下探索受測系統，將畫面與互動轉移記錄為可追蹤資料，並根據探索結果產生 Gherkin 風格任務設定。

具體而言，本研究希望回答下列問題：第一，如何在避免破壞性操作的前提下提升受測系統頁面覆蓋；第二，如何將探索結果從零散截圖轉換為 screen graph、page family 與 SUT dossier 等可重用知識；第三，如何由探索圖自動產生具有驗收條件的任務設定；第四，生成任務如何回到 AgentOccam 執行與評估流程中，形成探索、生成、回放與驗證的閉環。

## 研究貢獻

本研究的主要貢獻可概括為四項。第一，提出一個安全導向的 SUT discovery pipeline，將大型語言模型用於頁面註解、動作排序與下一步決策，同時以規則過濾高風險行為。第二，設計結構化探索資料模型，包含 ScreenRecord、TransitionRecord、ActionCandidate、PageFamily 與 TaskSeed，使探索結果可供除錯、人工檢閱與後續生成任務使用。

第三，提出從 screen graph 到 task seed，再到 Gherkin task config 的任務生成流程，讓系統能根據已探索頁面與路徑產生可重播的測試任務。第四，整合 Gherkin criteria、LLM judge 與 ISP 相關模組，使生成任務可與現有 AgentOccam 執行器、評估器與表單輸入測試流程銜接。

## 論文章節架構

第二章介紹本研究所需背景與相關研究，包括網頁代理、AgentOccam、Gherkin、LLM judge 與網頁應用程式測試。第三章說明 OccamQA 的系統架構與資料流程。第四章規劃實驗設計、評估指標與結果表格。第五章總結本研究並討論未來可延伸方向。附錄列出本系統使用的提示範例，便於後續檢查與重現。

# 第二章 背景與相關研究

## 網頁應用程式測試與自動探索

網頁應用程式測試通常包含導覽測試、表單測試、狀態驗證與端到端流程測試。傳統自動化測試工具能精準重播腳本，但腳本本身通常需要人工撰寫。自動探索工具則試圖從頁面結構中尋找可互動元素並擴展狀態空間，但在動態前端、登入狀態、表單資料與語意驗收條件上仍面臨挑戰。

本研究採取介於傳統 crawler 與大型語言模型代理之間的設計。OccamQA 不以完全窮舉 DOM 狀態為目標，而是以測試任務生成為導向，優先探索可能代表新頁面、新模組或新工作流程的互動。此設計可降低無意義狀態爆炸，同時保留足夠資訊供後續人工與自動化檢查。

## 大型語言模型網頁代理

大型語言模型網頁代理通常將網頁狀態轉換為文字、截圖或可存取性樹，再由模型輸出可執行的動作，例如 click、type、go_back 或 stop。此類代理的成效高度依賴 observation space 與 action space 設計。AgentOccam 以精簡動作集合與結構化觀察為特色，將網頁任務轉換為模型較易處理的閱讀理解問題。

OccamQA 沿用 AgentOccam 的動作表示與執行環境，但任務目標不同。AgentOccam 原始流程面向「已知任務的執行」，OccamQA 則面向「未知任務的發現與生成」。因此，OccamQA 需要額外維護探索記憶、重複頁面判斷、頁面摘要、任務機會與可驗收證據。

## Gherkin 與行為驅動開發

Gherkin 以 Given、When、Then 結構描述使用者情境、操作步驟與預期結果。相較於單一自然語言 intent，Gherkin 更適合作為測試任務輸出格式，因為它能明確區分前提、行為與驗收條件。

本專案已實作 Gherkin parser 與 gherkin_criteria evaluator，能將結構化 Gherkin 任務轉換為 Agent 可理解的自然語言目標，並從 Then 子句萃取驗收標準。OccamQA 的 task synthesizer 會產生與此格式相容的 JSON 任務設定，使生成任務能直接進入現有執行流程。

## LLM Judge 與語意評估

網頁任務的驗收結果有時難以用字串比對或固定 URL 判斷。例如，表單提交後可能顯示動態訊息，列表內容可能因資料狀態而不同，或目標狀態需結合多個頁面元素判讀。LLM judge 透過讀取任務描述與當前頁面狀態，對任務是否完成進行語意判斷，可補足傳統規則評估的不足。

本研究在生成任務時保留兩種評估策略：若任務能以穩定文字、URL 或標題驗證，則優先使用 gherkin_criteria；若任務涉及表單提交、資料新增或語意狀態，則可使用 llm_judge 作為較彈性的驗收方式。

## Input Space Partitioning 與表單測試

表單是網頁應用程式中最常見也最容易出錯的互動類型之一。Input Space Partitioning 將輸入值依有效、邊界、無效、空值等類別劃分，使測試案例能覆蓋更多輸入情境。本專案已有 ISP generator，能根據 accessibility-tree 中的欄位資訊與大型語言模型產生候選輸入分區。

OccamQA 初版探索策略預設不主動提交表單，以降低破壞性操作風險；但在任務生成與後續回放階段，可將已知表單頁面轉換為可檢查的 Gherkin 任務，再由 ISP 延伸輸入組合。這使探索階段與表單強化測試階段保持解耦。

## 相關工具與基準

本研究實作環境使用 Playwright 與 browser_env 進行瀏覽器互動，並參考 AgentOccam 對 WebArena 與 WebVoyager 任務的支援方式。實驗系統規劃包含 TimeOff、KeystoneJS、Spring Petclinic 與自訂範例應用程式，藉此觀察 OccamQA 在登入型管理系統、內容管理系統與一般 CRUD 系統中的適用性。

此外，專案中加入 Google ADK 相關整合，使不同探索角色可透過 role-scoped session 維持提示脈絡與狀態快照。ADK 並非本研究唯一可行實作，但可作為管理多角色 LLM 呼叫與紀錄探索狀態的工程選項。

# 第三章 研究方法

## 方法概述

OccamQA 的整體流程分為六個階段：探索、記錄、圖建構、任務種子抽取、任務生成與回放驗證。探索階段由瀏覽器環境取得目前頁面的 accessibility tree 與互動元素，安全探索策略產生候選動作，大型語言模型協助判斷哪些動作最可能帶來新頁面或新流程。

記錄階段將畫面資訊保存為 ScreenRecord，將動作造成的狀態轉移保存為 TransitionRecord。圖建構階段根據 fingerprint 去除重複畫面，並建立從 root screen 到各目標頁面的路徑。任務生成階段將 screen graph 彙整為 page family 與 task seed，最後輸出可供 AgentOccam 執行的 Gherkin task config。

*圖 3-1 OccamQA 系統架構：SUT → Discovery Runtime → Screen Recorder → Screen Graph → SUT Dossier → Task Synthesizer → AgentOccam Runner*

## 系統架構

OccamQA 目前主要實作於 AgentOccam/discovery 套件。models.py 定義探索資料結構，explorer.py 定義安全候選動作策略，runtime.py 與 runtime_strategy.py 負責單一瀏覽器工作階段的探索控制流程，recorder.py 負責儲存畫面狀態，screen_graph.py 管理去重圖與路徑，dossier.py 彙整 page family 與任務計畫，task_synthesizer.py 產生任務設定。

入口程式 discover_sut.py 讀取 YAML 設定後啟動探索流程。設定包含 SUT 名稱、起始網址、登入 storage state、安全模式、最大步數、模型設定、ADK 設定、任務生成策略與輸出目錄。此設計使 OccamQA 可在不修改 AgentOccam 任務執行器的情況下，作為前置任務生成管線獨立運作。

| 資料結構 | 用途 | 主要欄位 |
| --- | --- | --- |
| ScreenRecord | 保存單一畫面狀態 | screen_id、url、title、observation_text、interactive_elements、form_fields、fingerprint、summary |
| TransitionRecord | 保存一次互動轉移 | from_screen_id、to_screen_id、action、success、action_label、metadata |
| ActionCandidate | 保存可嘗試動作 | action、role、label、zone、signature、novelty_score、selected_by |
| PageFamily | 彙整相似頁面族群 | family_id、canonical_screen_id、page_type、route_patterns、supported_operations |
| TaskSeed | 生成任務前的中介表示 | target_screen_id、task_idea、path_actions、preferred_eval_type、acceptance_hints |

*表 3-1 OccamQA 主要資料結構*

## 安全探索策略

安全探索策略的核心目標是在提高覆蓋率的同時避免破壞受測系統狀態。系統優先考慮 link、button、tab、menuitem、searchbox 等可導覽元素，並以正規表示式過濾 delete、remove、logout、submit、save、confirm 等高風險標籤。初版設定預設 allow_form_fill=false 與 allow_form_submit=false，使探索階段以導航與狀態觀察為主。

在候選動作過多時，策略會根據元素所在區域推估 header、footer、sidebar 或 main，降低隱私權政策、cookie、footer 連結與重複導覽列的優先度。對於月曆、日期切換或可能造成循環的互動，系統以 signature 記錄已嘗試結果，並透過 ExplorationMemory 避免反覆執行低價值或無變化動作。

## 大型語言模型輔助探索

OccamQA 在探索過程中使用多個 LLM 角色。screen_annotation 角色根據目前頁面摘要 page_type、domain_objects、forms_detected、task_opportunities 與 write_risk。action_ranking 角色從候選動作中挑選最可能帶來新流程的互動。next_action 角色在單一 live browser session 中決定下一步動作、go_back 或 stop。page_revisit 角色可判斷新畫面是否只是已訪問頁面的變體。

此設計將 LLM 用於語意判斷，而非完全取代規則。規則層負責安全邊界、候選動作抽取與資料格式；LLM 層負責摘要、排序、去重與任務生成。兩者分工可降低模型幻覺造成的破壞性風險，也使探索結果可被人工審查。

## Screen Graph 與 Page Family

Screen graph 的節點代表去重後的畫面狀態，邊代表由某個動作造成的成功或失敗轉移。每個 ScreenRecord 透過 fingerprint 去重，避免相同頁面因短暫提示訊息、表格排序或重複導覽而被重複計算。當重複畫面出現時，系統會合併缺少的摘要、表單欄位與任務機會。

Page family 是 screen graph 的上一層摘要，用於將路由相似、頁面類型相似或功能相近的畫面合併。此階段會建立 family graph、SUT overview 與 SUT dossier，提供任務生成器更高層次的系統知識，例如系統主要功能區、領域物件、表單頁面與可支援操作。

*圖 3-2 探索到任務生成之資料流：ScreenRecord/TransitionRecord → ScreenGraph → PageFamily → TaskSeed → Gherkin Task Config*

## 任務種子抽取

Task seed 是介於探索結果與最終任務設定之間的中介表示。OccamQA 會從 root screen 到各 page family 的路徑中挑選具有覆蓋價值的目標頁面，並依操作類型與任務價值分配優先級。候選種子需包含目標頁面摘要、路徑動作、可驗收提示、領域物件、支援操作與評估類型建議。

任務種子設計的目的在於避免直接讓模型從整份 graph 自由生成任務。透過 seed 限制，task synthesizer 只能根據實際探索證據產生任務，降低虛構頁面、虛構成功訊息或不穩定驗收條件的機率。

## 任務生成與評估設定

TaskSynthesizer 會根據 task seed、target page family dossier、目標畫面的 accessibility tree 摘要，以及既有 reference task examples 生成 JSON 任務設定。輸出任務包含 sites、task_id、require_login、storage_state、start_url、gherkin 與 eval 欄位。若 LLM 生成失敗，系統會使用 deterministic fallback 建立保守的導航任務。

生成任務預設使用 Gherkin 格式描述 Feature、Scenario、Given、When 與 Then。評估類型可為 gherkin_criteria 或 llm_judge。前者適合穩定文字、標題與 URL；後者適合需語意判斷的表單與工作流程任務。生成後的任務可放入 config_files/<sut_name>/ 目錄，再由 eval_webarena.py 透過 AgentOccam 執行。

## 與 ISP 的整合

OccamQA 的探索階段會記錄 form_fields 與 forms_detected，讓後續表單任務可接續 ISP generator。當某個任務需要填寫欄位時，ISP 模組可根據欄位 label、input_type、required 與周邊文字生成有效、邊界、無效與空值分區，並將分區值注入 Gherkin when steps。

此整合讓 OccamQA 不只產生單一路徑任務，也能在後續延伸出多組輸入測試案例。初版論文將先以可行性與流程整合為主，待實驗資料穩定後，再進一步量化 ISP 生成任務對錯誤發現能力的影響。

## 輸出檔案與可重現性

每次探索執行會在 output/discovery/<run_id>/ 下輸出 manifest、browser config、prompt logs、screens、states、graph、page overview、page families、SUT dossier、task seeds、task plan、task configs 與 summary 等資料。這些檔案使研究者能回溯模型在每一步看到的頁面、選擇的動作與生成任務的依據。

由於目前工作區的探索輸出可能仍在調整或被清理，本草稿不填入實際數值。正式論文應在固定版本與固定設定下重新執行實驗，將 run_id、模型、最大步數、登入狀態與輸出摘要完整保存，以利重現。

# 第四章 實驗

## 研究問題

本研究規劃以四個研究問題評估 OccamQA。RQ1：OccamQA 是否能在安全限制下探索出受測系統的主要頁面與 page family？RQ2：LLM 輔助的動作排序與下一步決策是否能降低重複狀態與低價值互動？RQ3：由探索結果生成的 Gherkin 任務是否具備可讀性、可驗收性與可執行性？RQ4：結合 LLM judge 與 ISP 後，是否能支援更複雜的表單與工作流程測試？

## 實驗對象

| 系統 | 類型 | 登入需求 | 預期觀察重點 | 狀態 |
| --- | --- | --- | --- | --- |
| TimeOff | 請假/人事管理系統 | 需要 | Dashboard、Absence、Staff、Department、Settings 等流程 | 待跑正式結果 |
| KeystoneJS | 內容管理系統 | 需要 | Post、User、Gallery、Category 等 CRUD 與導覽 | 待跑正式結果 |
| Spring Petclinic | 範例 CRUD 系統 | 視設定而定 | Owners、Pets、Visits 等資料流程 | 待跑正式結果 |
| CustomApp | 自訂範例系統 | 視設定而定 | 基本導覽與任務格式驗證 | 待跑正式結果 |

*表 4-1 實驗對象規劃*

## 軟硬體與模型設定

實驗環境規劃使用目前 AgentOccam1 工作區，瀏覽器自動化由 Playwright 與 browser_env 執行，主要模型設定暫定為 gemini-2.5-flash 或 adk-gemini-2.5-flash。探索設定包含 max_steps、time_budget_minutes、safe_mode、allow_form_fill、allow_form_submit、selected_actions_per_screen 與 task_generation_context_chars。正式實驗需記錄作業系統、Python 版本、套件版本、瀏覽器版本與模型供應方式。

目前草稿不宣稱任何已完成實驗結果。後續建議固定每個 SUT 至少執行三次探索，以觀察 LLM 決策的變異，並保存每次 run_id 的 summary、graph、task_plan 與 generated tasks。

## 評估指標

| 指標 | 定義 | 用途 |
| --- | --- | --- |
| Discovered Screens | 去重後 ScreenRecord 數量 | 衡量頁面狀態探索量 |
| Page Families | 彙整後功能頁面族群數 | 衡量功能區覆蓋 |
| Transitions | 成功與失敗互動轉移數 | 衡量探索路徑與互動量 |
| Semantic Revisits | 被判定為已訪問變體的畫面數 | 衡量去重與循環控制 |
| Generated Tasks | 產生的任務設定數 | 衡量任務生成能力 |
| Executable Task Rate | 可由 AgentOccam 成功啟動並完成格式檢查的任務比例 | 衡量工程可用性 |
| Pass Rate | 回放後通過驗收的比例 | 衡量端到端任務品質 |
| Manual Review Score | 人工檢查任務是否合理的分數 | 衡量可讀性與測試價值 |

*表 4-2 評估指標*

## 實驗一：安全探索覆蓋率

實驗一比較 OccamQA 在不同受測系統上的探索覆蓋情況。每次執行固定最大步數與時間預算，統計去重畫面數、page family 數、轉移數與表單頁面數。此實驗用於回答 RQ1，確認 OccamQA 是否能取得足以生成任務的系統知識。

| SUT | Run ID | Max Steps | Screens | Page Families | Transitions | Form Pages | 備註 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TimeOff | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |
| KeystoneJS | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |
| Spring Petclinic | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |
| CustomApp | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |

*表 4-3 自動探索結果（待補）*

## 實驗二：LLM 輔助策略之影響

實驗二比較僅使用規則候選排序與加入 LLM action_ranking、next_action 決策後的探索效果。觀察重點包含重複頁面比例、no-change action 次數、semantic revisit 次數與 page family 覆蓋。若 LLM 能更準確避開 footer 連結、重複導覽與低價值切換，理論上應能在相同步數下探索更多功能頁面。

正式分析時應避免只用單次結果判斷，因為 LLM 輸出可能具有隨機性。建議每組設定至少執行三次，回報平均值與標準差，並附上代表性失敗案例。

## 實驗三：任務生成品質

實驗三檢查 TaskSynthesizer 由 task seed 產生任務設定的品質。人工檢查項目包含：任務是否根據真實探索證據、Given/When/Then 是否清楚、驗收條件是否可判斷、是否避免近似重複任務、是否未虛構不存在的頁面或成功訊息。

自動檢查項目包含 JSON schema 合法性、Gherkin parser 能否成功解析、eval 欄位是否完整、reference acceptance criteria 是否存在，以及 task_id 是否唯一。

| SUT | Seeds | Generated Tasks | Schema Valid | Duplicate Dropped | Manual Useful | 備註 |
| --- | --- | --- | --- | --- | --- | --- |
| TimeOff | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |
| KeystoneJS | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |
| Spring Petclinic | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |
| CustomApp | 待補 | 待補 | 待補 | 待補 | 待補 | 待補 |

*表 4-4 任務生成與回放結果（待補）*

## 實驗四：端到端回放與 ISP 擴充

實驗四將生成任務放回 AgentOccam 執行器中回放，並以 gherkin_criteria 或 llm_judge 評估完成度。對於包含表單欄位的任務，可進一步使用 ISP generator 產生多個輸入分區，觀察任務是否能覆蓋有效、無效、邊界與空值情境。

此實驗可分兩階段進行。第一階段只測試導航型與低風險 workflow 任務，確認生成任務可執行。第二階段再啟用表單與 ISP，並在可重設資料庫或測試環境中執行，以避免污染真實資料。

*圖 4-1 實驗流程：Discovery Run → Task Generation → Static Validation → AgentOccam Replay → Gherkin/LLM Judge Evaluation → Manual Review*

## 威脅與限制

本研究可能受到模型輸出不穩定、受測系統初始資料不同、瀏覽器渲染差異、登入狀態過期與評估器語意判斷偏差影響。為降低這些威脅，正式實驗應固定模型與設定、保存所有 run artifacts、重複執行多次，並以人工檢查補充自動指標。

此外，OccamQA 初版刻意限制表單提交與破壞性操作，因此對需要深層 CRUD 或跨頁提交流程的覆蓋可能不足。此限制是安全性與覆蓋率之間的取捨，後續可透過可重設測試資料庫與風險分級策略逐步放寬。

# 第五章 結論與未來展望

## 結論

本研究提出 OccamQA，一套以 AgentOccam1 專案為基礎的網頁應用程式自動探索與測試任務生成流程。OccamQA 將大型語言模型從單純的任務執行者延伸為測試任務設計助手，透過安全探索、結構化畫面記錄、screen graph、page family、task seed 與 Gherkin 任務生成，協助測試人員從未知系統中取得可重播的測試任務。

本草稿目前完成研究背景、相關研究、系統方法與實驗規劃。因正式探索輸出與回放結果尚需固定設定後重新取得，第四章數據表格暫以待補欄位呈現。後續完成實驗後，應以實際 run artifacts 補上量化分析、失敗案例與人工檢查結果。

## 未來展望

未來可從四個方向延伸。第一，加入可重設資料庫與 sandbox 機制，使 OccamQA 能安全探索更多表單提交與 CRUD 操作。第二，強化 semantic revisit 與 page family 演算法，降低動態列表、警示訊息與 modal 狀態造成的誤判。第三，建立更完整的任務品質評分器，結合 schema validation、LLM critique 與人工抽樣審查。第四，將 ISP 與生成任務更緊密整合，使表單頁面能自動產生多組輸入測試並回報驗證結果。

若後續專案正式更名為 OccamQA，也建議同步更新程式套件名稱、設定檔名稱與輸出資料夾，以降低論文名稱與程式實作之間的落差。

# 附錄A 提示範例

## A.1 畫面註解提示

目的：根據目前頁面 URL、標題、候選動作、表單欄位與 accessibility tree，輸出 page_type、summary、domain_objects、forms_detected、task_opportunities、important_dom 與 write_risk。

輸出格式：strict JSON，不包含 markdown fence，避免額外自然語言說明。

## A.2 動作排序提示

目的：從候選 actions 中選出最可能提升探索覆蓋率的動作，避免 footer/legal links、logout、重複點擊與低價值 toggle。

輸出格式：selected_actions 陣列，每個項目包含 action、reason 與 novelty_score。

## A.3 下一步行動提示

目的：在單一 live browser session 中根據探索記憶、目前頁面摘要、剩餘時間與可用候選動作，選擇一個 candidate action、go_back 或 stop。

輸出格式：action、reason、mark_screen_done。若畫面已無有意義互動，模型可要求 stop 或 go_back。

## A.4 任務生成提示

目的：根據 task seed、target page family dossier、canonical screen evidence 與 reference task examples，生成 up to N 個 JSON 風格任務，包含 task_type、feature、scenario、given、when、then、eval_type、reference_acceptance_criteria、confidence 與 review_notes。

限制：不得虛構不存在的頁面、表單或成功訊息；navigation seed 應產生簡潔可驗證的導覽任務；crud_form seed 可使用 llm_judge；若證據不足應降低 confidence 並在 review_notes 標記。

# 參考文獻

[1] ［作者待補］, “AgentOccam: A Simple Yet Strong Baseline for LLM-Based Web Agents,” ICLR 2025.

[2] ［作者待補］, “WebArena: A Realistic Web Environment for Building Autonomous Agents,” ［出版資訊待補］.

[3] ［作者待補］, “WebVoyager: Building an End-to-End Web Agent with Large Multimodal Models,” ［出版資訊待補］.

[4] Playwright Documentation, ［正式引用格式待補］.

[5] Cucumber/Gherkin Reference, ［正式引用格式待補］.

[6] ［作者待補］, “AutoQALLMs: Automating Web Application Testing Using Large Language Models (LLMs) and Selenium,” ［出版資訊待補］.

[7] ［作者待補］, “使用填表回饋與大型語言模型以自動探索網頁應用程式之研究,” ［學位論文資訊待補］.
