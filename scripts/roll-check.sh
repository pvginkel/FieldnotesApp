#!/bin/bash
# The prd roll check (FN-63): docs/slice-test-plan.md, section 3, says when to run it and how to
# read what it prints and leaves in tmp/roll/. Start it in the background before the push that
# rolls fieldnotes-prd, under `timeout 1900`: it ends on its own once the pod that ran before the
# push is gone, or after 30 minutes. It only reads prd: the pod listing and the three hostnames'
# readiness answers.
#
# It runs from the repo root whatever the caller's cwd, and kills its probes however it exits, so a
# `timeout` leaves none behind.

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
trap 'jobs -p | xargs -r kill 2> /dev/null' EXIT
trap 'exit 143' TERM INT

SECONDS=0
mkdir -p tmp/roll && rm -f tmp/roll/*
cexec iac kubectl -n fieldnotes-prd get pods -l app=fieldnotes -o name | cut -d/ -f2 > tmp/roll/old
[ "$(wc -l < tmp/roll/old)" -eq 1 ] || { echo "tmp/roll/old names $(wc -l < tmp/roll/old) pods, not one: no check"; exit 1; }
probe() { while :; do echo "$(date -u +%T.%3N) $(curl -s -o /dev/null -m 5 -w '%{http_code}' "$1") $1"; sleep 0.5; done >> tmp/roll/probes; }
probe https://fieldnotes-api.home/health/readyz & probe https://fieldnotes-mcp.home/readyz & probe https://fieldnotes/ &
until [ $SECONDS -ge 1800 ] || { cexec iac kubectl -n fieldnotes-prd get pods -l app=fieldnotes --no-headers --request-timeout=10s > tmp/roll/now &&
    sed "s/^/$(date -u +%T) /" tmp/roll/now >> tmp/roll/pods && ! grep -qwFf tmp/roll/old tmp/roll/now; }; do sleep 5; done
jobs -p | xargs -r kill
grep -vwFf tmp/roll/old tmp/roll/now | cut -d' ' -f1 > tmp/roll/new
grep -qwFf tmp/roll/old tmp/roll/now && echo "the old pod outlived the check"
awk '{ print $3, $2 }' tmp/roll/probes | sort | uniq -c
