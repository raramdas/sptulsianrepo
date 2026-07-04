#!/bin/bash
# run_dashboard.sh — launch the Streamlit dashboard on the OCI VM.
#
# Access it at http://<VM_IP>:8501 from your browser.
# NOTE: you must open port 8501 in BOTH the OCI security list AND ufw:
#   OCI Console -> VCN -> Security List -> add Ingress rule TCP 8501
#   sudo ufw allow 8501
#
# For persistent running (survives SSH logout), use nohup or a systemd service.

cd "$(dirname "$0")"

# Run in the background, logging to dashboard.log
nohup streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false \
    >> dashboard.log 2>&1 &

echo "Dashboard starting on port 8501 (PID $!)"
echo "Logs: $(pwd)/dashboard.log"
echo "Access at: http://<YOUR_VM_IP>:8501"
