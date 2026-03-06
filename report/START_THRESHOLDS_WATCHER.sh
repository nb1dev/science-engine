#!/bin/bash
# Start Population Thresholds Watcher in Background

cd /Users/pnovikova/Documents/work

if pgrep -f "update_population_thresholds.py" > /dev/null; then
    echo "⚠️  Thresholds watcher is already running!"
    echo "   To stop: pkill -f update_population_thresholds.py"
    exit 1
fi

nohup python3 science-engine/report/update_population_thresholds.py > logs/thresholds_update.log 2>&1 &
PID=$!

echo "✓ Population Thresholds Watcher started"
echo "  PID: $PID"
echo "  Log: logs/thresholds_update.log"
echo "  Interval: 7 days"
echo ""
echo "Manual run: python3 science-engine/report/update_population_thresholds.py --once"
echo "Stop: pkill -f update_population_thresholds.py"
