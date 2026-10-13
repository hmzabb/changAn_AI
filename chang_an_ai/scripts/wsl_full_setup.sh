#!/bin/bash
# WSL full setup script - all output goes to file for visibility
LOGFILE=/root/venv_ca_install.log
echo "=== Starting full install ===" > $LOGFILE
echo "Date: $(date)" >> $LOGFILE
/root/venv_ca/bin/pip install \
  pydantic pydantic-core pydantic-settings \
  httpx httpcore \
  openai \
  langchain langchain-core langchain-openai langgraph \
  python-dotenv fastapi jieba redis prometheus-fastapi-instrumentator \
  --no-cache-dir --timeout 120 >> $LOGFILE 2>&1
echo "PIP EXIT: $?" >> $LOGFILE
echo "=== Verifying ===" >> $LOGFILE
/root/venv_ca/bin/python -c "
import openai; print('openai', openai.__version__)
import httpx; print('httpx', httpx.__version__)
from langchain_openai import ChatOpenAI; print('langchain_openai OK')
import langgraph; print('langgraph OK')
import langchain; print('langchain OK')
" >> $LOGFILE 2>&1
echo "VERIFY EXIT: $?" >> $LOGFILE
echo "=== Complete ===" >> $LOGFILE
cat $LOGFILE