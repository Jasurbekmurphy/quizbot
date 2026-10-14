#!/usr/bin/env bash
# VPS (Ubuntu/Debian) ga o'rnatish. Bot papkasi ichida root sifatida ishga tushiring:  bash install.sh
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"

apt-get update -q
apt-get install -y -q python3 python3-venv git

python3 -m venv venv
venv/bin/pip install -q -r requirements.txt

[ -f .env ] || cp .env.example .env

cat > /etc/systemd/system/quiz-bot.service <<UNIT
[Unit]
Description=Test (quiz) Telegram boti
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# GitHub'dan avtomatik yangilanish (papka git orqali olingan bo'lsa)
if [ -d .git ]; then
    chmod +x update.sh
    cat > /etc/systemd/system/quiz-bot-update.service <<UNIT
[Unit]
Description=Test botini GitHub'dan yangilash

[Service]
Type=oneshot
ExecStart=/bin/bash $DIR/update.sh
UNIT
    cat > /etc/systemd/system/quiz-bot-update.timer <<UNIT
[Unit]
Description=Test botini har 2 daqiqada yangilash

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT
fi

systemctl daemon-reload
systemctl enable quiz-bot >/dev/null 2>&1
[ -d .git ] && systemctl enable --now quiz-bot-update.timer >/dev/null 2>&1 && echo "✅ Avtomatik yangilanish yoqildi (har 2 daqiqada GitHub tekshiriladi)"

if grep -q '^BOT_TOKEN=.\+' .env; then
    systemctl restart quiz-bot
    echo "✅ Bot ishga tushdi. Holatini ko'rish: systemctl status quiz-bot"
else
    echo "⚠️ Endi .env faylini to'ldiring:  nano $DIR/.env"
    echo "   Keyin ishga tushiring:        systemctl restart quiz-bot"
fi
