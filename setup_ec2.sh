#!/bin/bash
# setup_ec2.sh
# Run once after SSH-ing into a fresh Ubuntu 22.04 EC2 instance.
# Sets up the environment and prepares the agent to run.
#
# Usage:
#   chmod +x setup_ec2.sh
#   ./setup_ec2.sh

set -e  # exit on any error

echo "=== [1/6] Updating system packages ==="
sudo apt-get update -qq && sudo apt-get upgrade -y -qq

echo "=== [2/6] Installing Python and tools ==="
sudo apt-get install -y python3-pip python3-venv tmux git unzip -qq

echo "=== [3/6] Creating project directory ==="
mkdir -p ~/research/outputs
cd ~/research

echo "=== [4/6] Creating Python virtual environment ==="
python3 -m venv venv
source venv/bin/activate

echo "=== [5/6] Installing Python dependencies ==="
pip install --quiet anthropic boto3 requests python-dotenv

echo "=== [6/6] Verifying installs ==="
python3 -c "import anthropic, boto3, requests, dotenv; print('All dependencies OK')"

echo ""
echo "======================================"
echo "  Setup complete."
echo ""
echo "  Next steps:"
echo "  1. Copy your files to this instance:"
echo "     scp research_agent.py .env.example eu_offgrid_solar_research_spec.md ubuntu@<EC2-IP>:~/research/"
echo ""
echo "  2. Create your .env file:"
echo "     cp .env.example .env"
echo "     nano .env   # add your API keys"
echo ""
echo "  3. Start a tmux session and run:"
echo "     tmux new -s research"
echo "     source venv/bin/activate"
echo "     python3 research_agent.py"
echo "     # Press Ctrl+B then D to detach"
echo ""
echo "  4. To reattach later:"
echo "     tmux attach -t research"
echo ""
echo "  5. To watch the log live:"
echo "     tail -f outputs/process_log.txt"
echo "======================================"
