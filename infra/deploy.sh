#!/usr/bin/env bash
# 무중단 배포. deploy.yml이 부르고, 로컬에서도 같은 순서로 돌려 볼 수 있다:
#   COMPOSE_FILE=infra/compose.yaml COMPOSE_PROJECT_NAME=seoul-bike bash infra/deploy.sh <env 파일>
# 이미지는 미리 빌드해 둔다 (deploy.yml "이미지 받기·빌드"). 서버의 /bin/bash(3.2)에서도 돌게 쓴다.
#
# 화면·API를 받는 backend·frontend는 순차 교체한다: 새 컨테이너를 하나 더 띄워 healthy가 되면 옛 컨테이너를 내린다.
# 그동안 서버 포트(3000·8080)는 edge가 잡고 있어 공개 주소가 끊기지 않는다. 새 컨테이너가 healthy가 되지 않으면
# 새 것만 지우고 실패한다 (옛 컨테이너가 이전 버전으로 계속 돈다).
# 나머지(작업자·MCP)는 바뀐 것만 다시 만든다 (compose 기본 동작). 작업자는 잠깐 멈췄다가 Kafka에 남은 일을 이어서 처리한다.
#
# 주의: backend의 Flyway 마이그레이션은 새 컨테이너가 시작할 때 돌고, 그동안 옛 컨테이너도 같은 DB로 돈다.
# 마이그레이션은 옛 코드가 계속 돌 수 있게 넓히는 변경(표·열·인덱스 추가)만 한다. 지우거나 이름을 바꾸는 것은 다음 배포로 나눈다.
set -euo pipefail

env_file=${1:?사용법: bash infra/deploy.sh <env 파일>}
project=${COMPOSE_PROJECT_NAME:?COMPOSE_PROJECT_NAME이 필요하다}
# Configure every Compose invocation before any container change. Bad/missing
# road data must fail before replacing a running backend.
checkout=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
. "$checkout/infra/optimizer/deploy-settings.sh"
configure_optimizer_deployment "$checkout" "$env_file"
# 옛 컨테이너를 내릴 때 기다리는 최대 시간 (roll의 두 번째 인자).
# backend: Spring 그레이스풀 종료(75초, 경로 계산이 60초까지 기다린다) + 여유. compose의 backend stop_grace_period와 같다.
# 보통은 진행 중 요청이 없어 1~2초에 끝난다.
# frontend: nginx는 열린 연결(SSE)이 끝날 때까지 기다리며 그동안 새 연결을 받지 않는다. 화면은 끊긴 SSE를 다시 잇고,
# 새 요청은 edge가 새 컨테이너로 보내므로 짧게 둔다.
backend_stop=90
frontend_stop=10
# nginx(edge·frontend)가 Docker DNS 답을 캐시하는 시간(valid=2s)보다 길게 기다려 새 컨테이너가 보이게 한 뒤 옛 것을 내린다.
dns_settle=5
health_timeout=300

dc() {
  docker compose --env-file "$env_file" "$@"
}

# 서비스의 실행 중인 컨테이너 ID. `docker compose run`으로 띄운 일회용 컨테이너는 뺀다.
containers() {
  docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$1" \
    --filter "label=com.docker.compose.oneoff=False"
}

# 지금 설정·이미지 그대로 만든 컨테이너인가 (compose가 다시 만들지 않는 기준과 같다: 설정 해시와 이미지 ID)
unchanged() {
  local svc=$1 c=$2 want have img
  want=$(dc config --hash "$svc" | awk '{print $2}')
  have=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$c")
  if [ -z "$want" ] || [ "$want" != "$have" ]; then
    return 1
  fi
  img=$(docker inspect -f '{{.Config.Image}}' "$c")
  [ "$(docker image inspect -f '{{.Id}}' "$img" 2>/dev/null)" = "$(docker inspect -f '{{.Image}}' "$c")" ]
}

state() {
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1"
}

roll() {
  local svc=$1 stop_timeout=$2 old new count status waited=0 stopped
  # 지난 실패로 멈춰 남은 컨테이너는 정리한다 (교체 대상 개수를 틀리게 세지 않게)
  stopped=$(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.oneoff=False" \
    --filter "label=com.docker.compose.service=$svc" --filter status=exited --filter status=created --filter status=dead)
  if [ -n "$stopped" ]; then
    docker rm $stopped >/dev/null
  fi
  old=$(containers "$svc")
  if [ -z "$old" ]; then
    echo "$svc: 떠 있는 컨테이너가 없어 새로 띄운다"
    dc up -d --no-deps --wait --wait-timeout "$health_timeout" "$svc"
    return
  fi
  count=$(printf '%s\n' "$old" | wc -l | tr -d ' ')
  # 바뀐 게 없으면 교체하지 않는다 (agent만 바뀐 배포에서 화면 SSE를 끊지 않게)
  if [ "$count" = 1 ] && unchanged "$svc" "$old"; then
    echo "$svc: 바뀐 게 없어 그대로 둔다"
    return
  fi
  echo "$svc: 새 컨테이너를 하나 더 띄운다 (지금 ${count}개)"
  # --no-recreate: 옛 컨테이너는 그대로 두고 지금 설정·이미지로 하나만 더 만든다
  dc up -d --no-deps --no-recreate --scale "$svc=$((count + 1))" "$svc"
  new=$(containers "$svc" | grep -vxF "$old" || true)
  if [ -z "$new" ]; then
    echo "$svc: 새 컨테이너가 만들어지지 않았다" >&2
    return 1
  fi
  while :; do
    status=$(state "$new")
    if [ "$status" = healthy ]; then
      break
    fi
    case $status in
      unhealthy | exited | dead) ;;
      *)
        if [ "$waited" -lt "$health_timeout" ]; then
          sleep 5
          waited=$((waited + 5))
          continue
        fi
        ;;
    esac
    echo "$svc: 새 컨테이너가 ${waited}초 동안 healthy가 되지 않았다 ($status). 새 것만 지우고 옛 컨테이너를 그대로 둔다" >&2
    docker logs --tail 100 "$new" >&2 || true
    docker rm -f "$new" >/dev/null
    return 1
  done
  echo "$svc: 새 컨테이너 healthy (${waited}초). ${dns_settle}초 뒤 옛 컨테이너를 내린다"
  sleep "$dns_settle"
  docker stop -t "$stop_timeout" $old >/dev/null
  docker rm $old >/dev/null
  echo "$svc: 교체 끝"
}

# 1) 상태를 가진 기반 서비스. 이미지를 고정해 두어 바뀐 게 없으면 그대로 둔다.
dc up -d --no-deps --wait --wait-timeout "$health_timeout" postgres redpanda vectordb

# OSRM must be healthy before backend replacement and worker startup.
if [ "$OSRM_ENABLED" = true ]; then
  dc up -d --no-deps --wait --wait-timeout "$health_timeout" osrm
fi

# 2) backend 순차 교체 (마이그레이션은 새 컨테이너가 시작할 때)
roll backend "$backend_stop"

# 3) 나머지는 바뀐 것만 다시 만든다. 서비스가 늘어도 여기에 들어간다 (OSRM overlay의 osrm 등).
rest=$(dc config --services | grep -vxE 'edge|frontend|backend|postgres|redpanda|vectordb' | tr '\n' ' ')
# shellcheck disable=SC2086 # 서비스 이름 목록을 단어로 나눈다
dc up -d --no-deps --wait --wait-timeout "$health_timeout" --remove-orphans $rest

# 4) frontend 순차 교체. edge가 아직 없으면(이 구성으로 처음 배포) 옛 frontend가 공개 포트를 잡고 있으므로,
#    새 frontend가 healthy가 되면 옛 것을 바로 내리고 5)에서 edge가 포트를 받는다 (이때 한 번 몇 초 끊긴다).
if [ -n "$(containers edge)" ]; then
  roll frontend "$frontend_stop"
else
  roll frontend 0
fi

# 5) edge는 설정이 바뀔 때만 다시 만든다 (그때만 잠깐 끊긴다).
#    처음 한 번은 예전 frontend·backend가 잡던 서버 포트가 위에서 풀린 뒤 여기서 뜬다.
dc up -d --no-deps --wait --wait-timeout 60 edge
dc ps --format 'table {{.Name}}\t{{.Status}}'
