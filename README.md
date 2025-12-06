# PawXAI_Trading
An automated Telegram trading bot that watches the latest tweets from specified Twitter accounts and executes trades based on predefined signals. When a tweet meets a sentiment of the specific requirement. The bot can place spot trades through Jupiter or open/close perpetual positions on Lighter.

# Specific Python version
Change path
```bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"
```
Check Python version
```bash
which python3                   
python3 --version       
```

# Install dependencies
```bash
pip install -r requirements.txt
```

# Environment Setup

Ensure that the following scripts are located in the project root folder:

```buy_spot.py```

```lighter_trade.py```

```run_all.sh```

# Search for account index
```bash
curl "https://mainnet.zklighter.elliot.ai/api/v1/accountsByL1Address?l1_address=Your_L1_Address"
```

# For Discord Server
Move files in `discord` folder into root folder
```bash
python discord_server.py
```

