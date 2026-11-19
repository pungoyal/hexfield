#!/usr/bin/env bash
# Runs on the server, from CI:  ssh <server> bash -s -- /opt/hexfield < deploy/deploy.sh
#
# CI has already written <dir>/.env.next and <dir>/compose.yaml.next and pulled the image.
# This swaps them in, restarts the stack and waits for its health check; if the release
# never turns healthy, the previous one is started again and the deploy fails.
#
# The server is shared with other apps, so this touches nothing but:
#   - its own directory, a single folder directly under /opt;
#   - the `hexfield` Compose project (its one container and its `hexfield_data` volume);
#     its service is called `hexfield` too, so that is the only name it adds to the network;
#   - images of ghcr.io/vaari-dev/hexfield.
# It joins the edge's network but never creates, changes or removes it. It stores no
# registry login (CI pulls through this server's Docker with its own) and runs no
# system-wide prune. It refuses to run if another project or container already uses its
# names, since Caddy finds the app by its name on the shared network.
set -euo pipefail

dir=${1:?usage: deploy.sh /opt/<app>}
project=hexfield
name=hexfield                       # the service's only name on the shared network
repo=ghcr.io/vaari-dev/hexfield

fail() { echo "::error::$*" >&2; exit 1; }

[[ $dir =~ ^/opt/[a-z0-9][a-z0-9._-]*$ ]] || fail "deploy directory must be one folder directly under /opt, not $dir"
cd "$dir"
[ -f .env.next ] && [ -f compose.yaml.next ] || fail "$dir/.env.next or compose.yaml.next is missing"

setting() { sed -n "s/^$1='\(.*\)'\$/\1/p" "$2"; }
tag=$(setting IMAGE_TAG .env.next)
network=$(setting DOCKER_NETWORK .env.next)
prev_tag=$( [ -f .env ] && setting IMAGE_TAG .env || true)
docker image inspect "$repo:$tag" > /dev/null 2>&1 || fail "$repo:$tag has not been pulled"

# ---------------------------------------------------------------- refuse to step on others
other=$(docker ps -a --filter "label=com.docker.compose.project=$project" \
          --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u | grep -vxF "$dir" || true)
[ -z "$other" ] || fail "a Compose project named $project already runs from $other"

docker network inspect "$network" > /dev/null 2>&1 ||
  fail "network $network does not exist: deploy the edge first (this deploy never creates it)"
for id in $(docker network inspect "$network" --format '{{range $id, $c := .Containers}}{{$id}} {{end}}'); do
  info=$(docker inspect "$id" --format \
    "{{index .Config.Labels \"com.docker.compose.project\"}}|{{.Name}}|{{with index .NetworkSettings.Networks \"$network\"}}{{join .Aliases \" \"}}{{end}}")
  owner=${info%%|*}; rest=${info#*|}; cname=${rest%%|*}; names=" ${rest#*|} ${cname#/} "
  if [ "$owner" != "$project" ] && [[ $names == *" $name "* ]]; then
    fail "container ${cname#/} (project '${owner:-none}') already answers to '$name' on $network"
  fi
done

# ---------------------------------------------------------------- swap in and start
compose() { docker compose -p "$project" "$@"; }
for f in compose.yaml .env; do
  [ ! -e "$f" ] || cp -p "$f" "$f.prev"
  mv "$f.next" "$f"
done
if ! compose up -d --wait --wait-timeout 120 --remove-orphans; then
  compose logs --tail 50
  if [ -e .env.prev ]; then
    echo "::error::The new release never became healthy; starting the previous one ($prev_tag) again."
    mv compose.yaml.prev compose.yaml
    mv .env.prev .env
    compose up -d --wait --wait-timeout 120 --remove-orphans
  fi
  exit 1
fi

# ---------------------------------------------------------------- tidy its own images
# Keeps the running release and the one before it (for a manual rollback).
docker images "$repo" --format '{{.Tag}}' | grep -vxF -e "$tag" -e "${prev_tag:-$tag}" -e '<none>' |
  while read -r old; do docker rmi "$repo:$old" > /dev/null 2>&1 || true; done
echo "Running $repo:$tag"
