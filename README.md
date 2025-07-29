# changegen
Wrapper for the OpenAI to gen nice changleg based on code changes, not commit messages. Helpful if your pet-project commit messages are not meaningful.

# usage
```
pip install openai gitpython tqdm
export OPENAI_API_KEY=...   # your key

# All commits to first commit:
python3 generate_changelog.py --repo https://github.com/shaman007/home-k3s --branch main

# Optional filters / tweaks:
python3 generate_changelog.py --since 2024-01-01
python3 generate_changelog.py --model gpt-4o-mini
python3 generate_changelog.py --per-commit-budget 6000
python3 generate_changelog.py --include-merges
```
