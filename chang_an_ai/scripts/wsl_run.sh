#!/bin/bash
# WSL持久化测试脚本 - --full 模式验证缓存
rm -f /root/test_output_v2.txt
echo "Test started at $(date)" > /root/test_status.txt
cd /mnt/d/zmz/project/chang_an_travel/chang_an_ai
/root/venv_ca/bin/python -u scripts/test_agent_comprehensive.py --full > /root/test_output_v2.txt 2>&1
RC=$?
echo "Test finished with exit code $RC at $(date)" >> /root/test_status.txt
exit $RC