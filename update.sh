#!/bin/bash
set -e

echo "========================================="
echo "       Updating SkillUp System           "
echo "========================================="

# Stop currently running server instance
echo "Stopping currently running server..."
pkill -f runserver || true
sleep 1

# If update archive was uploaded to ~, extract it
if [ -f "C:\Users\jhnmc/skillup_update.tar.gz" ]; then
    echo "Applying updates from skillup_update.tar.gz..."
    tar -xzf "C:\Users\jhnmc/skillup_update.tar.gz" -C "C:\Users\jhnmc/skillup/"
    rm "C:\Users\jhnmc/skillup_update.tar.gz"
fi

cd "C:\Users\jhnmc/skillup"
source venv/bin/activate

# Install any newly added python packages
echo "Checking python dependencies..."
pip install -r requirements.txt

# Run any new migrations
echo "Applying migrations..."
python manage.py migrate --no-input

# Rebuild static files
echo "Collecting static files..."
python manage.py collectstatic --no-input

# Restart server with nohup
echo "Restarting server in background..."
nohup ./start.sh > skillup.log 2>&1 &

sleep 2
echo "========================================="
echo "Update Complete! Server restarted."
echo "========================================="
tail -n 10 skillup.log