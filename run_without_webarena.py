"""
簡化版的執行腳本，不需要 WebArena server
適合測試自定義任務或真實網站任務
"""
import os
import sys

# 確保不會因為缺少 WebArena 環境變數而報錯
# 設置一些預設值（即使不使用也無妨）
os.environ.setdefault('SHOPPING', 'http://placeholder')
os.environ.setdefault('SHOPPING_ADMIN', 'http://placeholder')
os.environ.setdefault('REDDIT', 'http://placeholder')
os.environ.setdefault('GITLAB', 'http://placeholder')
os.environ.setdefault('MAP', 'http://placeholder')
os.environ.setdefault('WIKIPEDIA', 'http://placeholder')
os.environ.setdefault('HOMEPAGE', 'http://placeholder')

# 確保至少有一個 LLM API Key
has_openai = 'OPENAI_API_KEY' in os.environ
has_gemini = 'GEMINI_API_KEY' in os.environ

if not has_openai and not has_gemini:
    print("⚠️  警告：未設定任何 LLM API Key")
    print("請執行以下其中一個：")
    print("  - OpenAI: $env:OPENAI_API_KEY = 'your-api-key-here'")
    print("  - Gemini: $env:GEMINI_API_KEY = 'your-api-key-here'")
    sys.exit(1)

if has_gemini and not has_openai:
    print("✓ 使用 Gemini API")
elif has_openai and not has_gemini:
    print("✓ 使用 OpenAI API")
else:
    print("✓ 已設定多個 API Keys（將根據 config 中的模型選擇）")

# 執行原始的 eval_webarena.py
from eval_webarena import run

if __name__ == "__main__":
    print("=" * 80)
    print("🚀 執行 AgentOccam（不使用 WebArena server）")
    print("=" * 80)
    run()
