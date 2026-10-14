#!/usr/bin/env bash
# GitHub'da yangi o'zgarish bo'lsa, uni yuklab botni qayta ishga tushiradi. Taymer har 2 daqiqada chaqiradi.
set -e
cd "$(dirname "$0")"
git fetch -q origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    git reset -q --hard origin/main
    venv/bin/pip install -q -r requirements.txt
    systemctl restart quiz-bot
    echo "Yangilandi: $(git log -1 --format='%h %s')"
fi
