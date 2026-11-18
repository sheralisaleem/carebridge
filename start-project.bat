@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the CareBridge project folder.
    pause
    exit /b 1
)

set "PYTHON=.venv\Scripts\python.exe"
set "STATUS=1"

if not exist ".env" (
    copy ".env.example" ".env" >nul
    if errorlevel 1 goto :failed
    echo Created .env. Set the LLM provider and API key there, then run this launcher again.
    echo See HOW_TO_RUN.txt for setup instructions.
    pause
    goto :done
)

if not exist "%PYTHON%" (
    echo Creating the .venv virtual environment with Python 3.11...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo Install Python 3.11 with the Windows Python launcher, then try again.
        goto :failed
    )
)

"%PYTHON%" -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 (
    echo The .venv Python environment is unavailable or older than Python 3.11.
    echo See HOW_TO_RUN.txt for repair instructions.
    goto :failed
)

"%PYTHON%" -c "import anthropic, argon2, chromadb, dotenv, email_validator, httpx, jinja2, mcp, numpy, openai, starlette, uvicorn, websockets" >nul 2>&1
if errorlevel 1 (
    echo Installing CareBridge dependencies...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :failed
)

echo Starting CareBridge. Open the URL printed by Uvicorn when startup completes.
echo Press Ctrl+C in this window to stop the server.
"%PYTHON%" -m clinic_agent.web --demo-doctor-signup %*
set "STATUS=%errorlevel%"
if "%STATUS%"=="0" goto :done

:failed
echo CareBridge could not start. Review the message above or see HOW_TO_RUN.txt.
pause

:done
popd
endlocal & exit /b %STATUS%
