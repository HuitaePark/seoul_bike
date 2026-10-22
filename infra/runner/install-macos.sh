#!/usr/bin/env bash
# 배포 서버(macOS Apple Silicon)에 GitHub Actions self-hosted runner를 설치하고 launchd 서비스로 등록한다.
#
# 사용: bash infra/runner/install-macos.sh <등록 토큰>
#   등록 토큰(1시간 유효)은 둘 중 하나로 받는다.
#   - GitHub 저장소 Settings → Actions → Runners → New self-hosted runner 화면의 --token 값
#   - gh api -X POST repos/skala-ai-mini-project-1/skala_seoul_bike/actions/runners/registration-token -q .token
set -euo pipefail

TOKEN="${1:?등록 토큰을 첫 번째 인자로 넘긴다}"
REPO_URL="https://github.com/skala-ai-mini-project-1/skala_seoul_bike"
VERSION="${RUNNER_VERSION:-2.338.0}"
DIR="${RUNNER_DIR:-$HOME/actions-runner-seoul-bike}"
NAME="${RUNNER_NAME:-$(scutil --get LocalHostName)}"

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "macOS Apple Silicon 전용 스크립트다" >&2
  exit 1
fi
if ! command -v docker >/dev/null; then
  echo "docker가 없다. 먼저 설치한다: brew install colima docker docker-compose && brew services start colima" >&2
  exit 1
fi

mkdir -p "$DIR" "$HOME/seoul-bike"
cd "$DIR"

if [[ ! -x ./config.sh ]]; then
  curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${VERSION}/actions-runner-osx-arm64-${VERSION}.tar.gz"
  tar xzf runner.tar.gz
  rm runner.tar.gz
fi

# 기본 라벨(self-hosted, macOS, ARM64)에 seoul-bike를 더한다. deploy.yml은 이 라벨로만 서버를 고른다.
./config.sh --unattended --replace --url "$REPO_URL" --token "$TOKEN" \
  --name "$NAME" --labels seoul-bike --work _work

# launchd로 뜬 러너는 로그인 셸의 PATH를 모른다. docker와 credential helper 경로를 고정한다.
echo "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" > .path

./svc.sh install
./svc.sh start
./svc.sh status

cat <<'EOF'

남은 서버 설정 (infra/README.md 참고):
  1) ~/seoul-bike/.env 만들기 (infra/.env.example 참고) 후 chmod 600 ~/seoul-bike/.env
  2) Colima가 로그인 시 자동 실행되는지 확인: brew services list | grep colima
  3) 잠자기 방지: caffeinate LaunchAgent 등록 또는 sudo pmset -c sleep 0
EOF
