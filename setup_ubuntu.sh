#!/bin/bash
set -e

echo "========================================="
echo "  Setting up SkillUp System on Ubuntu    "
echo "========================================="

sudo apt update
sudo apt install -y python3 python3-pip python3-venv

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Running migrations..."
python manage.py migrate --no-input

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "========================================="
echo "Setup finished successfully!"
echo "To run the app, simply execute: ./start.sh"
echo "========================================="