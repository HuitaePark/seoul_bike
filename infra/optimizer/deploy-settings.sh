#!/usr/bin/env bash
# Source this file, then call configure_optimizer_deployment <checkout> <env file>.
# Uses Compose's dotenv parser; never sources or evaluates the env file itself.
configure_optimizer_deployment() {
  local checkout=$1 env_file=$2 resolved line enabled=false data_dir= input_dir= version
  checkout=$(cd "$checkout" && pwd -P) || return 1
  # Read only the three optimizer settings from Compose's resolved environment.
  # The full environment can contain secrets: never print `resolved`.
  resolved=$(docker compose --env-file "$env_file" -f "$checkout/infra/compose.yaml" config --environment) || return 1
  while IFS= read -r line; do
    case "$line" in
      OSRM_ENABLED=*) enabled=${line#*=} ;;
      OSRM_DATA_DIR=*) data_dir=${line#*=} ;;
      OSRM_INPUT_DIR=*) input_dir=${line#*=} ;;
    esac
  done <<< "$resolved"
  export COMPOSE_FILE="$checkout/infra/compose.yaml"
  case "$enabled" in
    false|'')
      export OSRM_ENABLED=false OSRM_DATA_DIR= OSRM_INPUT_DIR=
      echo 'optimizer: haversine'
      return 0
      ;;
    true) ;;
    *) echo 'OSRM_ENABLED must be true or false' >&2; return 1 ;;
  esac
  # COMPOSE_FILE uses ':' as its separator. Reject line breaks before writing
  # resolved paths to GITHUB_ENV; also check physical paths to catch symlinks.
  case "$checkout" in
    *:*|*$'\n'*|*$'\r'*) echo 'checkout path cannot contain colons or line breaks' >&2; return 1 ;;
  esac
  data_dir=$(optimizer_external_directory OSRM_DATA_DIR "$data_dir" "$checkout") || return 1
  if [ -n "$input_dir" ]; then
    input_dir=$(optimizer_external_directory OSRM_INPUT_DIR "$input_dir" "$checkout") || return 1
  fi
  if [ ! -r "$data_dir/data-version" ] || [ ! -s "$data_dir/data-version" ] || [ ! -r "$data_dir/region.osrm.hsgr" ] || [ ! -s "$data_dir/region.osrm.hsgr" ]; then
    echo 'OSRM_DATA_DIR needs readable data-version and prepared CH region.osrm.hsgr' >&2
    return 1
  fi
  version=$(cat "$data_dir/data-version") || return 1
  if ! [[ "$version" =~ ^[a-f0-9]{64}$ ]]; then
    echo 'data-version must be the SHA-256 written by prepare-osrm.sh' >&2
    return 1
  fi
  export OSRM_ENABLED=true OSRM_DATA_DIR="$data_dir" OSRM_INPUT_DIR="$input_dir"
  export COMPOSE_FILE="$COMPOSE_FILE:$checkout/infra/optimizer/compose.osrm.yaml"
  echo 'optimizer: osrm (prepared CH data)'
}

optimizer_external_directory() {
  local name=$1 path=$2 checkout=$3 physical
  case "$path" in
    *$'\n'*|*$'\r'*) echo "$name cannot contain line breaks" >&2; return 1 ;;
  esac
  case "$path" in
    /*) ;;
    *) echo "$name must be an absolute directory outside the checkout" >&2; return 1 ;;
  esac
  physical=$(cd "$path" 2>/dev/null && pwd -P) || {
    echo "$name directory does not exist" >&2; return 1;
  }
  case "$physical/" in
    "$checkout/"*) echo "$name must be outside the checkout (including symlinks)" >&2; return 1 ;;
  esac
  case "$physical" in
    *$'\n'*|*$'\r'*) echo "$name cannot contain line breaks" >&2; return 1 ;;
  esac
  printf '%s\n' "$physical"
}
