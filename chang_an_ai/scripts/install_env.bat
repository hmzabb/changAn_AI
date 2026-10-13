@echo off
echo ========================================
echo  长安AI 环境安装 (Conda Python + venv)
echo ========================================
echo.

set ENV_PATH=D:\Python\Conda\envs\chang_an_ai
set PYTHON=D:\Python\Conda\python.exe

echo [1/5] 使用Conda Python创建venv...
"%PYTHON%" -m venv "%ENV_PATH%"
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] 创建失败!
    pause
    exit /b 1
)
echo [OK] 创建成功
echo.

echo [2/5] 升级pip...
"%ENV_PATH%\Scripts\python.exe" -m pip install --upgrade pip -q
echo [OK]
echo.

echo [3/5] 安装核心包 (避免DLL冲突)...
"%ENV_PATH%\Scripts\pip.exe" install ^
    pydantic pydantic-core pydantic-settings ^
    httpx httpcore ^
    openai ^
    python-dotenv ^
    --no-cache-dir
echo [OK]
echo.

echo [4/5] 安装AI/Agent依赖...
"%ENV_PATH%\Scripts\pip.exe" install ^
    langchain langchain-core langchain-openai ^
    langgraph ^
    fastapi uvicorn ^
    jieba redis ^
    prometheus-fastapi-instrumentator ^
    pytest pytest-asyncio ^
    --no-cache-dir
echo [OK]
echo.

echo [5/5] 验证关键导入...
"%ENV_PATH%\Scripts\python.exe" -u -c "from langchain_openai import ChatOpenAI; import httpx; import pydantic; from langgraph.graph import StateGraph; from pydantic_settings import BaseSettings; print('ALL IMPORTS OK')"
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================
    echo  [SUCCESS] 环境就绪!
    echo ========================================
    echo.
    echo 使用方法:
    echo   %ENV_PATH%\Scripts\python.exe -u scripts\run_test_clean.py
    echo.
) else (
    echo [FAIL] 导入失败
)
pause