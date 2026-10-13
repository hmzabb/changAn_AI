@echo off
echo ========================================
echo  长安AI Conda环境安装脚本
echo ========================================
echo.

set ENV_PATH=D:\Python\Conda\envs\chang_an_ai
set CONDA=D:\Python\Conda\Scripts\conda.exe

echo [1/4] 创建Conda环境 (Python 3.11)...
echo 执行: %CONDA% create -p "%ENV_PATH%" python=3.11 -y
%CONDA% create -p "%ENV_PATH%" python=3.11 -y
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] 环境创建失败!
    pause
    exit /b 1
)
echo [OK] 环境创建成功
echo.

echo [2/4] 通过Conda安装关键C扩展包（避免DLL冲突）...
%CONDA% install -p "%ENV_PATH%" pydantic httpx openai python-dotenv pydantic-settings -c conda-forge -y
echo.

echo [3/4] 通过pip安装其余依赖...
set PIP=%ENV_PATH%\Scripts\pip.exe
"%PIP%" install fastapi uvicorn[standard] langchain langchain-core langchain-openai langgraph jieba redis prometheus-fastapi-instrumentator pytest pytest-asyncio
echo.

echo [4/4] 验证环境...
"%ENV_PATH%\python.exe" -u -c "from langchain_openai import ChatOpenAI; import httpx; import pydantic; from langgraph.graph import StateGraph; print('ALL OK - environment ready!')"
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] 环境安装完成!
    echo.
    echo 运行测试:
    echo   %ENV_PATH%\python.exe -u scripts\run_test_clean.py
) else (
    echo [FAIL] 导入测试失败
)
echo.
pause