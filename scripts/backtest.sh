#!/usr/bin/env bash
# MarketMind-Pro backtest — one-command start/stop/status.
#
#   bash scripts/backtest.sh            # = start
#   bash scripts/backtest.sh start      # run in background, open report when done
#   bash scripts/backtest.sh status     # is it running? + last report
#   bash scripts/backtest.sh stop       # kill a running backtest
#
# Everything runs locally as a single Python process (no Docker, no services).
# Extra arguments after `start` are passed to `python -m src.backtest`,
# e.g.:  bash scripts/backtest.sh start --rotate 8 --export-weights

set -euo pipefail
cd "$(dirname "$0")/.."

PID_FILE="data/backtest.pid"
LOG_FILE="reports/backtest.log"
PYTHON="${PYTHON:-python3}"

running_pid() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    kill -0 "$pid" 2>/dev/null || return 1
    echo "$pid"
}

latest_report() {
    ls -t reports/backtest_*.html 2>/dev/null | head -1 || true
}

cmd_start() {
    if pid=$(running_pid); then
        echo "⏳ Backtest already running (pid $pid). Use: bash scripts/backtest.sh status"
        exit 1
    fi
    if ! "$PYTHON" -c "import pyarrow" 2>/dev/null; then
        echo "Installing missing dependency: pyarrow…"
        "$PYTHON" -m pip install --quiet pyarrow
    fi
    mkdir -p data reports
    local args=("$@")
    # Default: the FULL candidate pool (~435 tickers). First run downloads
    # everything (long); later runs come from the parquet cache.
    [ ${#args[@]} -eq 0 ] && args=(--mode all --tickers ALL --export-weights)

    echo "🚀 Starting backtest in the background: python -m src.backtest ${args[*]}"
    echo "   Log: $LOG_FILE"
    (
        "$PYTHON" -m src.backtest "${args[@]}" >"$LOG_FILE" 2>&1
        status=$?
        rm -f "$PID_FILE"
        report=$(ls -t reports/backtest_*.html 2>/dev/null | head -1)
        if [ "$status" -eq 0 ] && [ -n "$report" ]; then
            command -v open >/dev/null && open "$report"
        fi
    ) &
    echo $! > "$PID_FILE"
    echo "✅ Running (pid $(cat "$PID_FILE")). The report opens in your browser when done."
    echo "   Check progress:  bash scripts/backtest.sh status"
    echo "   Stop early:      bash scripts/backtest.sh stop"
}

cmd_status() {
    if pid=$(running_pid); then
        echo "⏳ Backtest is RUNNING (pid $pid)."
        [ -f "$LOG_FILE" ] && { echo "── last log lines ──"; tail -5 "$LOG_FILE"; }
    else
        echo "💤 No backtest is running."
    fi
    report=$(latest_report)
    if [ -n "$report" ]; then
        echo "📄 Latest report: $report"
        echo "🗂  All runs:      reports/index.html"
    else
        echo "📄 No reports yet — run: bash scripts/backtest.sh start"
    fi
}

cmd_stop() {
    if pid=$(running_pid); then
        # Kill the wrapper subshell and its python child.
        pkill -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
        rm -f "$PID_FILE"
        echo "🛑 Backtest stopped (pid $pid). Nothing is left running."
    else
        rm -f "$PID_FILE"
        echo "💤 No backtest was running."
    fi
}

case "${1:-start}" in
    start)  shift || true; cmd_start "$@" ;;
    status) cmd_status ;;
    stop)   cmd_stop ;;
    *) echo "Usage: bash scripts/backtest.sh [start|status|stop]"; exit 1 ;;
esac
