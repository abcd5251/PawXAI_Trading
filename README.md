# PawXAI_Trading



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

# Execution
```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```
