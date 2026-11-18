#!/bin/bash
source venv/bin/activate
echo "==========================================================="
echo " Starting SkillUp Server..."
echo " Access locally on the server: http://127.0.0.1:8000"
echo " Access from other devices:    http://192.168.100.73:8000"
echo "==========================================================="
python manage.py runserver 0.0.0.0:8000