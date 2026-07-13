#!/usr/bin/env bash
# Remove cursoragent from GitHub Contributors sidebar.
# Requires: git, ssh access to GitHub, and authenticated GitHub CLI (gh auth login).
set -euo pipefail

REPO="${1:-joruf/agent-forge}"
OWNER="${REPO%%/*}"
NAME="${REPO##*/}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
GH="${GH:-$(command -v gh 2>/dev/null || true)}"
if [[ -z "$GH" && -x "$HOME/.local/bin/gh" ]]; then
  GH="$HOME/.local/bin/gh"
fi

cd "$WORKDIR"

if [[ -z "$GH" ]] || [[ ! -x "$GH" ]]; then
  echo "GitHub CLI (gh) fehlt. Installieren:"
  echo "  mkdir -p ~/.local/bin"
  echo "  curl -sL https://github.com/cli/cli/releases/download/v2.63.2/gh_2.63.2_linux_amd64.tar.gz | tar -xz -C /tmp"
  echo "  cp /tmp/gh_2.63.2_linux_amd64/bin/gh ~/.local/bin/gh"
  exit 1
fi

if ! "$GH" auth status >/dev/null 2>&1; then
  echo "Bitte zuerst bei GitHub anmelden:"
  echo "  gh auth login"
  exit 1
fi

echo "==> 1/5 Ein sauberer Commit ohne Co-Author (Cursor-Hook umgehen)"
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-joruf}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-rufjoachim@loresoft.de}"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

git checkout --orphan contributor-fix
git add -A
TREE="$(git write-tree)"
COMMIT="$(git commit-tree "$TREE" -F - <<'EOF'
AgentForge v0.1.0

Multi-agent AI desktop platform for Linux with Ollama and optional cloud LLMs.
EOF
)"
git reset --hard "$COMMIT"

if git log -1 --format='%B' | grep -qi 'co-authored-by:.*cursor'; then
  echo "FEHLER: Co-Author Cursor noch in Commit-Nachricht."
  exit 1
fi

echo "==> 2/5 Temporären Branch pushen (main-clean)"
git push -f origin HEAD:main-clean

echo "==> 3/5 Default-Branch auf main-clean setzen"
"$GH" api "repos/$REPO" -X PATCH -f default_branch=main-clean

echo "==> 4/5 Alten main löschen und main-clean -> main"
git push origin :main || true
git push -f origin HEAD:main
"$GH" api "repos/$REPO" -X PATCH -f default_branch=main

echo "==> 5/5 Temporären Branch entfernen"
git push origin :main-clean || true

git branch -M main
git fetch origin
git reset --hard origin/main

echo ""
echo "Fertig. Bitte GitHub-Seite hart neu laden (Strg+Shift+R)."
echo "Contributors-API prüfen:"
curl -s "https://api.github.com/repos/$REPO/contributors" | python3 -c "import sys,json; print([x['login'] for x in json.load(sys.stdin)])"
